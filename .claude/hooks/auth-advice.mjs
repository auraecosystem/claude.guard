#!/usr/bin/env node
/**
 * PreToolUse advisory: when the agent runs a command that needs Claude or GitHub
 * authentication, but none is present in this sandbox session, attach a one-time
 * note pointing at the host command that fixes it — `claude-guard setup-token`
 * for Claude, the GitHub App for GitHub. The command would fail to authenticate
 * anyway; surfacing the fix up front turns a cryptic auth error into an action.
 *
 * Non-blocking by design (additionalContext, never deny): a read-only `gh`/`git`
 * op against a public repo can succeed unauthenticated, so blocking would be wrong
 * — this is friction, not a wall (the sandbox is the security floor). It also fails
 * OPEN: any internal error lets the command through untouched. The note is emitted
 * at most once per auth kind per session (a /tmp sentinel), so a session full of
 * `gh` calls doesn't re-narrate the same advice on every one.
 */
import { execFileSync } from "node:child_process";
import { existsSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  isMain,
  readStdinJson,
  emitHookResponse,
  HookEvent,
} from "./lib-hook-io.mjs";

// gh subcommands/flags that work WITHOUT authentication — a bare `gh`, the login
// flow itself, version/help, and local config. Anything else is auth-requiring.
export const GH_NO_AUTH = new Set([
  "auth",
  "version",
  "--version",
  "help",
  "--help",
  "-h",
  "config",
  "completion",
  "extension",
  "alias",
]);

// claude subcommands/flags that don't reach the inference backend: version/help,
// the token-capture flow we'd be advertising, connector + installer management.
export const CLAUDE_NO_AUTH = new Set([
  "version",
  "--version",
  "-v",
  "help",
  "--help",
  "-h",
  "setup-token",
  "mcp",
  "config",
  "update",
  "doctor",
  "install",
  "migrate-installer",
]);

// git subcommands that open a connection to a remote (and so hit GitHub's auth).
export const GIT_REMOTE_OPS = new Set([
  "push",
  "fetch",
  "pull",
  "clone",
  "ls-remote",
]);

/**
 * Split a shell command into the segments a connector could hide behind
 * (`a && gh ... ; claude ...`). Sequencers/pipes only — enough for the
 * matcher-gated common forms; deeper quoting is a documented speed-bump gap.
 * @param {string} command
 * @returns {string[]}
 */
export function splitSegments(command) {
  return command
    .split(/&&|\|\||[|;\n]/)
    .map((seg) => seg.trim())
    .filter(Boolean);
}

/**
 * The first real program of a segment plus its args, after stripping leading
 * `VAR=value` assignments (which don't change which tool authenticates). Returns
 * null when the segment is only assignments or whitespace. The program is its
 * basename so an absolute `/usr/bin/gh` still reads as `gh`.
 * @param {string} segment
 * @returns {{ program: string, args: string[] } | null}
 */
export function leadingProgram(segment) {
  const tokens = segment.split(/\s+/).filter(Boolean);
  let i = 0;
  while (i < tokens.length && /^\w+=/.test(tokens[i])) i++;
  if (i >= tokens.length) return null;
  const token = tokens[i];
  // Basename without a regex (the linter flags `/.*\//` as ReDoS-prone): an
  // absolute `/usr/bin/gh` still reads as `gh`; lastIndexOf -1 keeps the whole word.
  return {
    program: token.slice(token.lastIndexOf("/") + 1),
    args: tokens.slice(i + 1),
  };
}

/**
 * True when a `claude` invocation reaches the inference backend (so it needs a
 * login). A bare `claude` opens the interactive REPL, which also requires auth.
 * @param {string[]} args
 */
export function claudeNeedsAuth(args) {
  const first = args[0];
  return first === undefined ? true : !CLAUDE_NO_AUTH.has(first);
}

/**
 * True when a `gh` invocation talks to the GitHub API (so it needs a credential).
 * A bare `gh` only prints help.
 * @param {string[]} args
 */
export function ghNeedsAuth(args) {
  const first = args[0];
  return first === undefined ? false : !GH_NO_AUTH.has(first);
}

/**
 * True when a `git` invocation is a remote operation against github.com — the
 * only host the sandbox's credential helper serves (and only from GH_TOKEN). An
 * explicit github URL in the args decides it directly; otherwise an explicit
 * non-github URL rules it out, and a bare remote name is resolved via
 * `resolveRemoteUrl` (default `origin`). `clone` has no remote to resolve, so it
 * only triggers on an explicit github URL.
 * @param {string[]} args
 * @param {(remote: string) => string} resolveRemoteUrl
 */
export function gitTargetsGithub(args, resolveRemoteUrl) {
  const sub = args[0];
  if (!GIT_REMOTE_OPS.has(sub)) return false;
  const rest = args.slice(1).filter((arg) => !arg.startsWith("-"));
  if (rest.some((arg) => /github\.com/i.test(arg))) return true;
  // An explicit URL/scp-style arg that wasn't github means a non-github remote.
  if (rest.some((arg) => arg.includes("://") || /^[^/@]+@[^/:]+:/.test(arg)))
    return false;
  if (sub === "clone") return false;
  return /github\.com/i.test(resolveRemoteUrl(rest[0] || "origin") || "");
}

/**
 * True when a usable Claude login is present. The interactive path authenticates
 * from a seeded credentials file; headless from an env token; an API key works
 * for either. The agent's scrubbed `bash -c` strips the env token, so the file is
 * the load-bearing signal for a nested `claude` call.
 * @param {Record<string, string | undefined>} env
 * @param {() => boolean} credentialsFileExists
 */
export function claudeAuthPresent(env, credentialsFileExists) {
  if (env.CLAUDE_CODE_OAUTH_TOKEN?.trim()) return true;
  if (env.ANTHROPIC_API_KEY?.trim()) return true;
  return credentialsFileExists();
}

/**
 * True when a GitHub credential is present. The sandbox blanks the host's own
 * token and forwards only the App-minted GH_TOKEN; gh also honors GITHUB_TOKEN.
 * @param {Record<string, string | undefined>} env
 */
export function githubAuthPresent(env) {
  return Boolean(env.GH_TOKEN?.trim() || env.GITHUB_TOKEN?.trim());
}

/**
 * The auth kind a command needs but lacks (`"claude"` / `"github"`), or null when
 * every segment is either auth-free or already authenticated. Returns the first
 * unmet need so the advice names a concrete fix.
 * @param {{
 *   command: string,
 *   env: Record<string, string | undefined>,
 *   credentialsFileExists: () => boolean,
 *   resolveRemoteUrl: (remote: string) => string,
 * }} deps
 * @returns {"claude" | "github" | null}
 */
export function detectUnmetAuth({
  command,
  env,
  credentialsFileExists,
  resolveRemoteUrl,
}) {
  for (const segment of splitSegments(command)) {
    const lp = leadingProgram(segment);
    if (!lp) continue;
    const { program, args } = lp;
    if (program === "claude" && claudeNeedsAuth(args))
      return claudeAuthPresent(env, credentialsFileExists) ? null : "claude";
    if (program === "gh" && ghNeedsAuth(args))
      return githubAuthPresent(env) ? null : "github";
    if (program === "git" && gitTargetsGithub(args, resolveRemoteUrl))
      return githubAuthPresent(env) ? null : "github";
  }
  return null;
}

const ADVICE = Object.freeze({
  claude:
    "claude-guard: this runs `claude`, but no saved Claude login is available in " +
    "this sandbox session, so it will stop at the login screen. To reuse a login " +
    "across sessions, run `claude-guard setup-token` on the host — it captures and " +
    "stores a Claude login that is injected into each session. One-time heads-up " +
    "for this session.",
  github:
    "claude-guard: this needs GitHub access, but no GitHub credential is available " +
    "in this sandbox session (your personal token is withheld by design), so it " +
    "will fail to authenticate. To grant scoped, short-lived GitHub access, set up " +
    "the GitHub App on the host: `claude-guard gh-app create` then `claude-guard " +
    "gh-app install`. A repo-scoped token is then minted into each session. " +
    "One-time heads-up for this session.",
});

/**
 * The advice note for an auth kind.
 * @param {"claude" | "github"} kind
 */
export function adviceContext(kind) {
  return ADVICE[kind];
}

/**
 * Path of the once-per-session sentinel for an auth kind. The sandbox's /tmp is
 * fresh per ephemeral container, so a fixed name is already per-session; `dir` is
 * injectable for tests.
 * @param {"claude" | "github"} kind
 * @param {string} dir
 */
export function adviceSentinel(kind, dir) {
  return join(dir, `.claude-guard-auth-advice-${kind}`);
}

// Stryker disable all: subprocess-only CLI wiring (stdin → detectUnmetAuth →
// hook response + sentinel). In-process tests drive the exported helpers above;
// these mutants are unobservable here.
/* c8 ignore start -- CLI-entry block (same subprocess boundary as the other hooks) */
if (isMain(import.meta.url)) {
  try {
    const input = await readStdinJson();
    const command = input?.tool_input?.command;
    if (input?.tool_name !== "Bash" || typeof command !== "string")
      process.exit(0);

    const env = process.env;
    const cfgDir = env.CLAUDE_CONFIG_DIR || join(env.HOME || "", ".claude");
    const credsFile = join(cfgDir, ".credentials.json");
    const kind = detectUnmetAuth({
      command,
      env,
      credentialsFileExists: () =>
        existsSync(credsFile) && statSync(credsFile).size > 0,
      resolveRemoteUrl: (remote) => {
        try {
          return execFileSync("git", ["remote", "get-url", remote], {
            encoding: "utf8",
          }).trim();
        } catch {
          return "";
        }
      },
    });
    if (!kind) process.exit(0);

    const sentinel = adviceSentinel(kind, process.env.TMPDIR || "/tmp");
    if (existsSync(sentinel)) process.exit(0);
    try {
      writeFileSync(sentinel, "");
    } catch {
      // A read-only /tmp just means the note may repeat — never a reason to fail.
    }
    emitHookResponse(HookEvent.PRE_TOOL_USE, {
      additionalContext: adviceContext(kind),
    });
  } catch {
    process.exit(0); // Advisory only: never block the agent on a hook fault.
  }
}
/* c8 ignore stop */

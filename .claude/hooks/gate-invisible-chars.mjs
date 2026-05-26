#!/usr/bin/env node
/**
 * PreToolUse: prompt user for approval if invisible-character payloads
 * were found in instruction files. Uses permissionDecision: "ask" so
 * Claude Code shows a permission dialog—works in the VM where the user
 * can't access the filesystem directly.
 *
 * Once the user approves, the alert file is deleted so subsequent tool
 * calls proceed without re-prompting.
 */
import { readFileSync, existsSync, unlinkSync } from "node:fs";
import { join } from "node:path";

const PROJECT_DIR = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const ALERT_FILE = join(PROJECT_DIR, ".claude", ".invisible-char-alert");

if (!existsSync(ALERT_FILE)) process.exit(0);

const findings = readFileSync(ALERT_FILE, "utf-8").trim();

// Remove the alert file so the user is only prompted once.
// If they approve, subsequent tool calls proceed normally.
// If they deny, this single tool call fails but the warning was shown.
try {
  unlinkSync(ALERT_FILE);
} catch {
  // Root-owned in devcontainer; gate still fires this once.
}

process.stdout.write(
  JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "ask",
      permissionDecisionReason:
        "Invisible character injection detected in instruction files.\n\n" +
        findings +
        "\n\nApprove to continue with these files, or deny to abort.",
    },
  }),
);

# secure-claude-code-defaults

Run Claude Code inside a hardware-isolated VM with an AI monitor watching every tool call. Type `claude` and it just works.

## What happens when you type `claude`

The `bin/claude` wrapper handles the full lifecycle:

1. Creates a **git worktree** so Claude works on an isolated copy of your repo
2. Starts a **Kata/Firecracker microVM** inside a devcontainer (<125ms boot)
3. Applies a **network firewall**—the VM has no internet route; all traffic goes through an allowlisted proxy
4. Launches Claude Code with `--dangerously-skip-permissions`—safe because the VM + network + monitor contain the blast
5. A **trusted monitor** (cheap model in a separate container) reviews every tool call: allow, deny, or halt-and-notify-human

When you’re done, the worktree merges or cleans up.

## The monitor

Implements [AI control](https://arxiv.org/abs/2312.06942). A second model reviews every tool call before execution. Three tiers:

- **allow**—normal operations (reading files, running tests)
- **deny**—block and continue (bulk deletion, force push)
- **ask**—halt the agent, push-notify you via [ntfy.sh](https://ntfy.sh) (exfiltration patterns, monitor tampering, obfuscated commands)

Fails closed. Logs everything to `~/.cache/claude-monitor/monitor.jsonl`. The monitor runs in its own container with no network—the monitored model cannot read the policy or detection logic.

## VM isolation

The app runs inside a [Firecracker microVM](https://firecracker-microvm.github.io/) via [Kata Containers](https://katacontainers.io/). An attacker must:

1. Escape the VM (hypervisor boundary—~1–2 CVEs/decade)
2. Escape the internal Docker network (no NAT masquerade, no internet route)
3. Bypass the proxy allowlist (squid with SSL bump, GET/HEAD-only for read-only domains)

As of May 2026, [frontier models](https://arxiv.org/abs/2603.02277) score 100% on container escape from misconfigurations but 0% on novel kernel exploits (i.e. through the hypervisor boundary).

## Venice routing (optional)

`bin/claude-private` routes through [Venice](https://venice.ai) for end-to-end encrypted inference with Opus fallback for heavy reasoning. `bin/claude-paranoid` stays on E2EE open-weights only—nothing touches Anthropic’s servers.

## Anti-prompt-injection hooks

Every tool call passes through sanitization hooks:

- **Input**: normalizes [confusable/homoglyph characters](https://arxiv.org/abs/2508.14070) so deny rules can’t be bypassed with Cyrillic lookalikes
- **Output**: strips invisible Unicode (format chars, variation selectors) and ANSI escapes that could carry [hidden payloads](https://arxiv.org/abs/2603.00164)

## Install

```bash
git clone https://github.com/alexander-turner/secure-claude-code-defaults.git \
    ~/.local/share/secure-claude-code-defaults
cd ~/.local/share/secure-claude-code-defaults
bash setup.bash
```

`setup.bash` symlinks configs into `~/.claude/`, installs Kata Containers (Linux), registers the runtime with Docker, and puts the `claude` wrapper on your PATH.

On macOS (no KVM), set `CONTAINER_RUNTIME=runc` in `.devcontainer/.env` to fall back to container isolation without the VM layer.

### Monitor provider

The monitor auto-detects from available API keys:

| Key                    | Provider      | Default model    |
| ---------------------- | ------------- | ---------------- |
| `ANTHROPIC_API_KEY`    | Anthropic     | claude-haiku-4-5 |
| `VENICE_INFERENCE_KEY` | Venice (E2EE) | qwen3-coder-480b |

### Without the devcontainer

Set `CLAUDE_NO_SANDBOX=1` to run on the host. You still get the monitor (via permission hooks), deny list, pre-push checks, and sanitization—but no VM boundary. Don’t use `--dangerously-skip-permissions` in this mode.

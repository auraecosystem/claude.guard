# Dotfiles: Restore Claude Code Preferences

Drop this file into your dotfiles repo as `.claude/commands/restore-preferences.md`
(or source the instructions into your `CLAUDE.md`) so you can run
`/restore-preferences` after `secure-claude-code-defaults` setup merges
security settings into your `~/.claude/settings.json`.

---

## Instructions for Claude

Read `~/.claude/settings.json` and merge my personal preferences into it
**without removing any security settings** (deny rules, hooks whose commands
reference `$SCCD_DIR`, env vars starting with `DISABLE_` or
`CLAUDE_CODE_DISABLE_`, sandbox config).

### Preferences to restore

Edit this section with your own values before using.

```jsonc
{
  // UI
  "theme": "dark-ansi",
  "editorMode": "vim",

  // Plugins — list yours here
  "enabledPlugins": {
    "code-simplifier@claude-plugins-official": true,
    "plugin-dev@claude-plugins-official": true,
    "pyright-lsp@claude-plugins-official": true,
  },

  // Extra allow rules (machine-specific)
  "permissions.allow+": [
    "Bash(ollama:*)",
    "Bash(brew upgrade:*)",
    "Bash(brew services:*)",
    "Bash(defaults read:*)",
    "Bash(orb status *)",
    "Bash(orb start *)",
    "Bash(launchctl getenv:*)",
    "Bash(launchctl bootout:*)",
    "Bash(launchctl bootstrap:*)",
    "Bash(launchctl load:*)",
    "Bash(launchctl list:*)",
    "Bash(log show:*)",
    "Bash(crontab -l)",
    "Bash(last reboot *)",
    "Bash(sysctl -n kern.boottime)",
    "Bash(pkill -f \"ollama serve\")",
    "Read(//Applications/**)",
    "Read(//Users/server/.ollama/**)",
    "Read(//etc/ollama/**)",
    "Read(//private/tmp/**)",
  ],
}
```

### Merge rules

1. For `permissions.allow+`: **append** these to the existing `permissions.allow`
   array (do not duplicate entries already present).
2. For top-level keys (`theme`, `editorMode`, `enabledPlugins`): **set or
   overwrite** the value.
3. **Never remove** any entry from `permissions.deny`.
4. **Never remove** any hook whose command contains `SCCD_DIR`.
5. **Never remove** any `env` var whose key starts with `DISABLE_` or
   `CLAUDE_CODE_DISABLE_`.
6. **Never modify** the `sandbox` block.
7. Write the merged result back to `~/.claude/settings.json` with 2-space
   indentation.

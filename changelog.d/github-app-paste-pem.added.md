- `claude-github-app create` now lets you **paste the private key's text** at the
  prompt instead of pointing it at a downloaded `.pem` file — paste the block
  starting with the `-----BEGIN` line and it captures through the matching
  `-----END … PRIVATE KEY-----`. This removes the file-transfer step when setting
  up the App on a remote/SSH host, where landing the browser-downloaded key on
  that host is the awkward part; pasting from your local clipboard just works.
  Giving a filesystem path still works exactly as before.

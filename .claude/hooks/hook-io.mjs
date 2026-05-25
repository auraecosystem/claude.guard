/**
 * Shared I/O for Claude Code hooks.
 *
 * Reads JSON from stdin (piped by the hook harness) and provides a
 * helper to write the JSON response to stdout.
 */

export async function readHookInput() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString();
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function writeHookOutput(output) {
  process.stdout.write(JSON.stringify(output));
}

# Teardown-latency analysis: the seed-branch review (PR #1215)

PR #1215 adds a **host-side review of the returned `claude/*` seed branch** at
session end (`worktree_review_seed_diff`, `bin/lib/worktree-seed.bash`; the module
is `.claude/hooks/monitorlib/seed_review.py`). It runs in the ephemeral teardown
path (`bin/claude-guard` `_ephemeral_cleanup`), **after** the agent's work is
extracted to the host branch and **before** `ephemeral_teardown` brings the docker
stack down. This note answers: how much latency does it add, and can it run in
parallel?

> **Measure-first.** Every figure below is measured except the LLM call, which the
> repo tracks no latency for (the only charted model number is promptarmor at
> ~185 ms — a far smaller classification). The LLM term is therefore a labelled
> estimate; the conclusion holds across its whole plausible range.

## TL;DR

- **Added latency, no monitor key:** ~**78 ms** — one `python3 -m
monitorlib.seed_review` subprocess (the LLM layer short-circuits to
  "unavailable"). Negligible against the ~506 ms docker teardown it sits next to.
- **Added latency, monitor key set:** dominated by **one strong-model LLM call**
  (default `claude-sonnet-4-6`, `max_tokens=800`, temp 0, `MONITOR_TIMEOUT=10 s`):
  ~**3.5 s typical, 2–10 s** across small-diff..near-token-cap / timeout. This is
  ~7× the entire teardown.
- **Can we parallelize?** Yes — the review reads only the (now-fixed) host branch
  while teardown removes the docker stack; the resources are disjoint. **But
  running it concurrently with `ephemeral_teardown` saves only the ~506 ms
  teardown**, because the LLM call is several times longer than the teardown and
  stays on the critical path. The real lever is **detaching the LLM layer** so the
  terminal returns after the ~78 ms deterministic pass and the verdict lands async.

![Teardown latency: the seed-branch review's added cost under three scheduling options](assets/teardown-latency.svg)

## Where the cost is (measured)

| Component                                         | Cost                                          | Source                                                                                                                                      |
| ------------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| docker teardown (`compose down -v`, the baseline) | **506 ms** median (491–577)                   | wall-clock = max(parallel stops)+containers+network+volumes over the 19 runs on the `perf-history` branch (`.github/teardown-history.json`) |
| seed review — deterministic layer (no key)        | **78 ms** (60 ms of it Python+import startup) | `python3 -m monitorlib.seed_review` subprocess over #1215's own 1743-line diff, no monitor key                                              |
| seed review — LLM layer (key set)                 | **~3.5 s** typical (2–10 s)                   | estimate: one `claude-sonnet-4-6` call, `max_tokens=800`, ≤80k-token diff, 10 s/attempt timeout — not tracked by the repo                   |

The deterministic layer is cheap because it is all local git: a `git diff --raw -z`,
the whole-diff `git diff`, and a per-manifest `git diff -- <path>` only for files
whose basename is in the content-rule set (`package.json`, `Makefile`, …). For a
1743-line diff that is ~18 ms of git on top of ~60 ms interpreter startup.

The LLM layer is the whole story when a key is configured. It is a single
non-streaming request whose latency is prefill-of-the-diff plus decode-of-the-
verdict; near the 80k-token input cap it approaches (and a retry can exceed) the
10 s per-attempt timeout.

## Can it run in parallel?

**Structurally, yes.** Once `worktree_extract_to_host` returns, the host branch
`claude/<id>` exists and nothing else writes it; the review only does
`git -C <repo> diff <base> <branch>` (host-side) and then one HTTP call. It never
touches the container or the docker resources that `ephemeral_teardown` removes
(`bin/claude-guard` ~2122). So the review and the teardown are independent and can
overlap.

**But the payoff is bounded by the teardown, which is the small term.** Scheduling
options, in increasing order of payoff:

1. **Serial (today)** — review, then teardown. Wait ≈ `det + LLM + teardown`
   ≈ 4.08 s (keyed).
2. **Review ∥ teardown** — start the review in the background, run teardown
   concurrently, `wait` before the wrapper exits so the warning still prints. Wait
   ≈ `det + max(LLM, teardown)` ≈ 3.58 s. **Saves the ~506 ms teardown only** —
   the LLM still gates the exit. Cheap and harmless, but modest.
3. **LLM detached** — print the (instant, talk-past-proof) deterministic flags
   synchronously, run only the LLM layer fully in the background (it already writes a `.review.json` to the seed-branches dir), and let the wrapper exit. Wait ≈
   `det + teardown` ≈ 0.58 s. **Saves the whole ~3.5 s LLM call.** The verdict
   lands asynchronously, so the on-screen warning at exit no longer carries the LLM
   judgement (only the deterministic flags) — a **UX trade-off**, since the review
   is explicitly advisory and non-blocking ("the work is already safe on the
   branch").

Option 3 is the only one that removes the dominant cost. Option 2 is a strict but
small improvement and composes with 3 (detached LLM still wants its teardown
overlapped). Neither changes any security boundary — the review is a filter, not a
gate, on a path the threat model already places out of scope.

## Recommendation

If the goal is to make session-end feel instant again, **detach the LLM layer
(option 3)** and keep the deterministic flags synchronous, optionally folding in
option 2 for the residual teardown. This is a launch-path change in
`_ephemeral_cleanup` (kcov-gated 100%, signal-shielded teardown), so it needs the
background/`wait` wiring, output-ordering care, and tests — and the async-verdict
delivery is a product decision (surface it via the existing notification path, or
just the `.review.json` the user can read). The parallelize-with-teardown variant
alone is not worth the load-bearing-path churn for ~0.5 s.

## Reproduce

```bash
# Teardown baseline (real stack; root-owns workspace — chown back after locally):
bash bin/bench-teardown.bash            # human table
bash bin/bench-teardown.bash --json     # machine-readable per-leg ms

# Deterministic-layer cost with NO monitor key (LLM short-circuits):
env -u ANTHROPIC_API_KEY -u MONITOR_API_KEY -u OPENROUTER_API_KEY -u VENICE_INFERENCE_KEY \
  PYTHONPATH=.claude/hooks \
  python3 -m monitorlib.seed_review "$PWD" "$(git merge-base HEAD origin/main)" HEAD

# Re-render this chart:
python3 docs/assets/gen-teardown-latency-chart.py
```

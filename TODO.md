# TODO

## Prompt injection defenses

- [ ] Investigate autoformatter-based sanitization as an alternative/complement to
      detection-only blocking. An autoformatter could normalize text by stripping
      non-rendering characters before they reach the LLM, rather than hard-blocking.
      See: https://embracethered.com/blog/posts/2026/scary-agent-skills/

- [ ] PR upstream to `out-of-character`: add detection for bidi controls (U+200E-F,
      U+202A-E, U+2066-9, U+061C), variation selectors (U+FE00-FE0F, U+E0100-E01EF),
      blank-rendering chars (U+2800, U+3164, U+115F), and object replacement (U+FFFC).
      These are handled by EXTRA_CHECKS in sanitize-input.mjs. The `\p{Cf}` category
      check auto-covers all 170 Format chars (including tag chars, Egyptian hieroglyph
      controls, musical formatting, etc.) and auto-updates with Node’s ICU/Unicode data.

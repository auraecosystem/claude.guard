"""Provider usage-block field names — the single source of truth for which keys
carry token counts.

A provider's ``usage`` block (returned by the monitor's own API calls and mirrored
into the agent's stream-json transcript) spells token counts one of two ways: the
Anthropic Messages shape (``input_tokens``/``output_tokens`` with cache reads and
writes split out separately) or the OpenAI-compatible shape (``prompt_tokens``/
``completion_tokens``, no cache split) that Venice and OpenRouter speak. Both the
monitor's response parser (``monitorlib.api``) and the CTF cost meter
(``tests.ctf.cost``) read these blocks; a consumer that hard-codes only one dialect
silently counts the other as zero — the bug that read the OpenRouter-proxied agent's
cost as ``$0.0000``. Defining the field names once here keeps every consumer in lock
step, so a dialect can never be dropped from one reader but not another.
"""

# Anthropic Messages usage. Prompt tokens are reported across a base field and two
# cache fields, each billed at (approximately) the input rate, so all three sum into
# "input"; output is its own field. The monitor prices cache reads/writes separately,
# so it reads the cache fields by name too.
ANTHROPIC_INPUT_FIELD = "input_tokens"
ANTHROPIC_CACHE_READ_FIELD = "cache_read_input_tokens"
ANTHROPIC_CACHE_WRITE_FIELD = "cache_creation_input_tokens"
ANTHROPIC_OUTPUT_FIELD = "output_tokens"
ANTHROPIC_PROMPT_FIELDS = (
    ANTHROPIC_INPUT_FIELD,
    ANTHROPIC_CACHE_READ_FIELD,
    ANTHROPIC_CACHE_WRITE_FIELD,
)

# OpenAI-compatible usage (Venice, OpenRouter): single prompt/completion fields, no
# prompt-cache split on this wire.
OPENAI_PROMPT_FIELD = "prompt_tokens"
OPENAI_OUTPUT_FIELD = "completion_tokens"


def _is_number(value: object) -> bool:
    """A real numeric token count (a bool is an int in Python — never a quantity)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def usage_tokens(usage: dict) -> tuple[int, int]:
    """(input, output) tokens from one usage block, across both wire dialects.

    Anthropic's cache reads/writes are summed into input so cached turns aren't
    undercounted; the OpenAI prompt/completion fields are read only when the Anthropic
    keys are absent (a turn that carries both is Anthropic, with cache fields). A
    consumer that knows its wire reads the field-name constants directly; a consumer
    parsing a mixed-provenance stream (the agent transcript) uses this collapse.
    """
    inp = sum(
        int(usage[f]) for f in ANTHROPIC_PROMPT_FIELDS if _is_number(usage.get(f))
    )
    if inp == 0 and _is_number(usage.get(OPENAI_PROMPT_FIELD)):
        inp = int(usage[OPENAI_PROMPT_FIELD])
    out = (
        usage[ANTHROPIC_OUTPUT_FIELD]
        if _is_number(usage.get(ANTHROPIC_OUTPUT_FIELD))
        else None
    )
    if out is None and _is_number(usage.get(OPENAI_OUTPUT_FIELD)):
        out = usage[OPENAI_OUTPUT_FIELD]
    return inp, int(out) if _is_number(out) else 0

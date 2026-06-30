"""Dataset identity for eval runs — the SSOT both report modules and runners share.

A run's *dataset* is what it was measured on: for the monitor eval the set of
`--sources`, for the sabotage eval the pinned bench commit plus the audited
strata. Each row carries a ``dataset`` object::

    "dataset": {"label": "<segmentation key>", "composition": "<signature>"}

``label`` is the key the charts segment on — operator-set (``--dataset-label`` /
the env var), defaulting to ``composition`` when unset. ``composition`` is the
normalized signature the label was derived from, kept for audit and for the
read-time fallback below.

The live README/PR charts plot only the **current** dataset (the trailing run of
rows sharing the latest row's label) so switching datasets starts a fresh graph;
the archive doc plots every dataset as its own facet. Both read identity through
``dataset_label`` here, which derives an identity for legacy rows that predate the
``dataset`` field — so a mixed old/new history segments correctly without a
backfill.
"""

MONITOR = "monitor"
SABOTAGE = "sabotage"


def normalize_sources(sources: str) -> str:
    """A monitor ``--sources`` string in canonical form: trimmed, empties dropped,
    sorted, comma-joined. Sorting makes the signature order-independent, so
    reordering the same sources never reads as a different dataset."""
    parts = [s.strip() for s in (sources or "").split(",")]
    return ",".join(sorted(p for p in parts if p))


def _sabotage_composition(bench_sha: str, strata) -> str:
    """``<short-bench-sha>:<sorted,strata>`` — the sabotage dataset signature."""
    return f"{(bench_sha or '')[:7]}:{','.join(sorted(strata or []))}"


def monitor_dataset(sources: str, label: str = "") -> dict:
    """The ``dataset`` object for a monitor run; label defaults to the composition."""
    composition = normalize_sources(sources)
    return {"label": label or composition, "composition": composition}


def sabotage_dataset(bench_sha: str, strata, label: str = "") -> dict:
    """The ``dataset`` object for a sabotage run; label defaults to the composition."""
    composition = _sabotage_composition(bench_sha, strata)
    return {"label": label or composition, "composition": composition}


def _derived_composition(row: dict, kind: str) -> str:
    """Reconstruct a legacy row's composition from the fields it does carry."""
    if kind == MONITOR:
        return normalize_sources(row.get("sources") or "")
    if kind == SABOTAGE:
        return _sabotage_composition(row.get("bench_sha") or "", row.get("strata"))
    raise ValueError(f"unknown dataset kind: {kind!r}")


def dataset_label(row: dict, *, kind: str) -> str:
    """The segmentation key for ``row`` — its explicit ``dataset.label`` when
    present, else its ``dataset.composition``, else (legacy rows with no
    ``dataset`` field) the composition derived from the row's own fields. The
    single place an identity is resolved, so old and new rows compare cleanly."""
    ds = row.get("dataset") or {}
    return ds.get("label") or ds.get("composition") or _derived_composition(row, kind)


def group_by_dataset(rows: list, *, kind: str) -> list:
    """``rows`` split into ``(label, [rows])`` groups, one per maximal contiguous
    run of a dataset label, in order — the archive's per-dataset facets/table. An
    abandoned-then-resumed label yields two groups (it is a fresh graph each time),
    matching how the live window treats it."""
    groups: list = []
    for row in rows:
        label = dataset_label(row, kind=kind)
        if groups and groups[-1][0] == label:
            groups[-1][1].append(row)
        else:
            groups.append((label, [row]))
    return groups


def current_dataset_window(rows: list, *, kind: str) -> list:
    """The trailing maximal run of ``rows`` sharing the LAST row's dataset label.

    This is the live-chart reset: the last row is always the current run, so a row
    introducing a new dataset yields just ``[current]`` and the prior dataset's
    rows drop off the live graph. Empty in, empty out."""
    if not rows:
        return []
    target = dataset_label(rows[-1], kind=kind)
    out: list = []
    for row in reversed(rows):
        if dataset_label(row, kind=kind) != target:
            break
        out.append(row)
    out.reverse()
    return out

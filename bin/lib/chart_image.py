"""Local matplotlib renderer for the monitor perf line charts.

The companion :mod:`quickchart` builder encodes a chart's whole dataset into a
quickchart.io GET URL. That URL is self-contained and never expires, but GitHub
serves every embedded image through its camo proxy, which hex-encodes the source
URL into the proxied request path — doubling its length — and answers a proxied
request line over ~8 KiB with HTTP 414 (URI Too Long). A multi-series chart with
IQR bands (the per-stage timing chart: five lines, each with a p25–p75 band)
overflows that budget, so the embedded image renders broken.

This module sidesteps the limit by rendering the chart to a PNG locally. The
caller uploads that file to stable hosting and embeds its short URL, so no
dataset ever rides in the URL. It consumes the same ``labels`` + ``Series`` shape
as :func:`quickchart.chart_url`, so a caller can swap one renderer for the other
without reshaping its data.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import matplotlib

matplotlib.use("Agg")  # headless: no display, write straight to a file
import matplotlib.pyplot as plt  # noqa: E402  (must follow the Agg backend select)


class SeriesLike(Protocol):
    """The subset of :class:`quickchart.Series` this renderer reads."""

    label: str
    data: list
    color: str
    band: tuple | None


def _plot_points(x: range, series: SeriesLike, ax: "plt.Axes") -> None:
    """Draw one series' line+markers, breaking the line at ``None`` y-values."""
    ys = [float("nan") if v is None else v for v in series.data]
    ax.plot(x, ys, color=series.color, marker="o", markersize=4, linewidth=2)


def _plot_band(x: range, series: SeriesLike, ax: "plt.Axes") -> None:
    """Shade a series' (lows, highs) IQR band; skip points missing a bound."""
    if series.band is None:
        return
    lows, highs = series.band
    los = [float("nan") if v is None else v for v in lows]
    his = [float("nan") if v is None else v for v in highs]
    ax.fill_between(x, los, his, color=series.color, alpha=0.13, linewidth=0)


def _label_line_end(x: range, series: SeriesLike, ax: "plt.Axes") -> None:
    """Write the series name at its last real point, in the line's color."""
    for i in reversed(range(len(series.data))):
        if series.data[i] is not None:
            ax.annotate(
                series.label,
                xy=(x[i], series.data[i]),
                xytext=(6, 0),
                textcoords="offset points",
                color=series.color,
                fontweight="bold",
                va="center",
                fontsize=9,
            )
            return


def render_chart(
    labels: Sequence[str],
    series: Sequence[SeriesLike],
    out_path: Path,
    *,
    title: str = "",
    y_label: str = "",
    inline_labels: bool = False,
    width_px: int = 640,
    height_px: int = 320,
    dpi: int = 144,
) -> Path:
    """Render ``series`` to a PNG at ``out_path`` and return that path.

    ``inline_labels`` writes each series' name at its last point (replacing the
    legend), matching the quickchart inline-label layout. Pixel dimensions are
    scaled by ``dpi`` so the embed is crisp on high-density screens.
    """
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    for s in series:
        _plot_band(x, s, ax)
    for s in series:
        _plot_points(x, s, ax)
    if inline_labels:
        for s in series:
            _label_line_end(x, s, ax)
    elif sum(1 for s in series if s.label) > 1:
        ax.legend([s.label for s in series], loc="best", fontsize=8)

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    if title:
        ax.set_title(title, fontsize=10)
    if y_label:
        ax.set_ylabel(y_label, fontsize=9)
    ax.grid(True, color="#eee", linewidth=0.5)
    # Right margin so an inline end-of-line label isn't clipped at the axes edge.
    ax.margins(x=0.18 if inline_labels else 0.02)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)
    return out_path

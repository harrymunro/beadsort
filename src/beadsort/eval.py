"""Compare one output dimension against ground truth the user already has.

Truth comes from an existing label (presence = yes), a label prefix (`waiting-on:` ->
value), a metadata key, or a JSON file `{bead_id: value}`. The report is for the user's
own eyes: precision, recall and F1 per value, coverage, and accuracy per confidence band
so thresholds can be tuned on real data.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from beadsort.beads import Bead
from beadsort.errors import UsageError


def truth_from_label(beads: Iterable[Bead], label: str) -> dict[str, str]:
    return {b.id: ("yes" if label in b.labels else "no") for b in beads}


def truth_from_prefix(beads: Iterable[Bead], prefix: str) -> dict[str, str]:
    prefix = prefix if prefix.endswith(":") else prefix + ":"
    out: dict[str, str] = {}
    for b in beads:
        for label in b.labels:
            if label.startswith(prefix):
                out[b.id] = label[len(prefix) :]
                break
    return out


def truth_from_metadata(beads: Iterable[Bead], key: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for b in beads:
        value: Any = b.metadata
        for part in key.split("."):
            value = value.get(part) if isinstance(value, Mapping) else None
        if value is not None:
            out[b.id] = str(value)
    return out


def truth_from_json(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UsageError(f"cannot read truth file {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise UsageError("truth file must be a JSON object of bead id -> value")
    return {str(k): str(v) for k, v in raw.items() if v is not None}


@dataclass
class EvalReport:
    dimension: str
    total: int = 0
    covered: int = 0
    correct: int = 0
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)  # truth -> predicted -> n
    per_value: dict[str, dict[str, float]] = field(default_factory=dict)
    macro_f1: float = 0.0
    bands: list[dict[str, Any]] = field(default_factory=list)
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    uncovered_wrong_if_forced: int = 0

    @property
    def coverage(self) -> float:
        return self.covered / self.total if self.total else 0.0

    @property
    def accuracy(self) -> float:
        return self.correct / self.covered if self.covered else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "total": self.total,
            "covered": self.covered,
            "coverage": round(self.coverage, 4),
            "correct": self.correct,
            "accuracy": round(self.accuracy, 4),
            "macro_f1": round(self.macro_f1, 4),
            "per_value": self.per_value,
            "confusion": self.confusion,
            "bands": self.bands,
            "disagreements": self.disagreements,
            "review_yield": self.uncovered_wrong_if_forced,
        }


def evaluate(
    dimension: str,
    predictions: Mapping[str, str | None],
    truth: Mapping[str, str],
    *,
    confidences: Mapping[str, float] | None = None,
    forced: Mapping[str, str | None] | None = None,
    positive: Iterable[str] | None = None,
    bands: Iterable[float] = (0.5, 0.7, 0.9),
    titles: Mapping[str, str] | None = None,
) -> EvalReport:
    """Score `predictions` against `truth` on the beads present in both.

    `positive` collapses both sides to yes/no: a prediction counts as yes when its value
    is in the set (or is any non-null value when the set is empty). `forced` is what the
    tool would have said for uncovered beads had it been forced; it feeds review yield.
    """
    positive_set = set(positive) if positive is not None else None
    confidences = confidences or {}
    forced = forced or {}
    titles = titles or {}

    def collapse(value: str | None) -> str | None:
        if positive_set is None or value is None or value in {"yes", "no"}:
            return value
        if positive_set:
            return "yes" if value in positive_set else "no"
        return "yes"

    report = EvalReport(dimension=dimension)
    band_edges = sorted(set(bands))
    band_stats: dict[str, list[int]] = {}
    for bead_id, expected in truth.items():
        if bead_id not in predictions:
            continue
        report.total += 1
        predicted = collapse(predictions[bead_id])
        expected_c = collapse(expected) or "no"
        if predicted is None:
            forced_value = collapse(forced.get(bead_id))
            if forced_value is not None and forced_value != expected_c:
                report.uncovered_wrong_if_forced += 1
            continue
        report.covered += 1
        row = report.confusion.setdefault(str(expected_c), {})
        row[predicted] = row.get(predicted, 0) + 1
        conf = confidences.get(bead_id)
        band = _band_name(conf, band_edges)
        stats = band_stats.setdefault(band, [0, 0])
        stats[1] += 1
        if predicted == expected_c:
            report.correct += 1
            stats[0] += 1
        else:
            report.disagreements.append(
                {
                    "id": bead_id,
                    "title": titles.get(bead_id, ""),
                    "expected": expected_c,
                    "predicted": predicted,
                    "confidence": conf,
                }
            )
    values = sorted({*report.confusion, *(p for row in report.confusion.values() for p in row)})
    f1s: list[float] = []
    for value in values:
        tp = report.confusion.get(value, {}).get(value, 0)
        fn = sum(n for p, n in report.confusion.get(value, {}).items() if p != value)
        fp = sum(row.get(value, 0) for t, row in report.confusion.items() if t != value)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        support = tp + fn
        report.per_value[value] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }
        if support:
            f1s.append(f1)
    report.macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0
    for band in sorted(band_stats):
        correct, n = band_stats[band]
        report.bands.append({"band": band, "n": n, "accuracy": round(correct / n, 4) if n else 0.0})
    report.disagreements.sort(key=lambda d: -(d["confidence"] or 0.0))
    return report


def _band_name(conf: float | None, edges: list[float]) -> str:
    if conf is None:
        return "unknown"
    low = 0.0
    for edge in edges:
        if conf < edge:
            return f"{low:.2f}-{edge:.2f}"
        low = edge
    return f">={low:.2f}"

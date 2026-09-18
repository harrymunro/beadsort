"""Deriver for the built-in `size` pack: t-shirt size and breakage risk.

Composite scoring: independent Scores, each normalised to 0..1, combined with weights that
live in the pack thresholds. Retune the weights or bucket edges without re-running the
model. The composite is compared with bucket edges; it is never read as a magnitude.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from beadsort.packs import Answers, DeriveContext, Verdict

DIMENSIONS = ("size", "risk")
RISK_LEVELS = ("low", "medium", "high")
DEFAULT_WEIGHTS = {"scope_breadth": 0.45, "investigation_needed": 0.35, "verification_effort": 0.20}
DEFAULT_BUCKETS = [{"max": 0.30, "value": "s"}, {"max": 0.62, "value": "m"}, {"value": "l"}]


def _bucket_index(value: float, buckets: list[Mapping[str, Any]]) -> int:
    for i, b in enumerate(buckets):
        if "max" not in b or value <= float(b["max"]):
            return i
    return len(buckets) - 1


def _near_edge(value: float, buckets: list[Mapping[str, Any]], dead_zone: float) -> bool:
    return any("max" in b and abs(value - float(b["max"])) < dead_zone for b in buckets)


def derive(answers: Answers, thresholds: Mapping[str, Any], ctx: DeriveContext) -> Verdict:
    verdict = Verdict(labels=dict.fromkeys(DIMENSIONS))
    weights = dict(thresholds.get("weights") or DEFAULT_WEIGHTS)
    buckets = list(thresholds.get("buckets") or DEFAULT_BUCKETS)
    dead_zone = float(thresholds.get("dead_zone", 0.04))
    min_conf = float(thresholds.get("min_score_confidence", 0.40))
    risk_conf = float(thresholds.get("risk_conf", 0.50))

    kind = answers.choice("work_kind")
    verdict.meta["work_kind"] = kind
    umbrella = answers.p("is_umbrella") or 0.0
    if (kind == "not_repo_work" and answers.conf("work_kind") >= 0.60) or umbrella >= 0.70:
        verdict.meta["skipped"] = "not repository work" if kind == "not_repo_work" else "umbrella"
        if ctx.bead.issue_type in {"bug", "feature"}:
            verdict.review.append(
                f"size skipped: {verdict.meta['skipped']} but typed {ctx.bead.issue_type}"
            )
        _risk(answers, risk_conf, verdict)
        return verdict

    total = 0.0
    total_weight = 0.0
    lowest_conf = 1.0
    for qid, weight in weights.items():
        norm = answers.normalised_score(qid)
        if norm is None:
            verdict.uncertain.append("size")
            verdict.review.append(f"size: no answer for {qid}")
            _risk(answers, risk_conf, verdict)
            return verdict
        total += float(weight) * norm
        total_weight += float(weight)
        lowest_conf = min(lowest_conf, answers.conf(qid))
        verdict.meta[qid] = round(norm, 3)
    composite = total / total_weight if total_weight else 0.0
    verdict.meta["composite"] = round(composite, 4)
    verdict.meta["lowest_confidence"] = round(lowest_conf, 3)

    index = _bucket_index(composite, buckets)
    holistic = answers.score("overall_effort")
    if lowest_conf < min_conf:
        verdict.uncertain.append("size")
        verdict.review.append(f"size: a contributing score is unsure ({lowest_conf:.2f})")
    elif _near_edge(composite, buckets, dead_zone):
        verdict.uncertain.append("size")
        verdict.review.append(f"size: composite {composite:.2f} sits on a bucket edge")
    elif holistic is not None and abs(round(holistic) - index) >= 2:
        verdict.uncertain.append("size")
        verdict.review.append(
            f"size: composite says {buckets[index]['value']} but holistic effort says "
            f"level {round(holistic)}"
        )
    else:
        verdict.labels["size"] = str(buckets[index]["value"])

    _risk(answers, risk_conf, verdict)
    return verdict


def _risk(answers: Answers, risk_conf: float, verdict: Verdict) -> None:
    score = answers.score("risk_of_breakage")
    if score is None:
        return
    level = min(len(RISK_LEVELS) - 1, max(0, round(score)))
    probs = answers.probabilities("risk_of_breakage")
    non_adjacent = sum(p for k, p in probs.items() if abs(int(k) - level) > 1)
    peak = int(answers.top("risk_of_breakage", 1)[0][0]) if probs else level
    if answers.conf("risk_of_breakage") < risk_conf or non_adjacent > 0.35 or peak != level:
        verdict.uncertain.append("risk")
        verdict.review.append(f"risk: unsure ({answers.describe_top('risk_of_breakage')})")
        return
    verdict.labels["risk"] = RISK_LEVELS[level]

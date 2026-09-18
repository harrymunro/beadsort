"""Answers -> labels for user-written packs, driven by the `outputs:` rules in the YAML.

Rules (each output owns one dimension):

  - {dimension, from: <choice qid>, min_confidence, skip: [choices], rename: {choice: value}}
  - {dimension, from: <noul qid>, yes_above: 0.7, no_below: 0.3, values: {yes, no}, uncertain}
  - {dimension, from: <score qid>, by: argmax, levels: [v0, v1, ...], min_confidence}
  - {dimension, from: <score qid>, by: expectation, buckets: [{max, value}, ..., {value}]}
  - {dimension, composite: {inputs: {qid: {weight, invert}}, buckets: [...], min_confidence}}

Anything uncertain yields no label for that dimension, a review note, and the dimension in
`uncertain` so `on_uncertain: write_unsure` can act on it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from beadsort.packs import Answers, ResolvedPack, Verdict


def _bucket(value: float, buckets: list[Mapping[str, Any]]) -> str:
    for b in buckets:
        if "max" not in b or value <= float(b["max"]):
            return str(b["value"])
    return str(buckets[-1]["value"])


def derive_declarative(resolved: ResolvedPack, answers: Answers) -> Verdict:
    verdict = Verdict()
    pack = resolved.pack
    default_min_conf = float(resolved.thresholds.get("min_confidence", 0.6))
    for spec in pack.outputs:
        dim = str(spec["dimension"])
        verdict.labels[dim] = None
        if "composite" in spec:
            _composite(dim, spec["composite"], answers, verdict, default_min_conf)
            continue
        qid = str(spec["from"])
        q = pack.questions[qid]
        if not answers.has(qid):
            verdict.review.append(f"{dim}: no answer for {qid}")
            continue
        if q.type == "choice":
            choice = answers.choice(qid)
            conf = answers.conf(qid)
            min_conf = float(spec.get("min_confidence", default_min_conf))
            if choice is None or conf < min_conf:
                verdict.uncertain.append(dim)
                verdict.review.append(f"{dim}: unsure ({answers.describe_top(qid)})")
                continue
            if choice in (spec.get("skip") or []):
                continue
            verdict.labels[dim] = str((spec.get("rename") or {}).get(choice, choice))
        elif q.type == "noul":
            p = answers.p(qid) or 0.0
            yes_above = float(spec.get("yes_above", 0.7))
            no_below = float(spec.get("no_below", 0.3))
            values = {"yes": "yes", "no": "no", **(spec.get("values") or {})}
            if p >= yes_above:
                verdict.labels[dim] = str(values["yes"])
            elif p <= no_below:
                verdict.labels[dim] = str(values["no"])
            elif spec.get("uncertain") is not None:
                verdict.labels[dim] = str(spec["uncertain"])
            else:
                verdict.uncertain.append(dim)
                verdict.review.append(f"{dim}: unsure (p={p:.2f})")
        elif q.type == "score":
            conf = answers.conf(qid)
            min_conf = float(spec.get("min_confidence", default_min_conf))
            by = spec.get("by", "argmax")
            if by == "argmax":
                if conf < min_conf:
                    verdict.uncertain.append(dim)
                    verdict.review.append(f"{dim}: unsure ({answers.describe_top(qid)})")
                    continue
                top = answers.top(qid, 1)
                index = int(top[0][0]) if top else round(answers.score(qid) or 0)
                levels = list(spec["levels"])
                verdict.labels[dim] = str(levels[min(index, len(levels) - 1)])
            else:
                norm = answers.normalised_score(qid)
                if norm is None or conf < min_conf:
                    verdict.uncertain.append(dim)
                    verdict.review.append(f"{dim}: unsure (score conf {conf:.2f})")
                    continue
                verdict.labels[dim] = _bucket(norm, list(spec["buckets"]))
    return verdict


def _composite(
    dim: str,
    comp: Mapping[str, Any],
    answers: Answers,
    verdict: Verdict,
    default_min_conf: float,
) -> None:
    total_weight = 0.0
    total = 0.0
    min_conf_seen = 1.0
    for qid, cfg in comp["inputs"].items():
        cfg = cfg if isinstance(cfg, Mapping) else {"weight": cfg}
        weight = float(cfg.get("weight", 1.0))
        invert = bool(cfg.get("invert", False))
        if not answers.has(qid):
            verdict.review.append(f"{dim}: no answer for {qid}")
            verdict.uncertain.append(dim)
            return
        raw = answers.raw[qid]
        if raw.get("type") == "noul":
            value = float(raw.get("noul", 0.0))
        else:
            norm = answers.normalised_score(qid)
            if norm is None:
                verdict.uncertain.append(dim)
                return
            value = norm
            min_conf_seen = min(min_conf_seen, answers.conf(qid))
        if invert:
            value = 1.0 - value
        total += weight * value
        total_weight += weight
    if total_weight == 0:
        verdict.uncertain.append(dim)
        return
    composite = total / total_weight
    min_conf = float(comp.get("min_confidence", default_min_conf))
    if min_conf_seen < min_conf:
        verdict.uncertain.append(dim)
        verdict.review.append(f"{dim}: unsure (lowest score confidence {min_conf_seen:.2f})")
        verdict.meta[f"{dim}_composite"] = round(composite, 4)
        return
    verdict.labels[dim] = _bucket(composite, list(comp["buckets"]))
    verdict.meta[f"{dim}_composite"] = round(composite, 4)

"""Test doubles for the model."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from beadsort.engine import JudgeResult


def choice(option: str, confidence: float = 0.9, **others: float) -> dict[str, Any]:
    probs = {option: confidence, **others}
    total = sum(probs.values())
    if total < 1.0:
        probs["_rest"] = round(1.0 - total, 4)
    return {"type": "choice", "choice": option, "confidence": confidence, "probabilities": probs}


def noul(p: float) -> dict[str, Any]:
    return {"type": "noul", "noul": p}


def score(level: int, levels: int, confidence: float = 0.9) -> dict[str, Any]:
    probs = {str(i): 0.0 for i in range(levels)}
    probs[str(level)] = confidence
    spread = (1.0 - confidence) / max(1, levels - 1)
    for i in range(levels):
        if i != level:
            probs[str(i)] = round(spread, 4)
    value = sum(i * p for i, p in ((int(k), v) for k, v in probs.items()))
    return {
        "type": "score",
        "score": round(value, 4),
        "confidence": confidence,
        "probabilities": probs,
    }


class FakeJudge:
    """Answers from a table keyed by bare question id (pack prefix stripped), or a callable."""

    def __init__(
        self,
        table: Mapping[str, Mapping[str, Any]]
        | Callable[[Mapping[str, Any], str], Mapping[str, Any] | None]
        | None = None,
        *,
        model: str = "jev-fake",
        tokens: int = 500,
        budget: int | None = None,
    ) -> None:
        self.table = table or {}
        self.model = model
        self.tokens = tokens
        self.budget = budget
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def judge(
        self, state: Mapping[str, Any], questions: Mapping[str, Mapping[str, Any]]
    ) -> JudgeResult:
        self.calls.append((json.loads(json.dumps(state)), dict(questions)))
        if self.budget is not None:
            assert len(json.dumps(state)) <= self.budget, "state exceeded the budget"
        answers: dict[str, Any] = {}
        for full_id, spec in questions.items():
            _pack, _, qid = full_id.partition("__")
            answer = self.table(state, qid) if callable(self.table) else self.table.get(qid)
            if answer is None:
                answer = _default_for(spec)
            answers[full_id] = answer
        return JudgeResult(
            answers=answers, model=self.model, request_id="req-fake", input_tokens=self.tokens
        )


def _default_for(spec: Mapping[str, Any]) -> dict[str, Any]:
    if spec["type"] == "noul":
        return noul(0.05)
    if spec["type"] == "choice":
        first = next(iter(spec["criteria"]))
        return choice(first, 0.95)
    return score(0, len(spec["criteria"]), 0.95)

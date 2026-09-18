"""The run loop: select beads, build state, ask the model once per bead, derive verdicts.

One request per bead carries every enabled pack's uncached questions, with ids prefixed by
pack. The model never sees the ids. Model calls run in a small thread pool; nothing else
is parallel. Every fresh answer set is cached under a key that covers pack, version,
model, questions and state, so a second run makes no calls unless something changed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

from beadsort.beads import Bead, BeadIndex
from beadsort.cache import CacheStore, call_key
from beadsort.config import BeadsortConfig
from beadsort.errors import ApiError
from beadsort.packs import Answers, DeriveContext, ResolvedPack, Verdict, derive, precheck
from beadsort.state import build_state, state_size

PRICE_PER_MTOK_INPUT = 0.042
QUESTION_SEP = "__"


@dataclass(frozen=True)
class JudgeResult:
    answers: Mapping[str, Mapping[str, Any]]
    model: str
    request_id: str | None = None
    input_tokens: int | None = None


class Judge(Protocol):
    def judge(
        self, state: Mapping[str, Any], questions: Mapping[str, Mapping[str, Any]]
    ) -> JudgeResult: ...


@dataclass
class BeadResult:
    bead: Bead
    verdicts: dict[str, Verdict] = field(default_factory=dict)  # pack id -> verdict
    packs_applied: list[str] = field(default_factory=list)
    packs_fresh: list[str] = field(default_factory=list)  # packs answered by the model this run
    packs_prechecked: list[str] = field(default_factory=list)
    state_chars: int = 0
    input_tokens: int = 0
    error: str | None = None

    @property
    def labels(self) -> dict[str, str | None]:
        merged: dict[str, str | None] = {}
        for verdict in self.verdicts.values():
            merged.update(verdict.labels)
        return merged

    @property
    def review(self) -> list[str]:
        out: list[str] = []
        for pack_id, verdict in self.verdicts.items():
            out.extend(f"{pack_id}: {r}" for r in verdict.review)
        return out

    @property
    def uncertain(self) -> list[str]:
        out: list[str] = []
        for verdict in self.verdicts.values():
            out.extend(verdict.uncertain)
        return out


@dataclass
class RunReport:
    results: list[BeadResult] = field(default_factory=list)
    selected: int = 0
    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    seconds: float = 0.0
    model: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def estimated_cost_usd(self) -> float:
        return self.input_tokens / 1_000_000 * PRICE_PER_MTOK_INPUT


def select_beads(
    index: BeadIndex,
    config: BeadsortConfig,
    *,
    statuses: Iterable[str] | None = None,
    types: Iterable[str] | None = None,
    only: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[Bead]:
    statuses_set = set(statuses) if statuses else set(config.select.statuses)
    types_set = set(types) if types else set(config.select.types)
    only_set = set(only) if only else None
    chosen: list[Bead] = []
    for bead_id in sorted(index.beads):
        bead = index.beads[bead_id]
        if only_set is not None:
            if bead.id not in only_set:
                continue
        else:
            if bead.status not in statuses_set:
                continue
            if types_set and bead.issue_type not in types_set:
                continue
        chosen.append(bead)
        if limit and len(chosen) >= limit:
            break
    return chosen


def _needs(packs: Iterable[ResolvedPack]) -> set[str]:
    needs: set[str] = set()
    for pack in packs:
        needs.update(pack.pack.state_needs)
    return needs


def _context(
    bead: Bead, index: BeadIndex, config: BeadsortConfig, state: Mapping[str, Any]
) -> DeriveContext:
    return DeriveContext(
        bead=bead,
        config=config,
        state=state,
        open_blockers=tuple(index.open_blockers(bead)),
        children=tuple(index.children_of(bead)) if bead.is_epic else (),
        current_labels=bead.labels,
    )


def run(
    beads: Iterable[Bead],
    packs: list[ResolvedPack],
    *,
    index: BeadIndex,
    config: BeadsortConfig,
    cache: CacheStore,
    judge: Judge | None,
    model: str,
    workers: int = 8,
    offline: bool = False,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> RunReport:
    """Classify `beads` with `packs`. `judge=None` or `offline=True` uses the cache only."""
    started = time.monotonic()
    report = RunReport(model=model)
    needs = _needs(packs)
    jobs: list[tuple[BeadResult, dict[str, Any], dict[str, str], dict[str, Mapping[str, Any]]]] = []

    for bead in beads:
        report.selected += 1
        result = BeadResult(bead=bead)
        state = build_state(bead, index, config, needs)
        result.state_chars = state_size(state)
        ctx = _context(bead, index, config, state)
        cache.touch_bead(bead.id, content_hash=bead.content_hash(), title=bead.title)
        keys: dict[str, str] = {}
        questions: dict[str, Mapping[str, Any]] = {}
        for pack in packs:
            if not pack.pack.applies(bead, config):
                continue
            result.packs_applied.append(pack.id)
            key = call_key(pack.id, pack.version, model, pack.questions, state)
            keys[pack.id] = key
            pre = precheck(pack, ctx)
            if pre is not None:
                cache.record_precheck(
                    bead.id, pack.id, pack_version=pack.version, key=key, verdict=pre.to_dict()
                )
                result.verdicts[pack.id] = pre
                result.packs_prechecked.append(pack.id)
                continue
            status = cache.status_for(bead.id, pack.id, key)
            if status == "unchanged" and not force:
                report.cached += 1
                continue
            for qid, spec in pack.questions.items():
                questions[f"{pack.id}{QUESTION_SEP}{qid}"] = spec
        jobs.append((result, state, keys, questions))
        report.results.append(result)

    def _ask(
        job: tuple[BeadResult, dict[str, Any], dict[str, str], dict[str, Mapping[str, Any]]],
    ) -> None:
        result, state, keys, questions = job
        if not questions:
            return
        if judge is None or offline:
            result.error = "offline: answers not cached"
            return
        try:
            outcome = judge.judge(state, questions)
        except ApiError as exc:
            result.error = f"{exc.code}: {exc.message}"
            return
        except Exception as exc:  # noqa: BLE001 - one bead must not sink the run
            result.error = f"{type(exc).__name__}: {exc}"
            return
        result.input_tokens = outcome.input_tokens or 0
        by_pack: dict[str, dict[str, Any]] = {}
        for full_id, answer in outcome.answers.items():
            pack_id, _, qid = full_id.partition(QUESTION_SEP)
            by_pack.setdefault(pack_id, {})[qid] = dict(answer)
        for pack in packs:
            if pack.id not in by_pack:
                continue
            cache.record_answers(
                result.bead.id,
                pack.id,
                pack_version=pack.version,
                key=keys[pack.id],
                model=outcome.model,
                request_id=outcome.request_id,
                input_tokens=outcome.input_tokens,
                answers=by_pack[pack.id],
            )
            result.packs_fresh.append(pack.id)
        report.model = outcome.model

    pending = [job for job in jobs if job[3]]
    if pending:
        if progress:
            progress(f"asking the model about {len(pending)} bead(s) with {workers} worker(s)")
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(_ask, pending))
        report.calls = sum(1 for job in pending if not job[0].error)

    # Derive verdicts for every applicable pack, fresh or cached.
    for result, state, _keys, _questions in jobs:
        bead = result.bead
        ctx = _context(bead, index, config, state)
        for pack in packs:
            if pack.id not in result.packs_applied or pack.id in result.verdicts:
                continue
            entry = cache.pack_entry(bead.id, pack.id)
            if not entry or not entry.get("answers"):
                if result.error is None:
                    result.error = "no answers available"
                continue
            answers = Answers(entry["answers"])
            verdict = derive(pack, answers, ctx)
            if config.on_uncertain == "write_unsure":
                for dim in verdict.uncertain:
                    if verdict.labels.get(dim) is None:
                        verdict.labels[dim] = "unsure"
            cache.record_verdict(bead.id, pack.id, verdict.to_dict())
            result.verdicts[pack.id] = verdict
        report.input_tokens += result.input_tokens

    report.seconds = time.monotonic() - started
    cache.set_last_run(
        model=report.model,
        beads=report.selected,
        calls=report.calls,
        cached=report.cached,
        input_tokens=report.input_tokens,
    )
    return report


# ---- the real judge ------------------------------------------------------------------


class TypeSafeJudge:
    """Wraps the TypeSafe SDK. Never sets the SDK log level; never logs bodies."""

    def __init__(self, api_key: str, *, model: str, timeout: float = 120.0) -> None:
        try:
            from typesafe_sdk import RetryPolicy, TypeSafeClient
        except ImportError as exc:  # pragma: no cover
            raise ApiError("typesafe-sdk is not installed", code="sdk_missing") from exc
        self.model = model
        self._client = TypeSafeClient(
            api_key=api_key,
            model=model,
            timeout=timeout,
            retry=RetryPolicy(max_retries=4, backoff_max=10.0, timeout=180.0),
        )

    @staticmethod
    def to_sdk_questions(questions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        from typesafe_sdk import Choice, Noul, Score

        out: dict[str, Any] = {}
        for qid, spec in questions.items():
            qtype = spec["type"]
            if qtype == "choice":
                out[qid] = Choice(instructions=spec["instructions"], criteria=spec["criteria"])
            elif qtype == "score":
                out[qid] = Score(instructions=spec["instructions"], criteria=spec["criteria"])
            else:
                criteria = spec.get("criteria")
                out[qid] = Noul(instructions=spec["instructions"], criteria=criteria)
        return out

    @staticmethod
    def to_plain_answers(answers: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for qid, answer in answers.items():
            if hasattr(answer, "noul"):
                out[qid] = {"type": "noul", "noul": round(float(answer.noul), 4)}
            elif hasattr(answer, "choice"):
                out[qid] = {
                    "type": "choice",
                    "choice": answer.choice,
                    "confidence": round(float(answer.confidence), 4),
                    "probabilities": {
                        str(k): round(float(v), 4) for k, v in answer.probabilities.items()
                    },
                }
            elif hasattr(answer, "score"):
                out[qid] = {
                    "type": "score",
                    "score": round(float(answer.score), 4),
                    "confidence": round(float(answer.confidence), 4),
                    "probabilities": {
                        str(k): round(float(v), 4) for k, v in answer.probabilities.items()
                    },
                }
        return out

    def judge(
        self, state: Mapping[str, Any], questions: Mapping[str, Mapping[str, Any]]
    ) -> JudgeResult:
        from typesafe_sdk import (
            TypeSafeAPIError,
            TypeSafeAuthenticationError,
            TypeSafeRateLimitError,
        )

        try:
            response = self._client.system_one(
                dict(state), self.to_sdk_questions(questions), model=self.model
            )
        except TypeSafeAuthenticationError as exc:
            raise ApiError(
                "the API rejected the key (401). Check TYPESAFE_API_KEY.", code="auth"
            ) from exc
        except TypeSafeRateLimitError as exc:
            raise ApiError(
                "rate limited (429) after retries; lower --workers", code="rate_limited"
            ) from exc
        except TypeSafeAPIError as exc:
            status = getattr(exc, "status", None)
            raise ApiError(
                f"API error {status}: {exc}", code="api_error", detail={"status": status}
            ) from exc
        usage = getattr(response, "usage", None)
        return JudgeResult(
            answers=self.to_plain_answers(response.answers),
            model=str(getattr(response, "model", self.model)),
            request_id=getattr(response, "request_id", None),
            input_tokens=getattr(usage, "input_tokens", None) if usage else None,
        )

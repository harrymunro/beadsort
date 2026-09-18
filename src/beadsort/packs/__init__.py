"""Question packs: what to ask the model about a bead and how answers become labels.

A pack is a YAML file. Built-ins live in `packs/builtin/`; users add their own under
`.beadsort/packs/` or `~/.config/beadsort/packs/`. A pack owns one or more label
dimensions. Two enabled packs may not own the same dimension.

Answers -> labels is either declarative (`outputs:` rules, see `declarative.py`) or a
Python deriver (`deriver: beadsort.packs.triage`) for the built-ins, whose cross-question
logic is real code.
"""

from __future__ import annotations

import copy
import importlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

from beadsort.beads import Bead, validate_dimension, validate_value
from beadsort.config import BeadsortConfig, PackRef
from beadsort.errors import BeadsortError

QUESTION_TYPES = ("choice", "score", "noul")
_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_QID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_OWNER_RE = re.compile(r"\{\{\s*owner\s*\}\}")
BUILTIN_IDS = ("triage", "size", "agent-ready")


class PackError(BeadsortError):
    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message, code="pack_invalid", detail=detail)


@dataclass(frozen=True)
class Question:
    id: str
    type: str
    instructions: Any
    criteria: Any = None
    criteria_from: str | None = None
    when: str | None = None  # "people" | "repos": include only when config has that map


@dataclass(frozen=True)
class AppliesTo:
    statuses: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    exclude_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class Pack:
    id: str
    version: int
    description: str
    questions: Mapping[str, Question]
    dimensions: tuple[str, ...]
    applies_to: AppliesTo = AppliesTo()
    requires: tuple[str, ...] = ()
    thresholds: Mapping[str, Any] = field(default_factory=dict)
    outputs: tuple[Mapping[str, Any], ...] = ()
    deriver: str | None = None
    state_needs: tuple[str, ...] = ("parent",)
    source: str = "builtin"

    def applies(self, bead: Bead, config: BeadsortConfig) -> bool:
        statuses = self.applies_to.statuses or config.select.statuses
        if bead.status not in statuses:
            return False
        if self.applies_to.types and bead.issue_type not in self.applies_to.types:
            return False
        if bead.issue_type in self.applies_to.exclude_types:
            return False
        return not (config.select.types and bead.issue_type not in config.select.types)

    def missing_requirements(self, config: BeadsortConfig) -> list[str]:
        missing = []
        for req in self.requires:
            if req == "people" and not config.people:
                missing.append("people")
            elif req == "repos" and not config.repos:
                missing.append("repos")
            elif req == "owner" and config.owner == "the project owner":
                missing.append("owner")
        return missing


@dataclass(frozen=True)
class ResolvedPack:
    """A pack with config applied: owner substituted, people injected, gated questions dropped."""

    pack: Pack
    questions: Mapping[str, Mapping[str, Any]]  # qid -> {"type", "instructions", "criteria"?}
    thresholds: Mapping[str, Any]

    @property
    def id(self) -> str:
        return self.pack.id

    @property
    def version(self) -> int:
        return self.pack.version


# ---- verdicts --------------------------------------------------------------------------


@dataclass
class Verdict:
    """What a pack concluded for one bead. `None` means no label for that dimension."""

    labels: dict[str, str | None] = field(default_factory=dict)
    review: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    uncertain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "labels": dict(self.labels),
            "review": list(self.review),
            "meta": dict(self.meta),
            "uncertain": list(self.uncertain),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Verdict:
        return cls(
            labels=dict(raw.get("labels") or {}),
            review=list(raw.get("review") or []),
            meta=dict(raw.get("meta") or {}),
            uncertain=list(raw.get("uncertain") or []),
        )


class Answers:
    """Typed accessors over the plain-dict answers beadsort stores and caches."""

    def __init__(self, raw: Mapping[str, Mapping[str, Any]]) -> None:
        self.raw = dict(raw)

    def has(self, qid: str) -> bool:
        return qid in self.raw

    def get(self, qid: str) -> Mapping[str, Any] | None:
        return self.raw.get(qid)

    def choice(self, qid: str) -> str | None:
        answer = self.raw.get(qid)
        return answer.get("choice") if answer and answer.get("type") == "choice" else None

    def conf(self, qid: str) -> float:
        answer = self.raw.get(qid)
        if not answer:
            return 0.0
        if answer.get("type") == "noul":
            return abs(float(answer.get("noul", 0.5)) - 0.5) * 2
        return float(answer.get("confidence") or 0.0)

    def p(self, qid: str) -> float | None:
        answer = self.raw.get(qid)
        if not answer or answer.get("type") != "noul":
            return None
        return float(answer.get("noul", 0.0))

    def score(self, qid: str) -> float | None:
        answer = self.raw.get(qid)
        if not answer or answer.get("type") != "score":
            return None
        return float(answer.get("score", 0.0))

    def probabilities(self, qid: str) -> dict[str, float]:
        answer = self.raw.get(qid)
        if not answer:
            return {}
        return {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}

    def levels(self, qid: str) -> int:
        return len(self.probabilities(qid))

    def normalised_score(self, qid: str) -> float | None:
        score = self.score(qid)
        levels = self.levels(qid)
        if score is None or levels < 2:
            return None
        return score / (levels - 1)

    def top(self, qid: str, n: int = 2) -> list[tuple[str, float]]:
        probs = self.probabilities(qid)
        return sorted(probs.items(), key=lambda kv: -kv[1])[:n]

    def describe_top(self, qid: str) -> str:
        return ", ".join(f"{k} {v:.2f}" for k, v in self.top(qid))

    def top_p(self, qid: str) -> float:
        """Probability of the chosen option. For many-option Choices this is the right
        gate; `confidence` (spread over all options) punishes long option lists."""
        chosen = self.choice(qid)
        probs = self.probabilities(qid)
        if chosen is not None and chosen in probs:
            return probs[chosen]
        top = self.top(qid, 1)
        return top[0][1] if top else 0.0

    def margin(self, qid: str) -> float:
        """Lead of the chosen option over the best other option."""
        chosen = self.choice(qid)
        probs = self.probabilities(qid)
        if chosen is None or chosen not in probs:
            return 0.0
        others = [v for k, v in probs.items() if k != chosen]
        return probs[chosen] - (max(others) if others else 0.0)

    def mass(self, qid: str, options: Iterable[str]) -> float:
        probs = self.probabilities(qid)
        return sum(probs.get(o, 0.0) for o in options)


@dataclass(frozen=True)
class DeriveContext:
    bead: Bead
    config: BeadsortConfig
    state: Mapping[str, Any]
    open_blockers: tuple[Bead, ...] = ()
    children: tuple[Bead, ...] = ()
    current_labels: tuple[str, ...] = ()


# ---- loading ---------------------------------------------------------------------------


def _read_yaml(text: str, where: str) -> Mapping[str, Any]:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise PackError(f"{where}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise PackError(f"{where}: top level must be a map")
    return raw


def _tuple(value: Any, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(str(v) for v in value)
    raise PackError(f"{what} must be a list")


def parse_pack(raw: Mapping[str, Any], *, source: str) -> Pack:
    pack_id = str(raw.get("id") or "")
    if not _ID_RE.match(pack_id):
        raise PackError(f"{source}: pack id {pack_id!r} must match [a-z][a-z0-9-]*")
    try:
        version = int(raw.get("version", 1))
    except (TypeError, ValueError) as exc:
        raise PackError(f"{source}: version must be an integer") from exc
    questions_raw = raw.get("questions") or {}
    if not isinstance(questions_raw, Mapping) or not questions_raw:
        raise PackError(f"{source}: questions must be a non-empty map")
    questions: dict[str, Question] = {}
    for qid, spec in questions_raw.items():
        qid = str(qid)
        if not _QID_RE.match(qid):
            raise PackError(f"{source}: question id {qid!r} must match [a-z][a-z0-9_]*")
        if not isinstance(spec, Mapping):
            raise PackError(f"{source}: question {qid} must be a map")
        qtype = str(spec.get("type") or "")
        if qtype not in QUESTION_TYPES:
            raise PackError(f"{source}: question {qid} type must be one of {QUESTION_TYPES}")
        if "instructions" not in spec:
            raise PackError(f"{source}: question {qid} needs instructions")
        criteria = spec.get("criteria")
        criteria_from = spec.get("criteria_from")
        if qtype == "choice" and not criteria and not criteria_from:
            raise PackError(f"{source}: choice question {qid} needs criteria")
        if qtype == "score" and (not isinstance(criteria, list) or not 2 <= len(criteria) <= 10):
            raise PackError(f"{source}: score question {qid} needs 2 to 10 criteria levels")
        if (
            qtype == "noul"
            and criteria is not None
            and (not isinstance(criteria, Mapping) or set(criteria) - {"true", "false"})
        ):
            raise PackError(f"{source}: noul question {qid} criteria must have true/false")
        if criteria_from is not None and criteria_from not in {"people", "repos"}:
            raise PackError(f"{source}: question {qid} criteria_from must be people or repos")
        when = spec.get("when")
        if when is not None and when not in {"people", "repos"}:
            raise PackError(f"{source}: question {qid} when must be people or repos")
        questions[qid] = Question(
            id=qid,
            type=qtype,
            instructions=spec["instructions"],
            criteria=criteria,
            criteria_from=str(criteria_from) if criteria_from else None,
            when=str(when) if when else None,
        )
    applies_raw = raw.get("applies_to") or {}
    if not isinstance(applies_raw, Mapping):
        raise PackError(f"{source}: applies_to must be a map")
    applies = AppliesTo(
        statuses=_tuple(applies_raw.get("statuses"), "applies_to.statuses"),
        types=_tuple(applies_raw.get("types"), "applies_to.types"),
        exclude_types=_tuple(applies_raw.get("exclude_types"), "applies_to.exclude_types"),
    )
    thresholds = raw.get("thresholds") or {}
    if not isinstance(thresholds, Mapping):
        raise PackError(f"{source}: thresholds must be a map")
    outputs_raw = raw.get("outputs") or []
    if not isinstance(outputs_raw, list):
        raise PackError(f"{source}: outputs must be a list")
    deriver = raw.get("deriver")
    if deriver is not None and not isinstance(deriver, str):
        raise PackError(f"{source}: deriver must be a module path string")
    if deriver and outputs_raw:
        raise PackError(f"{source}: use either outputs or deriver, not both")
    if not deriver and not outputs_raw:
        raise PackError(f"{source}: a pack needs outputs rules or a deriver")
    dimensions = list(_tuple(raw.get("dimensions"), "dimensions"))
    for spec in outputs_raw:
        if not isinstance(spec, Mapping) or not spec.get("dimension"):
            raise PackError(f"{source}: every output needs a dimension")
        if spec["dimension"] not in dimensions:
            dimensions.append(str(spec["dimension"]))
    if not dimensions:
        raise PackError(f"{source}: a deriver pack must list its dimensions")
    for dim in dimensions:
        try:
            validate_dimension(dim)
        except ValueError as exc:
            raise PackError(f"{source}: {exc}") from exc
    if len(set(dimensions)) != len(dimensions):
        raise PackError(f"{source}: duplicate dimension in {dimensions}")
    for spec in outputs_raw:
        _validate_output(spec, questions, source)
    state_needs = _tuple(raw.get("state"), "state") or ("parent",)
    for need in state_needs:
        if need not in {"parent", "children", "open_blockers"}:
            raise PackError(f"{source}: state needs must be parent, children or open_blockers")
    return Pack(
        id=pack_id,
        version=version,
        description=str(raw.get("description") or "").strip(),
        questions=questions,
        dimensions=tuple(dimensions),
        applies_to=applies,
        requires=_tuple(raw.get("requires"), "requires"),
        thresholds=dict(thresholds),
        outputs=tuple(dict(o) for o in outputs_raw),
        deriver=deriver,
        state_needs=state_needs,
        source=source,
    )


def _validate_output(
    spec: Mapping[str, Any], questions: Mapping[str, Question], source: str
) -> None:
    dim = spec["dimension"]
    if "composite" in spec:
        comp = spec["composite"]
        if not isinstance(comp, Mapping) or not isinstance(comp.get("inputs"), Mapping):
            raise PackError(f"{source}: output {dim}: composite needs an inputs map")
        for qid, cfg in comp["inputs"].items():
            q = questions.get(qid)
            if q is None or q.type not in {"score", "noul"}:
                raise PackError(
                    f"{source}: output {dim}: composite input {qid} must be a score or noul"
                )
            weight = (cfg or {}).get("weight", 1.0) if isinstance(cfg, Mapping) else cfg
            try:
                if float(weight) <= 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise PackError(f"{source}: output {dim}: weight for {qid} must be > 0") from exc
        buckets = comp.get("buckets")
        if not isinstance(buckets, list) or not buckets:
            raise PackError(f"{source}: output {dim}: composite needs buckets")
        for b in buckets:
            if not isinstance(b, Mapping) or "value" not in b:
                raise PackError(f"{source}: output {dim}: each bucket needs a value")
            validate_value(str(b["value"]))
        return
    qid = spec.get("from")
    q = questions.get(str(qid)) if qid else None
    if q is None:
        raise PackError(f"{source}: output {dim}: from must name a question")
    if q.type == "score":
        by = spec.get("by", "argmax")
        if by not in {"argmax", "expectation"}:
            raise PackError(f"{source}: output {dim}: by must be argmax or expectation")
        levels = spec.get("levels")
        if by == "argmax":
            if not isinstance(levels, list) or len(levels) != len(q.criteria):
                raise PackError(
                    f"{source}: output {dim}: levels must match the {len(q.criteria)} criteria"
                )
            for v in levels:
                validate_value(str(v))
        else:
            buckets = spec.get("buckets")
            if not isinstance(buckets, list) or not buckets:
                raise PackError(f"{source}: output {dim}: expectation needs buckets")
    elif q.type == "noul":
        for key in ("yes_above", "no_below"):
            if key in spec:
                float(spec[key])
        for v in (spec.get("values") or {}).values():
            validate_value(str(v))
    elif q.type == "choice":
        for v in (spec.get("rename") or {}).values():
            validate_value(str(v))
    if spec.get("uncertain") is not None:
        validate_value(str(spec["uncertain"]))


def load_pack_text(text: str, *, source: str) -> Pack:
    return parse_pack(_read_yaml(text, source), source=source)


def load_pack_file(path: Path) -> Pack:
    path = Path(path)
    if not path.exists():
        raise PackError(f"pack file not found: {path}")
    return load_pack_text(path.read_text(encoding="utf-8"), source=str(path))


def builtin_pack_text(pack_id: str) -> str:
    try:
        return (resources.files("beadsort.packs.builtin") / f"{pack_id}.yaml").read_text(
            encoding="utf-8"
        )
    except FileNotFoundError as exc:
        raise PackError(f"no built-in pack named {pack_id!r}") from exc


def find_pack(ref: PackRef, config: BeadsortConfig) -> Pack:
    """Search order: explicit path, `.beadsort/packs/<id>.yaml`, user dir, built-in."""
    if ref.path:
        path = Path(ref.path)
        if not path.is_absolute():
            path = config.repo / path
        return load_pack_file(path)
    assert ref.id is not None
    candidates = [
        config.packs_dir / f"{ref.id}.yaml",
        Path.home() / ".config" / "beadsort" / "packs" / f"{ref.id}.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return load_pack_file(candidate)
    if ref.id in BUILTIN_IDS:
        return load_pack_text(builtin_pack_text(ref.id), source=f"builtin:{ref.id}")
    raise PackError(
        f"pack {ref.id!r} not found. Looked in {candidates[0]}, {candidates[1]} and built-ins "
        f"{BUILTIN_IDS}."
    )


def load_enabled_packs(
    config: BeadsortConfig, only: Iterable[str] | None = None
) -> tuple[list[ResolvedPack], list[str]]:
    """Resolve every enabled pack. Returns (packs, warnings). Duplicate dimensions are an error."""
    wanted = set(only) if only else None
    resolved: list[ResolvedPack] = []
    warnings: list[str] = []
    owners: dict[str, str] = {}
    for ref in config.packs:
        if wanted is not None and ref.key not in wanted:
            continue
        pack = find_pack(ref, config)
        missing = pack.missing_requirements(config)
        if missing:
            warnings.append(
                f"pack {pack.id} skipped: config is missing {', '.join(missing)} "
                f"(run `beadsort config init` and fill it in)"
            )
            continue
        for dim in pack.dimensions:
            if dim in owners:
                raise PackError(f"dimension {dim!r} is owned by both {owners[dim]} and {pack.id}")
            owners[dim] = pack.id
        resolved.append(resolve_pack(pack, config, ref))
    if wanted is not None:
        unknown = wanted - {ref.key for ref in config.packs}
        if unknown:
            raise PackError(f"packs not enabled in config: {sorted(unknown)}")
    return resolved, warnings


# ---- resolution ------------------------------------------------------------------------


def _substitute(value: Any, owner: str) -> Any:
    if isinstance(value, str):
        return _OWNER_RE.sub(owner, value)
    if isinstance(value, Mapping):
        return {k: _substitute(v, owner) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, owner) for v in value]
    return value


def resolve_pack(pack: Pack, config: BeadsortConfig, ref: PackRef | None = None) -> ResolvedPack:
    questions: dict[str, dict[str, Any]] = {}
    for qid, q in pack.questions.items():
        if q.when == "people" and not config.people:
            continue
        if q.when == "repos" and not config.repos:
            continue
        spec: dict[str, Any] = {
            "type": q.type,
            "instructions": _substitute(copy.deepcopy(q.instructions), config.owner),
        }
        criteria = copy.deepcopy(q.criteria)
        if q.criteria_from == "people":
            injected = {slug: person.describe() for slug, person in config.people.items()}
            criteria = {**injected, **(criteria or {})}
        elif q.criteria_from == "repos":
            injected = {name: desc for name, desc in config.repos.items()}
            base = dict(criteria or {})
            head = {k: v for k, v in base.items() if k == "this_repo"}
            tail = {k: v for k, v in base.items() if k != "this_repo"}
            criteria = {**head, **injected, **tail}
        if criteria is not None:
            spec["criteria"] = _substitute(criteria, config.owner)
        questions[qid] = spec
    thresholds = dict(pack.thresholds)
    if ref is not None:
        thresholds.update(ref.thresholds)
    return ResolvedPack(pack=pack, questions=questions, thresholds=thresholds)


def load_deriver(pack: Pack) -> ModuleType:
    assert pack.deriver
    try:
        return importlib.import_module(pack.deriver)
    except ImportError as exc:
        raise PackError(f"pack {pack.id}: cannot import deriver {pack.deriver}") from exc


def derive(resolved: ResolvedPack, answers: Answers, ctx: DeriveContext) -> Verdict:
    """Run the pack's logic over answers. Ensures every owned dimension appears in labels."""
    pack = resolved.pack
    if pack.deriver:
        module = load_deriver(pack)
        verdict = module.derive(answers, resolved.thresholds, ctx)
    else:
        from beadsort.packs.declarative import derive_declarative

        verdict = derive_declarative(resolved, answers)
    for dim in pack.dimensions:
        verdict.labels.setdefault(dim, None)
    for value in verdict.labels.values():
        if value is not None:
            validate_value(value)
    return verdict


def precheck(resolved: ResolvedPack, ctx: DeriveContext) -> Verdict | None:
    """A deriver may decide a bead without the model (e.g. blocked by an open dependency)."""
    pack = resolved.pack
    if not pack.deriver:
        return None
    module = load_deriver(pack)
    hook = getattr(module, "precheck", None)
    if hook is None:
        return None
    verdict = hook(resolved.thresholds, ctx)
    if verdict is not None:
        for dim in pack.dimensions:
            verdict.labels.setdefault(dim, None)
    return verdict

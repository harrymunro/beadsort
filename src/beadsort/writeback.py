"""Turn verdicts into `bd update` calls, safely.

The fence: beadsort only ever removes a label it wrote itself. It knows what it wrote from
the cache. A `dim:value` label it did not write is "foreign" and is left alone, and the
dimension is skipped for that bead, unless `--force`. A second `--apply` with nothing
changed issues no bd writes at all.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from beadsort import __version__
from beadsort.bd import BdClient, BdError
from beadsort.beads import labels_for_dimension
from beadsort.cache import CacheStore, now_iso
from beadsort.engine import BeadResult

METADATA_KEY = "beadsort"
METADATA_VERSION = 1


@dataclass
class WritePlan:
    bead_id: str
    title: str
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    respected: list[tuple[str, list[str]]] = field(default_factory=list)  # (dim, foreign labels)
    written_after: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] | None = None
    metadata_kv: list[tuple[str, str]] = field(default_factory=list)
    fresh_packs: list[str] = field(default_factory=list)
    audit: dict[str, Any] | None = None

    @property
    def is_noop(self) -> bool:
        return not self.add and not self.remove and self.metadata is None and not self.metadata_kv

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.bead_id,
            "title": self.title,
            "add": list(self.add),
            "remove": list(self.remove),
            "respected": [{"dimension": d, "labels": ls} for d, ls in self.respected],
            "metadata": self.metadata is not None or bool(self.metadata_kv),
            "noop": self.is_noop,
        }


@dataclass
class ApplyReport:
    applied: list[str] = field(default_factory=list)
    noop: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    writes: int = 0
    audits: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": list(self.applied),
            "noop": list(self.noop),
            "failed": [{"id": i, "error": e} for i, e in self.failed],
            "writes": self.writes,
            "audits": self.audits,
        }


def _compact_answer(answer: Mapping[str, Any]) -> dict[str, Any]:
    kind = answer.get("type")
    if kind == "noul":
        return {"p": answer.get("noul")}
    if kind == "choice":
        probs = answer.get("probabilities") or {}
        top = probs.get(answer.get("choice"), None)
        return {"choice": answer.get("choice"), "p": top, "conf": answer.get("confidence")}
    if kind == "score":
        return {"score": answer.get("score"), "conf": answer.get("confidence")}
    return {}


def build_metadata(result: BeadResult, cache: CacheStore) -> dict[str, Any]:
    """The nested object written under the single top-level `beadsort` key."""
    body: dict[str, Any] = {"v": METADATA_VERSION, "at": now_iso(), "tool": __version__}
    for pack_id, verdict in result.verdicts.items():
        entry = cache.pack_entry(result.bead.id, pack_id) or {}
        pack_meta: dict[str, Any] = {
            "pack_version": entry.get("pack_version"),
            "model": entry.get("model"),
            "key": entry.get("call_key"),
            "labels": {k: v for k, v in verdict.labels.items() if v is not None},
        }
        for qid, answer in (entry.get("answers") or {}).items():
            pack_meta[qid] = _compact_answer(answer)
        if verdict.meta.get("confidence") is not None:
            pack_meta["confidence"] = verdict.meta["confidence"]
        body[pack_id] = pack_meta
    return {METADATA_KEY: body}


def flatten_metadata(meta: Mapping[str, Any]) -> list[tuple[str, str]]:
    """`kv` capability: `beadsort.triage.blocking_party=named_person` style pairs."""
    pairs: list[tuple[str, str]] = []
    body = meta.get(METADATA_KEY) or {}
    pairs.append((f"{METADATA_KEY}.at", str(body.get("at", ""))))
    for pack_id, pack_meta in body.items():
        if not isinstance(pack_meta, Mapping):
            continue
        for qid, value in pack_meta.items():
            if qid in {"labels", "pack_version", "model", "key"}:
                continue
            if isinstance(value, Mapping):
                primary = value.get("choice", value.get("score", value.get("p")))
                if primary is not None:
                    pairs.append((f"{METADATA_KEY}.{pack_id}.{qid}", str(primary)))
                if value.get("conf") is not None:
                    pairs.append((f"{METADATA_KEY}.{pack_id}.{qid}.conf", str(value["conf"])))
            elif value is not None:
                pairs.append((f"{METADATA_KEY}.{pack_id}.{qid}", str(value)))
    return pairs


def plan_bead(
    result: BeadResult,
    *,
    cache: CacheStore,
    force: bool = False,
    capability: str = "json",
    metadata_changed: bool = True,
) -> WritePlan:
    bead = result.bead
    plan = WritePlan(bead_id=bead.id, title=bead.title, fresh_packs=list(result.packs_fresh))
    existing = set(bead.labels)
    prev_written = cache.written_labels(bead.id)
    desired = result.labels

    # Dimensions no pack decided this run keep their record: a label beadsort wrote for
    # a pack that is not enabled today is still beadsort's to replace tomorrow.
    plan.written_after = {d: v for d, v in prev_written.items() if d not in desired}

    for dim, value in sorted(desired.items()):
        current = labels_for_dimension(existing, dim)
        owned = {f"{dim}:{prev_written[dim]}"} & current if dim in prev_written else set()
        foreign = current - owned
        if foreign and not force:
            plan.respected.append((dim, sorted(foreign)))
            if owned:
                plan.written_after[dim] = prev_written[dim]
            continue
        target = f"{dim}:{value}" if value else None
        add = ({target} - current) if target else set()
        remove = (owned | (foreign if force else set())) - ({target} if target else set())
        plan.add.extend(sorted(add))
        plan.remove.extend(sorted(remove))
        if value:
            plan.written_after[dim] = value

    wants_metadata = result.error is None and result.verdicts and capability in {"json", "kv"}
    if wants_metadata and (metadata_changed or not cache.metadata_written(bead.id)):
        meta = build_metadata(result, cache)
        if capability == "json":
            # Some bd versions replace the whole metadata object on `--metadata`. Re-send
            # every key that is not ours so nothing anyone else stored is lost.
            others = {k: v for k, v in bead.metadata.items() if k != METADATA_KEY}
            plan.metadata = {**others, **meta}
        else:
            plan.metadata_kv = flatten_metadata(meta)

    if plan.fresh_packs:
        plan.audit = {
            "packs": [
                {
                    "id": p,
                    "version": (cache.pack_entry(bead.id, p) or {}).get("pack_version"),
                    "key": (cache.pack_entry(bead.id, p) or {}).get("call_key"),
                    "request_id": (cache.pack_entry(bead.id, p) or {}).get("request_id"),
                    "input_tokens": ((cache.pack_entry(bead.id, p) or {}).get("usage") or {}).get(
                        "input_tokens"
                    ),
                }
                for p in plan.fresh_packs
            ],
            "labels_added": list(plan.add),
            "labels_removed": list(plan.remove),
            "tool": f"beadsort {__version__}",
        }
    return plan


def plan_all(
    results: Iterable[BeadResult],
    *,
    cache: CacheStore,
    force: bool = False,
    capability: str = "json",
) -> list[WritePlan]:
    plans: list[WritePlan] = []
    for result in sorted(results, key=lambda r: r.bead.id):
        if result.error is not None and not result.verdicts:
            continue
        plans.append(
            plan_bead(
                result,
                cache=cache,
                force=force,
                capability=capability,
                metadata_changed=bool(result.packs_fresh),
            )
        )
    return plans


def apply(
    plans: Iterable[WritePlan],
    *,
    bd: BdClient,
    cache: CacheStore,
    model: str,
    audit: bool = True,
) -> ApplyReport:
    """Execute plans in id order. One failure is recorded and the loop continues."""
    report = ApplyReport()
    for plan in plans:
        if plan.is_noop:
            report.noop.append(plan.bead_id)
            continue
        try:
            bd.update(
                plan.bead_id,
                add_labels=plan.add,
                remove_labels=plan.remove,
                metadata_json=plan.metadata,
                set_metadata=plan.metadata_kv,
            )
        except BdError as exc:
            report.failed.append((plan.bead_id, f"{exc.code}: {exc.message}"))
            continue
        report.writes += 1
        report.applied.append(plan.bead_id)
        cache.set_written_labels(plan.bead_id, plan.written_after)
        if plan.metadata is not None or plan.metadata_kv:
            cache.set_metadata_written(plan.bead_id, True)
        if audit and plan.audit:
            try:
                bd.audit_record(
                    issue_id=plan.bead_id,
                    model=model,
                    prompt=json.dumps({"packs": plan.audit["packs"]}, separators=(",", ":")),
                    response=json.dumps(
                        {
                            "labels_added": plan.audit["labels_added"],
                            "labels_removed": plan.audit["labels_removed"],
                            "tool": plan.audit["tool"],
                        },
                        separators=(",", ":"),
                    ),
                )
                report.audits += 1
            except BdError as exc:
                report.failed.append((plan.bead_id, f"audit: {exc.code}: {exc.message}"))
    return report

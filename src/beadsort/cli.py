"""Command line entry point. Owns exit codes and the JSON/human switch; no logic lives here."""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from beadsort import __version__
from beadsort.bd import BdClient
from beadsort.beads import Bead, BeadIndex, split_label
from beadsort.cache import CacheStore
from beadsort.config import (
    BeadsortConfig,
    load_config,
    resolve_api_key,
    write_template,
)
from beadsort.discover import discover, find_repo
from beadsort.engine import (
    QUESTION_SEP,
    STATE_NEEDS,
    BeadResult,
    RunReport,
    TypeSafeJudge,
    run,
    select_beads,
)
from beadsort.errors import ApiError, BeadsortError, PartialApplyError, UsageError
from beadsort.output import Emitter, render_table
from beadsort.packs import (
    BUILTIN_IDS,
    ResolvedPack,
    builtin_pack_text,
    load_enabled_packs,
    load_pack_file,
    load_pack_text,
    resolve_pack,
)
from beadsort.state import build_state

KEY_HELP = (
    "No API key. Get one at https://console.typesafe.ai/settings/keys, then "
    "`export TYPESAFE_API_KEY=...` (or `bd config set custom.beadsort.api_key ...`)."
)


@dataclass
class Session:
    cwd: Path
    emitter: Emitter
    bd_bin: str = "bd"

    def repo(self) -> Path:
        repo = find_repo(self.cwd)
        if repo is None:
            raise UsageError(f"no beads repo found at or above {self.cwd} (looking for .beads/)")
        return repo

    def repos(self, root: Path | None, max_depth: int) -> list[Path]:
        if root is None:
            return [self.repo()]
        found = discover(root, max_depth)
        if not found:
            raise UsageError(f"no .beads/ directories found under {root}")
        return found


def guard(command: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Turn BeadsortError into the envelope + exit code; everything else is a real bug."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @click.option(
            "-C",
            "--directory",
            "directory",
            type=click.Path(file_okay=False, path_type=Path),
            default=None,
            help="Run as if started in this directory.",
        )
        @click.option("--json", "json_mode", is_flag=True, help="Machine-readable envelope.")
        @click.option("-q", "--quiet", is_flag=True, help="Only errors.")
        @functools.wraps(fn)
        @click.pass_obj
        def wrapper(
            session: Session,
            *args: Any,
            directory: Path | None = None,
            json_mode: bool = False,
            quiet: bool = False,
            **kwargs: Any,
        ) -> Any:
            if directory is not None:
                session.cwd = directory.resolve()
            if json_mode:
                session.emitter.json_mode = True
            if quiet:
                session.emitter.quiet = True
            try:
                return fn(session, *args, **kwargs)
            except BeadsortError as exc:
                sys.exit(session.emitter.fail(command, exc))

        return wrapper

    return decorator


@click.group()
@click.option(
    "-C",
    "--directory",
    "directory",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Run as if started in this directory.",
)
@click.option("--json", "json_mode", is_flag=True, help="Machine-readable envelope on stdout.")
@click.option("-q", "--quiet", is_flag=True, help="Only errors.")
@click.option("--bd", "bd_bin", default="bd", show_default=True, help="bd binary to use.")
@click.version_option(__version__, prog_name="beadsort")
@click.pass_context
def main(
    ctx: click.Context, directory: Path | None, json_mode: bool, quiet: bool, bd_bin: str
) -> None:
    """beadsort: typed, calibrated labels for your beads backlog."""
    ctx.obj = Session(
        cwd=(directory or Path.cwd()).resolve(),
        emitter=Emitter(json_mode=json_mode, quiet=quiet),
        bd_bin=bd_bin,
    )


# ---- shared plumbing -------------------------------------------------------------------


@dataclass
class Loaded:
    repo: Path
    config: BeadsortConfig
    bd: BdClient
    index: BeadIndex
    cache: CacheStore
    packs: list[ResolvedPack]
    warnings: list[str]


def _load(session: Session, repo: Path, only_packs: tuple[str, ...] = ()) -> Loaded:
    config = load_config(repo)
    bd = BdClient(repo, bd_bin=session.bd_bin)
    packs, warnings = load_enabled_packs(config, only=only_packs or None)
    records = bd.export()
    index = BeadIndex((Bead.from_record(r) for r in records), done_statuses=config.done_statuses)
    cache = CacheStore(config.cache_path).load()
    cache.drop_missing(set(index.beads))
    for warning in [*config.warnings, *warnings]:
        session.emitter.note(f"warning [{repo.name}]: {warning}")
    return Loaded(repo, config, bd, index, cache, packs, list(warnings))


def _judge(session: Session, loaded: Loaded, model: str, offline: bool) -> TypeSafeJudge | None:
    if offline:
        return None
    key, _source = resolve_api_key(loaded.bd)
    if not key:
        return None
    return TypeSafeJudge(key, model=model)


def _short(text: str, width: int = 44) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _current_labels(bead: Bead, dims: list[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for label in bead.labels:
        parsed = split_label(label)
        if parsed and parsed[0] in dims:
            out[parsed[0]] = parsed[1]
    return out


def _result_dict(result: BeadResult, dims: list[str]) -> dict[str, Any]:
    return {
        "id": result.bead.id,
        "title": result.bead.title,
        "status": result.bead.status,
        "type": result.bead.issue_type,
        "priority": result.bead.priority,
        "labels": result.labels,
        "current": _current_labels(result.bead, dims),
        "review": result.review,
        "uncertain": result.uncertain,
        "packs_applied": result.packs_applied,
        "packs_fresh": result.packs_fresh,
        "packs_prechecked": result.packs_prechecked,
        "state_chars": result.state_chars,
        "error": result.error,
        "meta": {p: v.meta for p, v in result.verdicts.items()},
    }


def _plan_rows(results: list[BeadResult], dims: list[str]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for result in results:
        current = _current_labels(result.bead, dims)
        if result.error:
            rows.append([result.bead.id, _short(result.bead.title), "-", "error", result.error])
            continue
        for dim in dims:
            if dim not in result.labels:
                continue
            proposed = result.labels[dim]
            now = current.get(dim)
            if proposed == now:
                continue
            note = "unsure" if dim in result.uncertain else ""
            rows.append(
                [
                    result.bead.id,
                    _short(result.bead.title),
                    dim,
                    f"{now or '-'} -> {proposed or '-'}",
                    note,
                ]
            )
    return rows


def _summary_line(report: RunReport) -> str:
    return (
        f"{report.selected} bead(s), {report.calls} model call(s), {report.cached} cached, "
        f"{report.input_tokens} input tokens (~${report.estimated_cost_usd:.4f}), "
        f"{report.seconds:.1f}s"
    )


# ---- run -------------------------------------------------------------------------------


@main.command()
@click.option(
    "--apply", "do_apply", is_flag=True, help="Write labels and metadata back into beads."
)
@click.option("--pack", "packs_only", multiple=True, help="Only these packs.")
@click.option("--only", multiple=True, help="Only these bead ids.")
@click.option(
    "--status", "statuses", multiple=True, help="Statuses to select (default from config)."
)
@click.option("--type", "types", multiple=True, help="Issue types to select.")
@click.option("--limit", type=int, default=None, help="At most this many beads.")
@click.option(
    "--force", is_flag=True, help="Re-ask the model and replace foreign dim:value labels."
)
@click.option("--workers", type=int, default=None, help="Parallel model calls.")
@click.option("--model", default=None, help="Model name or alias.")
@click.option(
    "--root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Run over every repo under this directory.",
)
@click.option("--max-depth", type=int, default=4, show_default=True)
@click.option("--offline", is_flag=True, help="Use cached answers only; never call the model.")
@click.option("--no-audit", is_flag=True, help="Skip bd audit records on --apply.")
@guard("run")
def run_cmd(
    session: Session,
    do_apply: bool,
    packs_only: tuple[str, ...],
    only: tuple[str, ...],
    statuses: tuple[str, ...],
    types: tuple[str, ...],
    limit: int | None,
    force: bool,
    workers: int | None,
    model: str | None,
    root: Path | None,
    max_depth: int,
    offline: bool,
    no_audit: bool,
) -> None:
    """Classify beads. Dry run by default: shows what would change. Add --apply to write."""
    from beadsort.doctor import ensure_capability
    from beadsort.writeback import apply as apply_plans
    from beadsort.writeback import plan_all

    emitter = session.emitter
    data: list[dict[str, Any]] = []
    human: list[str] = []
    partial = False
    missing_key = False
    for repo in session.repos(root, max_depth):
        try:
            loaded = _load(session, repo, packs_only)
        except BeadsortError as exc:
            if root is None:
                raise
            data.append({"repo": str(repo), "error": exc.to_dict()})
            human.append(f"== {repo}\nskipped: [{exc.code}] {exc.message}")
            continue
        config = loaded.config
        model_name = model or config.model
        unknown = sorted(i for i in only if i not in loaded.index)
        if unknown:
            emitter.note(f"warning [{repo.name}]: bead(s) not found: {', '.join(unknown)}")
        beads = select_beads(
            loaded.index, config, statuses=statuses, types=types, only=only, limit=limit
        )
        judge = _judge(session, loaded, model_name, offline)
        dims = [d for p in loaded.packs for d in p.pack.dimensions]
        try:
            report = run(
                beads,
                loaded.packs,
                index=loaded.index,
                config=config,
                cache=loaded.cache,
                judge=judge,
                model=model_name,
                workers=workers or config.workers,
                offline=offline,
                force=force,
                progress=emitter.note,
            )
        finally:
            loaded.cache.save()
        if (
            judge is None
            and not offline
            and any(r.error and "offline" in r.error for r in report.results)
        ):
            missing_key = True
        rows = _plan_rows(report.results, dims)
        repo_data: dict[str, Any] = {
            "repo": str(repo),
            "model": report.model,
            "selected": report.selected,
            "calls": report.calls,
            "cached": report.cached,
            "input_tokens": report.input_tokens,
            "estimated_cost_usd": round(report.estimated_cost_usd, 6),
            "seconds": round(report.seconds, 2),
            "changes": len(rows),
            "review": sum(1 for r in report.results if r.review),
            "results": [_result_dict(r, dims) for r in report.results],
        }
        human.append(f"== {repo}")
        if rows:
            human.append(render_table(["bead", "title", "dimension", "change", "note"], rows))
        else:
            human.append("no label changes")
        human.append(_summary_line(report))
        review_count = sum(1 for r in report.results if r.review)
        if review_count:
            human.append(f"{review_count} bead(s) need a human look: `beadsort review`")

        if do_apply:
            capability = ensure_capability(loaded.cache, loaded.bd.version(), bd_bin=session.bd_bin)
            plans = plan_all(report.results, cache=loaded.cache, force=force, capability=capability)
            try:
                applied = apply_plans(
                    plans,
                    bd=loaded.bd,
                    cache=loaded.cache,
                    model=report.model or model_name,
                    audit=config.audit and not no_audit,
                )
            finally:
                loaded.cache.save()
            respected = sum(len(p.respected) for p in plans)
            repo_data["applied"] = {
                **applied.to_dict(),
                "metadata": capability,
                "respected": respected,
                "plans": [p.to_dict() for p in plans],
            }
            human.append(
                f"applied: {applied.writes} bd write(s), {len(applied.noop)} unchanged, "
                f"{applied.audits} audit record(s), metadata={capability}"
                + (f", respected {respected} foreign label(s)" if respected else "")
            )
            for bead_id, error in applied.failed:
                human.append(f"  failed {bead_id}: {error}")
            if applied.failed:
                partial = True
        else:
            human.append("dry run: nothing written. Add --apply to write labels into beads.")
        data.append(repo_data)

    payload = data if root else data[0]
    error: BeadsortError | None = None
    if missing_key:
        error = ApiError(KEY_HELP, code="no_api_key")
    elif partial:
        error = PartialApplyError("some beads failed to apply; see failed list")
    if error is None:
        emitter.result("run", payload, "\n".join(human))
        return
    # One envelope on stdout, never two: the run data rides inside the error envelope.
    if not emitter.json_mode:
        emitter.result("run", payload, "\n".join(human))
    sys.exit(emitter.fail("run", error, data=payload))


# ---- status / review -------------------------------------------------------------------


@main.command()
@click.argument("bead_id", required=False)
@click.option("--root", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--max-depth", type=int, default=4, show_default=True)
@guard("status")
def status(session: Session, bead_id: str | None, root: Path | None, max_depth: int) -> None:
    """What beadsort knows about this repo (or every repo under --root). No network."""
    emitter = session.emitter
    data: list[dict[str, Any]] = []
    human: list[str] = []
    for repo in session.repos(root, max_depth):
        try:
            loaded = _load(session, repo)
        except BeadsortError as exc:
            if root is None:
                raise
            data.append({"repo": str(repo), "error": exc.to_dict()})
            human.append(f"== {repo}\nskipped: [{exc.code}] {exc.message}")
            continue
        dims = [d for p in loaded.packs for d in p.pack.dimensions]
        if bead_id:
            bead = loaded.index.get(bead_id)
            if bead is None:
                raise UsageError(f"bead not found: {bead_id}")
            entry = loaded.cache.peek(bead_id) or {}
            item = {
                "repo": str(repo),
                "id": bead.id,
                "title": bead.title,
                "labels": list(bead.labels),
                "current": _current_labels(bead, dims),
                "hash": bead.content_hash(),
                "cached_hash": entry.get("hash"),
                "stale": entry.get("hash") not in (None, bead.content_hash()),
                "written_labels": entry.get("written_labels"),
                "packs": entry.get("packs"),
            }
            data.append(item)
            human.append(f"{bead.id}: {bead.title}")
            human.append(f"  labels: {', '.join(bead.labels) or '-'}")
            human.append(
                f"  cached: {'yes' if entry else 'no'}{' (stale)' if item['stale'] else ''}"
            )
            for pack_id, pack in (entry.get("packs") or {}).items():
                verdict = pack.get("verdict") or {}
                human.append(f"  {pack_id}: {verdict.get('labels')} review={verdict.get('review')}")
            continue
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        dim_counts: dict[str, dict[str, int]] = {d: {} for d in dims}
        stale = 0
        uncertain = 0
        for bead in loaded.index.beads.values():
            by_status[bead.status] = by_status.get(bead.status, 0) + 1
            by_type[bead.issue_type] = by_type.get(bead.issue_type, 0) + 1
            for label in bead.labels:
                parsed = split_label(label)
                if parsed and parsed[0] in dim_counts:
                    dim_counts[parsed[0]][parsed[1]] = dim_counts[parsed[0]].get(parsed[1], 0) + 1
            entry = loaded.cache.peek(bead.id)
            if entry:
                if entry.get("hash") not in (None, bead.content_hash()):
                    stale += 1
                if any(
                    (p.get("verdict") or {}).get("uncertain")
                    for p in entry.get("packs", {}).values()
                ):
                    uncertain += 1
        item = {
            "repo": str(repo),
            "beads": len(loaded.index),
            "by_status": by_status,
            "by_type": by_type,
            "packs": [p.id for p in loaded.packs],
            "dimensions": dim_counts,
            "cached": len(loaded.cache.beads),
            "stale": stale,
            "uncertain": uncertain,
            "last_run": loaded.cache.last_run,
            "capabilities": loaded.cache.capabilities,
            "config": str(loaded.config.path) if loaded.config.path else None,
        }
        data.append(item)
        human.append(f"== {repo}")
        human.append(
            f"{len(loaded.index)} beads: "
            + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items()))
        )
        human.append(f"packs: {', '.join(p.id for p in loaded.packs) or 'none'}")
        for dim, counts in dim_counts.items():
            if counts:
                human.append(
                    f"  {dim}: " + ", ".join(f"{v} {n}" for v, n in sorted(counts.items()))
                )
        last = loaded.cache.last_run
        last_text = (
            f"; last run {last.get('at')} ({last.get('calls')} calls)" if last else "; never run"
        )
        human.append(
            f"cache: {len(loaded.cache.beads)} bead(s), {stale} stale, "
            f"{uncertain} with unsure answers{last_text}"
        )
        caps = loaded.cache.capabilities
        if caps.get("metadata"):
            human.append(f"metadata: {caps['metadata']} (bd {caps.get('checked_bd')})")
    emitter.result("status", data if root or bead_id else data[0], "\n".join(human))


@main.command()
@click.option("--pack", "packs_only", multiple=True)
@click.option("--dimension", default=None, help="Only beads unsure on this dimension.")
@guard("review")
def review(session: Session, packs_only: tuple[str, ...], dimension: str | None) -> None:
    """Beads that need a human look, with the reasons. Reads the cache; no network."""
    loaded = _load(session, session.repo())
    wanted = set(packs_only) or None
    items: list[dict[str, Any]] = []
    for bead in loaded.index.beads.values():
        entry = loaded.cache.peek(bead.id)
        if not entry or bead.status in loaded.config.done_statuses:
            continue
        reasons: list[str] = []
        unsure: list[str] = []
        for pack_id, pack in (entry.get("packs") or {}).items():
            if wanted and pack_id not in wanted:
                continue
            verdict = pack.get("verdict") or {}
            reasons.extend(f"{pack_id}: {r}" for r in verdict.get("review") or [])
            unsure.extend(verdict.get("uncertain") or [])
        if dimension and dimension not in unsure:
            continue
        if reasons:
            items.append(
                {
                    "id": bead.id,
                    "title": bead.title,
                    "priority": bead.priority,
                    "updated_at": bead.updated_at,
                    "unsure": unsure,
                    "reasons": reasons,
                }
            )
    items.sort(
        key=lambda i: (i["priority"] if i["priority"] is not None else 9, i["updated_at"] or "")
    )
    rows = [
        [
            i["id"],
            f"P{i['priority']}" if i["priority"] is not None else "-",
            _short(i["title"], 36),
            "; ".join(i["reasons"]),
        ]
        for i in items
    ]
    human = render_table(["bead", "pri", "title", "why"], rows) if rows else "nothing to review"
    session.emitter.result(
        "review", {"repo": str(loaded.repo), "count": len(items), "items": items}, human
    )


# ---- eval ------------------------------------------------------------------------------


@main.command("eval")
@click.option("--pack", "pack_id", required=True)
@click.option("--dimension", required=True)
@click.option("--truth-label", default=None, help="Presence of this label means yes.")
@click.option("--truth-prefix", default=None, help="Value after this label prefix is the truth.")
@click.option("--truth-metadata", default=None, help="Metadata key holding the truth.")
@click.option(
    "--truth-json",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="JSON file of bead id -> value.",
)
@click.option(
    "--positive", default=None, help="Comma-separated values counted as yes (collapses to yes/no)."
)
@click.option(
    "--status",
    "statuses",
    multiple=True,
    help="Statuses to include (default: every status in the truth).",
)
@click.option("--bands", default="0.5,0.7,0.9", show_default=True)
@click.option("--offline", is_flag=True)
@click.option("--model", default=None)
@guard("eval")
def eval_cmd(
    session: Session,
    pack_id: str,
    dimension: str,
    truth_label: str | None,
    truth_prefix: str | None,
    truth_metadata: str | None,
    truth_json: Path | None,
    positive: str | None,
    statuses: tuple[str, ...],
    bands: str,
    offline: bool,
    model: str | None,
) -> None:
    """Score one dimension against ground truth you already have. For your eyes only."""
    from beadsort.eval import (
        evaluate,
        truth_from_json,
        truth_from_label,
        truth_from_metadata,
        truth_from_prefix,
    )

    sources = [s for s in (truth_label, truth_prefix, truth_metadata, truth_json) if s]
    if len(sources) != 1:
        raise UsageError(
            "give exactly one of --truth-label, --truth-prefix, --truth-metadata, --truth-json"
        )
    loaded = _load(session, session.repo(), (pack_id,))
    pack = next((p for p in loaded.packs if p.id == pack_id), None)
    if pack is None:
        raise UsageError(f"pack {pack_id} is not enabled or was skipped")
    if dimension not in pack.pack.dimensions:
        raise UsageError(
            f"pack {pack_id} does not own dimension {dimension}; it owns {pack.pack.dimensions}"
        )
    beads_all = list(loaded.index.beads.values())
    if truth_label:
        truth = truth_from_label(beads_all, truth_label)
    elif truth_prefix:
        truth = truth_from_prefix(beads_all, truth_prefix)
    elif truth_metadata:
        truth = truth_from_metadata(beads_all, truth_metadata)
    else:
        assert truth_json is not None
        truth = truth_from_json(truth_json)
    ids = [i for i in truth if i in loaded.index]
    if statuses:
        ids = [i for i in ids if loaded.index.beads[i].status in statuses]
    if not ids:
        raise UsageError("no beads with ground truth found in this repo")
    config = loaded.config
    model_name = model or config.model
    judge = _judge(session, loaded, model_name, offline)
    try:
        report = run(
            [loaded.index.beads[i] for i in ids],
            [pack],
            index=loaded.index,
            config=config,
            cache=loaded.cache,
            judge=judge,
            model=model_name,
            workers=config.workers,
            offline=offline,
            progress=session.emitter.note,
        )
    finally:
        loaded.cache.save()
    if (
        judge is None
        and not offline
        and any(r.error and "offline" in r.error for r in report.results)
    ):
        raise ApiError(KEY_HELP, code="no_api_key")
    predictions = {r.bead.id: r.labels.get(dimension) for r in report.results if not r.error}
    confidences = {
        r.bead.id: (
            r.verdicts.get(pack_id).meta.get("confidence") if r.verdicts.get(pack_id) else None
        )
        for r in report.results
    }
    result = evaluate(
        dimension,
        predictions,
        {i: v for i, v in truth.items() if i in predictions},
        confidences={k: v for k, v in confidences.items() if v is not None},
        positive=[p.strip() for p in positive.split(",")] if positive is not None else None,
        bands=[float(b) for b in bands.split(",") if b.strip()],
        titles={b.id: b.title for b in beads_all},
    )
    lines = [
        f"{dimension}: {result.covered}/{result.total} covered ({result.coverage:.0%}), "
        f"accuracy {result.accuracy:.0%}, macro F1 {result.macro_f1:.2f}",
        render_table(
            ["value", "precision", "recall", "f1", "support"],
            [
                [v, m["precision"], m["recall"], m["f1"], m["support"]]
                for v, m in result.per_value.items()
            ],
        ),
    ]
    if result.bands:
        lines.append(
            render_table(
                ["confidence", "n", "accuracy"],
                [[b["band"], b["n"], b["accuracy"]] for b in result.bands],
            )
        )
    if result.disagreements:
        lines.append("disagreements (highest confidence first):")
        lines.append(
            render_table(
                ["bead", "expected", "predicted", "conf", "title"],
                [
                    [
                        d["id"],
                        d["expected"],
                        d["predicted"],
                        d["confidence"],
                        _short(d["title"], 40),
                    ]
                    for d in result.disagreements[:25]
                ],
            )
        )
    lines.append(_summary_line(report))
    session.emitter.result(
        "eval", {"repo": str(loaded.repo), "pack": pack_id, **result.to_dict()}, "\n".join(lines)
    )


# ---- share / packs / config / doctor ---------------------------------------------------


@main.command()
@click.argument("bead_id")
@click.option("--pack", "pack_id", default=None, help="One pack (default: every enabled pack).")
@click.option("--model", default=None)
@guard("share")
def share(session: Session, bead_id: str, pack_id: str | None, model: str | None) -> None:
    """Print a playground link with the exact state and questions beadsort would send."""
    from beadsort.share import playground_link

    loaded = _load(session, session.repo(), (pack_id,) if pack_id else ())
    bead = loaded.index.get(bead_id)
    if bead is None:
        raise UsageError(f"bead not found: {bead_id}")
    state = build_state(bead, loaded.index, loaded.config, STATE_NEEDS)
    questions: dict[str, Any] = {}
    for pack in loaded.packs:
        for qid, spec in pack.questions.items():
            questions[f"{pack.id}{QUESTION_SEP}{qid}"] = spec
    url = playground_link(state, questions, model or loaded.config.model)
    session.emitter.note("note: the link embeds the bead text; share it only where that is fine")
    session.emitter.result(
        "share",
        {"id": bead_id, "url": url, "state_chars": len(str(state)), "questions": len(questions)},
        url,
    )


@main.group()
def packs() -> None:
    """List, inspect and validate question packs."""


@packs.command("list")
@guard("packs list")
def packs_list(session: Session) -> None:
    """Enabled packs for this repo, and the built-ins."""
    repo = find_repo(session.cwd)
    config = load_config(repo) if repo else BeadsortConfig(repo=session.cwd)
    enabled, warnings = load_enabled_packs(config)
    items = [
        {
            "id": p.id,
            "version": p.version,
            "dimensions": list(p.pack.dimensions),
            "source": p.pack.source,
            "questions": len(p.questions),
        }
        for p in enabled
    ]
    rows = [
        [i["id"], i["version"], ", ".join(i["dimensions"]), i["questions"], i["source"]]
        for i in items
    ]
    human = render_table(["pack", "ver", "dimensions", "questions", "source"], rows)
    if warnings:
        human += "\n" + "\n".join(f"warning: {w}" for w in warnings)
    human += f"\nbuilt-ins: {', '.join(BUILTIN_IDS)}"
    session.emitter.result(
        "packs list", {"packs": items, "warnings": warnings, "builtins": list(BUILTIN_IDS)}, human
    )


@packs.command("show")
@click.argument("pack_id")
@guard("packs show")
def packs_show(session: Session, pack_id: str) -> None:
    """The resolved questions of a pack, after config is applied."""
    import json

    repo = find_repo(session.cwd)
    config = load_config(repo) if repo else BeadsortConfig(repo=session.cwd)
    path = Path(pack_id)
    if path.suffix == ".yaml" and path.exists():
        pack = load_pack_file(path)
    else:
        from beadsort.config import PackRef
        from beadsort.packs import find_pack

        pack = find_pack(PackRef(id=pack_id), config)
    resolved = resolve_pack(pack, config)
    data = {
        "id": pack.id,
        "version": pack.version,
        "description": pack.description,
        "dimensions": list(pack.dimensions),
        "applies_to": pack.applies_to.__dict__,
        "requires": list(pack.requires),
        "thresholds": resolved.thresholds,
        "questions": resolved.questions,
    }
    session.emitter.result("packs show", data, json.dumps(data, indent=2, ensure_ascii=False))


@packs.command("validate")
@click.argument("path", type=click.Path(dir_okay=False, exists=True, path_type=Path))
@guard("packs validate")
def packs_validate(session: Session, path: Path) -> None:
    """Check a pack file. Exit 0 when it is valid."""
    pack = load_pack_file(path)
    session.emitter.result(
        "packs validate",
        {
            "path": str(path),
            "id": pack.id,
            "version": pack.version,
            "dimensions": list(pack.dimensions),
            "questions": list(pack.questions),
        },
        f"{path}: ok ({pack.id} v{pack.version}, {len(pack.questions)} questions, "
        f"dimensions {', '.join(pack.dimensions)})",
    )


@main.group()
def config() -> None:
    """Configuration helpers."""


@config.command("init")
@click.option("--force", is_flag=True, help="Overwrite an existing config.")
@guard("config init")
def config_init(session: Session, force: bool) -> None:
    """Write a commented .beadsort/config.yaml and a .beadsort/.gitignore."""
    repo = session.repo()
    config_path, ignore_path = write_template(repo, force=force)
    session.emitter.result(
        "config init",
        {"config": str(config_path), "gitignore": str(ignore_path)},
        f"wrote {config_path}\nwrote {ignore_path}\n"
        "next: fill in owner, project and people, then `beadsort run`",
    )


@main.command()
@click.option("--probe", is_flag=True, help="Test metadata round-trip in a scratch repo.")
@click.option("--online", is_flag=True, help="Also check the API is reachable with your key.")
@guard("doctor")
def doctor(session: Session, probe: bool, online: bool) -> None:
    """Check bd, the repo, config, packs, the API key and metadata support."""
    from beadsort.doctor import run_checks

    repo = find_repo(session.cwd)
    cfg = load_config(repo) if repo else None
    checks = run_checks(repo, cfg, bd_bin=session.bd_bin, probe=probe, online=online)
    rows = [["ok" if c.ok else "!!", c.name, c.detail] for c in checks]
    ok = all(c.ok or c.name.endswith("warning") for c in checks)
    session.emitter.result(
        "doctor",
        {"ok": ok, "checks": [c.to_dict() for c in checks]},
        render_table(["", "check", "detail"], rows),
    )
    if not ok:
        sys.exit(1)


@main.command("builtin", hidden=True)
@click.argument("pack_id")
@guard("builtin")
def builtin(session: Session, pack_id: str) -> None:
    """Print a built-in pack's YAML (to copy into .beadsort/packs/ and edit)."""
    text = builtin_pack_text(pack_id)
    load_pack_text(text, source=pack_id)
    session.emitter.result("builtin", {"id": pack_id, "yaml": text}, text)

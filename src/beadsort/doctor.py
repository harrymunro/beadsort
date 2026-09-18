"""Health checks, and the one experiment that matters: does bead metadata round-trip?

Nothing in beadsort's design depends on bd's metadata behaving a particular way, because
nobody had checked. The probe creates a throwaway beads repo, writes metadata three ways,
reads it back through `bd show` and `bd export`, and records what worked in the cache so
the write path can pick the right form.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from beadsort.bd import BdClient, BdError
from beadsort.cache import CacheStore, now_iso
from beadsort.config import BeadsortConfig, resolve_api_key
from beadsort.packs import PackError, load_enabled_packs

CAPABILITIES = ("json", "kv", "none")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class ProbeResult:
    capability: str
    notes: list[str] = field(default_factory=list)
    merges: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"capability": self.capability, "merges": self.merges, "notes": list(self.notes)}


def _metadata_of(record: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not record:
        return None
    meta = record.get("metadata")
    return meta if isinstance(meta, Mapping) else None


def probe_metadata(*, bd_bin: str = "bd", actor: str = "beadsort") -> ProbeResult:
    """Write metadata in a scratch repo and read it back. Never touches a real repo."""
    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="beadsort-probe-") as tmp:
        repo = Path(tmp)
        try:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=False, capture_output=True)
        except FileNotFoundError:
            notes.append("git not found; bd init may still work")
        bd = BdClient(repo, bd_bin=bd_bin, actor=actor)
        try:
            bd.init("probe")
            bead_id = bd.create("beadsort metadata probe")
        except BdError as exc:
            return ProbeResult("unknown", [f"could not create a scratch repo: {exc.message}"])

        def read_back() -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
            shown = _metadata_of(bd.show(bead_id))
            exported = next((r for r in bd.export() if r.get("id") == bead_id), None)
            return shown, _metadata_of(exported)

        json_ok = False
        try:
            bd.update(bead_id, metadata_json={"beadsort": {"probe": 1}})
            shown, exported = read_back()
            json_ok = bool(
                shown
                and shown.get("beadsort", {}).get("probe") == 1
                and exported
                and exported.get("beadsort", {}).get("probe") == 1
            )
            if shown and not exported:
                notes.append("bd show returns metadata but bd export does not")
        except BdError as exc:
            notes.append(f"--metadata failed: {exc.message}")

        merges: bool | None = None
        if json_ok:
            try:
                bd.update(bead_id, set_metadata=[("other", "x")])
                shown, _ = read_back()
                merges = bool(shown and "beadsort" in shown and shown.get("other") == "x")
                bd.update(bead_id, metadata_json={"beadsort": {"probe": 2}})
                shown, _ = read_back()
                if shown and shown.get("other") != "x":
                    merges = False
                    notes.append("--metadata replaces other keys; beadsort will re-send them")
            except BdError as exc:
                notes.append(f"merge check failed: {exc.message}")
            return ProbeResult("json", notes, merges)

        try:
            bd.update(bead_id, set_metadata=[("beadsort.probe", "1")])
            shown, exported = read_back()
            kv_ok = bool(
                shown
                and str(shown.get("beadsort.probe")) == "1"
                and exported
                and str(exported.get("beadsort.probe")) == "1"
            )
        except BdError as exc:
            kv_ok = False
            notes.append(f"--set-metadata failed: {exc.message}")
        if kv_ok:
            return ProbeResult("kv", notes, None)
        notes.append("metadata does not round-trip in this bd; labels and cache only")
        return ProbeResult("none", notes, None)


def ensure_capability(
    cache: CacheStore, bd_version: str, *, bd_bin: str = "bd", force: bool = False
) -> str:
    """Return the metadata capability, probing once per bd version.

    A probe that could not even create a scratch repo is reported as "unknown" and is
    never cached, so the next run tries again. Callers treat "unknown" like "none".
    """
    caps = cache.capabilities
    if not force and caps.get("metadata") in CAPABILITIES and caps.get("checked_bd") == bd_version:
        return str(caps["metadata"])
    result = probe_metadata(bd_bin=bd_bin)
    if result.capability == "unknown":
        caps["last_probe_error"] = "; ".join(result.notes)
        return "unknown"
    caps.pop("last_probe_error", None)
    caps.update(
        {
            "metadata": result.capability,
            "merges": result.merges,
            "checked_bd": bd_version,
            "checked_at": now_iso(),
            "notes": result.notes,
        }
    )
    return result.capability


def run_checks(
    repo: Path | None,
    config: BeadsortConfig | None,
    *,
    bd_bin: str = "bd",
    probe: bool = False,
    online: bool = False,
) -> list[Check]:
    checks: list[Check] = []
    which = BdClient.available(bd_bin)
    checks.append(Check("bd on PATH", bool(which), which or f"{bd_bin} not found"))
    bd: BdClient | None = None
    version = None
    if which and repo:
        bd = BdClient(repo, bd_bin=bd_bin)
        try:
            version = bd.version()
            checks.append(Check("bd version", True, version))
        except BdError as exc:
            checks.append(Check("bd version", False, exc.message))
    checks.append(Check("beads repo", repo is not None, str(repo) if repo else "no .beads/ found"))
    if bd:
        try:
            count = len(bd.export())
            checks.append(Check("bd export", True, f"{count} beads"))
        except BdError as exc:
            checks.append(Check("bd export", False, exc.message))
    if config is not None:
        checks.append(
            Check(
                "config",
                True,
                str(config.path) if config.path else "no .beadsort/config.yaml (defaults)",
            )
        )
        for warning in config.warnings:
            checks.append(Check("config warning", False, warning))
        try:
            packs, warnings = load_enabled_packs(config)
            checks.append(Check("packs", True, ", ".join(p.id for p in packs) or "none enabled"))
            for warning in warnings:
                checks.append(Check("pack warning", False, warning))
        except PackError as exc:
            checks.append(Check("packs", False, exc.message))
    key, source = resolve_api_key(bd)
    checks.append(
        Check(
            "api key",
            key is not None,
            f"from {source}" if key else "not found: export TYPESAFE_API_KEY=...",
        )
    )
    if online and key:
        try:
            from typesafe_sdk import TypeSafeClient

            with TypeSafeClient(api_key=key, timeout=30.0) as client:
                models = client.models.list()
            names = [getattr(m, "name", str(m)) for m in getattr(models, "models", [])]
            checks.append(Check("api reachable", True, ", ".join(names) or "ok"))
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            checks.append(Check("api reachable", False, f"{type(exc).__name__}: {exc}"))
    if config is not None:
        cache = CacheStore(config.cache_path).load()
        caps = cache.capabilities
        if probe and version:
            capability = ensure_capability(cache, version, bd_bin=bd_bin, force=True)
            cache.save()
            if capability == "unknown":
                notes_text = caps.get("last_probe_error") or "probe failed"
            else:
                notes_text = "; ".join(caps.get("notes") or [])
            checks.append(
                Check(
                    "metadata round-trip",
                    capability not in {"none", "unknown"},
                    f"{capability} ({notes_text or 'verified in a scratch repo'})",
                )
            )
        elif caps.get("metadata"):
            checks.append(
                Check(
                    "metadata round-trip",
                    caps["metadata"] != "none",
                    f"{caps['metadata']} (cached for bd {caps.get('checked_bd')})",
                )
            )
        else:
            checks.append(
                Check("metadata round-trip", False, "unknown; run `beadsort doctor --probe`")
            )
    return checks

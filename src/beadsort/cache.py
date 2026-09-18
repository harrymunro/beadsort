"""`.beadsort/cache.json`: what beadsort has already asked and what it has already written.

The cache makes re-runs cheap (a bead whose state, pack and model have not changed is not
sent again) and makes write-back safe (beadsort only ever removes a label it wrote itself,
which it knows from `written_labels`).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CACHE_VERSION = 1


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def call_key(pack_id: str, pack_version: int, model: str, questions: Any, state: Any) -> str:
    blob = json.dumps(
        [pack_id, pack_version, model, questions, state],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class CacheStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "version": CACHE_VERSION,
            "bd_version": None,
            "capabilities": {},
            "last_run": None,
            "beads": {},
        }
        self.loaded = False

    # ---- persistence ---------------------------------------------------------------

    def load(self) -> CacheStore:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                raw = {}
            if isinstance(raw, dict) and raw.get("version") == CACHE_VERSION:
                self.data.update(raw)
                self.data.setdefault("beads", {})
                self.data.setdefault("capabilities", {})
        self.loaded = True
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- accessors -----------------------------------------------------------------

    @property
    def beads(self) -> dict[str, Any]:
        return self.data["beads"]

    def entry(self, bead_id: str) -> dict[str, Any]:
        return self.beads.setdefault(
            bead_id,
            {
                "hash": None,
                "title": "",
                "written_labels": {},
                "metadata_written": False,
                "packs": {},
            },
        )

    def peek(self, bead_id: str) -> dict[str, Any] | None:
        return self.beads.get(bead_id)

    def pack_entry(self, bead_id: str, pack_id: str) -> dict[str, Any] | None:
        entry = self.beads.get(bead_id)
        if not entry:
            return None
        return entry.get("packs", {}).get(pack_id)

    def status_for(self, bead_id: str, pack_id: str, key: str) -> str:
        """'new' | 'changed' | 'unchanged' for one bead and pack against a call key."""
        pack = self.pack_entry(bead_id, pack_id)
        if pack is None:
            return "new"
        return "unchanged" if pack.get("call_key") == key else "changed"

    def record_answers(
        self,
        bead_id: str,
        pack_id: str,
        *,
        pack_version: int,
        key: str,
        model: str,
        request_id: str | None,
        input_tokens: int | None,
        answers: Mapping[str, Any],
    ) -> None:
        entry = self.entry(bead_id)
        entry["packs"][pack_id] = {
            "pack_version": pack_version,
            "call_key": key,
            "model": model,
            "request_id": request_id,
            "at": now_iso(),
            "usage": {"input_tokens": input_tokens},
            "answers": dict(answers),
            "verdict": None,
        }

    def record_verdict(self, bead_id: str, pack_id: str, verdict: Mapping[str, Any]) -> None:
        pack = self.entry(bead_id)["packs"].setdefault(pack_id, {})
        pack["verdict"] = dict(verdict)

    def record_precheck(
        self, bead_id: str, pack_id: str, *, pack_version: int, key: str, verdict: Mapping[str, Any]
    ) -> None:
        entry = self.entry(bead_id)
        entry["packs"][pack_id] = {
            "pack_version": pack_version,
            "call_key": key,
            "model": None,
            "request_id": None,
            "at": now_iso(),
            "usage": {"input_tokens": 0},
            "answers": {},
            "verdict": dict(verdict),
        }

    def touch_bead(self, bead_id: str, *, content_hash: str, title: str) -> None:
        entry = self.entry(bead_id)
        entry["hash"] = content_hash
        entry["title"] = title

    def written_labels(self, bead_id: str) -> dict[str, str]:
        entry = self.beads.get(bead_id)
        return dict(entry.get("written_labels") or {}) if entry else {}

    def set_written_labels(self, bead_id: str, labels: Mapping[str, str]) -> None:
        self.entry(bead_id)["written_labels"] = dict(labels)

    def set_metadata_written(self, bead_id: str, value: bool) -> None:
        self.entry(bead_id)["metadata_written"] = value

    def metadata_written(self, bead_id: str) -> bool:
        entry = self.beads.get(bead_id)
        return bool(entry and entry.get("metadata_written"))

    def drop_missing(self, live_ids: set[str]) -> list[str]:
        """Forget beads that no longer exist in the export at all."""
        gone = [i for i in self.beads if i not in live_ids]
        for i in gone:
            del self.beads[i]
        return gone

    @property
    def capabilities(self) -> dict[str, Any]:
        return self.data.setdefault("capabilities", {})

    def set_last_run(self, **fields: Any) -> None:
        self.data["last_run"] = {"at": now_iso(), **fields}

    @property
    def last_run(self) -> dict[str, Any] | None:
        return self.data.get("last_run")

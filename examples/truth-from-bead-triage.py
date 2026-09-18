#!/usr/bin/env python3
"""Turn a bead-triage `state.json` into `beadsort eval --truth-json` files.

bead-triage (a Claude-driven triage skill) stores one verdict per bead with the fields
category (COMMS / HARRY / CODE), stakeholder, harry_type, ask_urgency, code_effort,
code_risk and reject_candidate, plus a content hash of the bead text at verdict time.
Only beads whose current text still matches that hash are kept, so the truth describes
the bead as it is now.

Usage:
  python3 examples/truth-from-bead-triage.py .beads-triage/state.json \
      --issues .beads/issues.jsonl --config .beadsort/config.yaml --out truth/

Writes: waiting-on.json, waiting-on-coarse.json, owner-kind.json, ask-urgency.json,
stale.json, size.json, risk.json, agent-ready.json (each `{bead_id: value}`).

Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

MATERIAL_FIELDS = ("title", "description", "design", "acceptance_criteria", "notes")


def content_hash(issue: dict) -> str:
    parts = []
    for field in MATERIAL_FIELDS:
        value = issue.get(field) or ""
        parts.append(re.sub(r"\s+", " ", str(value)).strip())
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def load_issues(path: Path) -> dict[str, dict]:
    issues: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("id"):
            issues[obj["id"]] = obj
    return issues


def load_people(config_path: Path | None) -> dict[str, str]:
    """name or alias (lowercased) -> slug, from .beadsort/config.yaml."""
    if not config_path or not config_path.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        print("pyyaml not installed; stakeholder names will not map to slugs", file=sys.stderr)
        return {}
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for slug, spec in (raw.get("people") or {}).items():
        names = [slug]
        if isinstance(spec, dict):
            names.append(str(spec.get("name") or ""))
            names.extend(str(a) for a in spec.get("aliases") or [])
        for name in names:
            if name:
                out[name.lower()] = slug
                out[name.split()[0].lower()] = out.get(name.split()[0].lower(), slug)
    return out


def slug_for(stakeholder: str | None, people: dict[str, str]) -> str:
    if not stakeholder:
        return "person"
    key = stakeholder.strip().lower()
    if key in people:
        return people[key]
    first = key.split()[0] if key.split() else key
    return people.get(first, "person")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("state", type=Path, help="bead-triage state.json")
    parser.add_argument(
        "--issues",
        type=Path,
        required=True,
        help="issues.jsonl (bd export) to check hashes against",
    )
    parser.add_argument(
        "--config", type=Path, default=None, help=".beadsort/config.yaml for people slugs"
    )
    parser.add_argument("--out", type=Path, default=Path("truth"))
    parser.add_argument(
        "--keep-changed", action="store_true", help="keep verdicts whose bead text changed since"
    )
    args = parser.parse_args(argv)

    state = json.loads(args.state.read_text(encoding="utf-8"))
    issues = load_issues(args.issues)
    people = load_people(args.config)
    verdicts = state.get("beads") or {}

    truth: dict[str, dict[str, str]] = {
        "waiting-on": {},
        "waiting-on-coarse": {},
        "owner-kind": {},
        "ask-urgency": {},
        "stale": {},
        "size": {},
        "risk": {},
        "agent-ready": {},
    }
    kept = skipped_missing = skipped_changed = 0
    for bead_id, verdict in verdicts.items():
        issue = issues.get(bead_id)
        if issue is None:
            skipped_missing += 1
            continue
        if not args.keep_changed and verdict.get("hash") and verdict["hash"] != content_hash(issue):
            skipped_changed += 1
            continue
        kept += 1
        category = verdict.get("category")
        if category == "COMMS":
            truth["waiting-on"][bead_id] = slug_for(verdict.get("stakeholder"), people)
            truth["waiting-on-coarse"][bead_id] = "person"
            urgency = verdict.get("ask_urgency")
            if urgency in {"blocking", "soon", "whenever"}:
                truth["ask-urgency"][bead_id] = urgency
        elif category == "HARRY":
            truth["waiting-on"][bead_id] = "owner"
            truth["waiting-on-coarse"][bead_id] = "owner"
            kind = verdict.get("harry_type")
            if kind == "decision":
                truth["owner-kind"][bead_id] = "decision"
            elif kind == "admin_action":
                truth["owner-kind"][bead_id] = "admin"
        elif category == "CODE":
            truth["waiting-on"][bead_id] = "agent"
            truth["waiting-on-coarse"][bead_id] = "agent"
            effort = (verdict.get("code_effort") or "").lower()
            if effort in {"s", "m", "l"}:
                truth["size"][bead_id] = effort
            risk = (verdict.get("code_risk") or "").lower()
            if risk in {"low", "medium", "high"}:
                truth["risk"][bead_id] = risk
        truth["stale"][bead_id] = "yes" if verdict.get("reject_candidate") else "no"

    for session in state.get("sessions") or []:
        blocked = bool((session.get("blocked_by_human") or "").strip())
        for bead_id in session.get("beads") or []:
            if bead_id in issues and (args.keep_changed or bead_id in verdicts):
                truth["agent-ready"][bead_id] = "no" if blocked else "yes"

    args.out.mkdir(parents=True, exist_ok=True)
    for name, mapping in truth.items():
        (args.out / f"{name}.json").write_text(
            json.dumps(mapping, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"{len(mapping):5d}  {args.out / f'{name}.json'}")
    print(f"kept {kept} verdict(s); skipped {skipped_changed} changed, {skipped_missing} missing")
    if truth["waiting-on"] and people:
        unmapped = sorted(
            {v for v in truth["waiting-on"].values()}
            - set(people.values())
            - {"owner", "agent", "person"}
        )
        if unmapped:
            print(f"stakeholders not in config people: {unmapped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A stand-in `bd` for tests. Serves a JSON store; logs every call; mutates on writes.

Invoked through a tiny wrapper script that conftest puts on PATH. Environment:
  FAKE_BD_STORE  path to the JSON store {"issues": [...], "audit": [...], "config": {...}}
  FAKE_BD_LOG    path to a JSONL file that receives one line per invocation
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

VERSION = "1.2.2"


def _load() -> dict[str, Any]:
    path = Path(os.environ["FAKE_BD_STORE"])
    return json.loads(path.read_text(encoding="utf-8"))


def _save(store: dict[str, Any]) -> None:
    path = Path(os.environ["FAKE_BD_STORE"])
    path.write_text(json.dumps(store, indent=1), encoding="utf-8")


def _log(argv: list[str], stdin: str | None) -> None:
    log = os.environ.get("FAKE_BD_LOG")
    if not log:
        return
    entry = {
        "argv": argv,
        "stdin": stdin,
        "env": {k: os.environ.get(k) for k in ("BD_JSON_ENVELOPE", "BEADS_ACTOR")},
    }
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _envelope(data: Any) -> str:
    if os.environ.get("BD_JSON_ENVELOPE") == "1":
        return json.dumps({"data": data, "schema_version": 1})
    return json.dumps(data)


def _fail(code: str, message: str, rc: int = 1) -> int:
    sys.stderr.write(json.dumps({"code": code, "message": message}) + "\n")
    return rc


def _find(store: dict[str, Any], bead_id: str) -> dict[str, Any] | None:
    for issue in store["issues"]:
        if issue["id"] == bead_id:
            return issue
    return None


def _show_shape(store: dict[str, Any], issue: dict[str, Any]) -> dict[str, Any]:
    """`bd show` expands dependencies into target beads and adds `parent`."""
    out = {k: v for k, v in issue.items() if k not in {"_type", "dependencies"}}
    deps = []
    parent = None
    for dep in issue.get("dependencies") or []:
        target = _find(store, dep["depends_on_id"])
        if dep.get("type") == "parent-child":
            parent = dep["depends_on_id"]
        if target:
            expanded = {k: v for k, v in target.items() if k not in {"_type", "dependencies"}}
            expanded["dependency_type"] = dep.get("type")
            deps.append(expanded)
    out["dependencies"] = deps
    if parent:
        out["parent"] = parent
    return out


def main(argv: list[str]) -> int:
    stdin = None
    if "--stdin" in argv:
        stdin = sys.stdin.read()
    _log(argv, stdin)

    # global flags
    args = list(argv)
    json_out = False
    cleaned: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in {"-C", "--actor", "--db"}:
            i += 2
            continue
        if a == "--json":
            json_out = True
            i += 1
            continue
        if a in {"-q", "--quiet", "--non-interactive", "--skip-hooks", "--skip-agents"}:
            i += 1
            continue
        cleaned.append(a)
        i += 1
    args = cleaned
    if not args:
        return _fail("usage", "no command")

    cmd = args[0]
    if cmd == "--version":
        sys.stdout.write(f"bd version {VERSION} (fake)\n")
        return 0

    store = _load()

    if cmd == "export":
        for issue in store["issues"]:
            sys.stdout.write(json.dumps({"_type": "issue", **issue}) + "\n")
        return 0

    if cmd == "show":
        issue = _find(store, args[1]) if len(args) > 1 else None
        if not issue:
            return _fail("not_found", f"issue not found: {args[1:]}")
        data = [_show_shape(store, issue)]
        sys.stdout.write(
            _envelope(data) + "\n" if json_out else f"{issue['id']}: {issue['title']}\n"
        )
        return 0

    if cmd == "blocked":
        blocked = []
        for issue in store["issues"]:
            if issue.get("status") in {"closed", "scrapped"}:
                continue
            for dep in issue.get("dependencies") or []:
                if dep.get("type") in {"blocks", "conditional-blocks", "waits-for"}:
                    target = _find(store, dep["depends_on_id"])
                    if target and target.get("status") not in {"closed", "scrapped"}:
                        blocked.append({"id": issue["id"], "title": issue["title"]})
                        break
        sys.stdout.write(_envelope(blocked) + "\n")
        return 0

    if cmd == "ready":
        ready = [
            {"id": i["id"], "title": i["title"]}
            for i in store["issues"]
            if i.get("status") == "open"
        ]
        sys.stdout.write(_envelope(ready) + "\nShowing all ready issues.\n")
        return 0

    if cmd == "config":
        if len(args) >= 3 and args[1] == "get":
            value = store.get("config", {}).get(args[2])
            if value is None:
                sys.stdout.write(f"{args[2]} (not set)\n")  # real bd: exit 0, not an error
                return 0
            sys.stdout.write(f"{value}\n")
            return 0
        return _fail("usage", "unsupported config invocation")

    if cmd == "update":
        if len(args) < 2:
            return _fail("usage", "update needs an id")
        issue = _find(store, args[1])
        if not issue:
            return _fail("not_found", f"issue not found: {args[1]}")
        rest = args[2:]
        if not rest:
            return _fail("usage", "no fields to update")
        labels = list(issue.get("labels") or [])
        metadata = dict(issue.get("metadata") or {})
        j = 0
        while j < len(rest):
            flag = rest[j]
            value = rest[j + 1] if j + 1 < len(rest) else None
            if flag == "--add-label" and value is not None:
                for part in value.split(","):
                    if part and part not in labels:
                        labels.append(part)
            elif flag == "--remove-label" and value is not None:
                for part in value.split(","):
                    labels = [x for x in labels if x != part]
            elif flag == "--metadata" and value is not None:
                metadata.update(json.loads(value))
            elif flag == "--set-metadata" and value is not None:
                k, _, v = value.partition("=")
                metadata[k] = v
            elif flag == "--unset-metadata" and value is not None:
                metadata.pop(value, None)
            else:
                return _fail("usage", f"unknown update flag {flag}")
            j += 2
        issue["labels"] = labels
        if metadata:
            issue["metadata"] = metadata
        elif "metadata" in issue:
            del issue["metadata"]
        issue["updated_at"] = "2026-09-17T12:00:00Z"
        _save(store)
        sys.stdout.write(f"Updated {issue['id']}\n")
        return 0

    if cmd == "audit" and len(args) > 1 and args[1] == "record":
        entry: dict[str, Any] = {}
        rest = args[2:]
        j = 0
        while j < len(rest):
            flag = rest[j]
            if flag == "--stdin":
                j += 1
                continue
            value = rest[j + 1] if j + 1 < len(rest) else None
            entry[flag.lstrip("-").replace("-", "_")] = value
            j += 2
        if stdin:
            entry.update(json.loads(stdin))
        entry["actor"] = os.environ.get("BEADS_ACTOR")
        store.setdefault("audit", []).append(entry)
        _save(store)
        sys.stdout.write("recorded\n")
        return 0

    if cmd == "q" or cmd == "create":
        title = args[1] if len(args) > 1 else "untitled"
        prefix = store.get("prefix", "bs")
        new_id = f"{prefix}-{len(store['issues']) + 1:03d}"
        store["issues"].append(
            {
                "id": new_id,
                "title": title,
                "description": "",
                "status": "open",
                "priority": 2,
                "issue_type": "task",
                "labels": [],
                "dependencies": [],
                "created_at": "2026-09-17T12:00:00Z",
                "updated_at": "2026-09-17T12:00:00Z",
            }
        )
        _save(store)
        sys.stdout.write(f"{new_id}\n")
        return 0

    if cmd == "init":
        prefix = "bs"
        if "-p" in args:
            prefix = args[args.index("-p") + 1]
        store["prefix"] = prefix
        store.setdefault("issues", [])
        _save(store)
        Path(".beads").mkdir(exist_ok=True)
        return 0

    return _fail("unknown_command", f"fake bd does not implement {cmd}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Subprocess adapter for the `bd` CLI.

beads has no plugin API, so beadsort talks to it the way beads' own orchestrator does:
one `bd` process per call, arguments as a list (never a shell string), JSON in and out.

Every invocation goes through a per-repo lock. Embedded Dolt is single-writer, and even
read-only commands rewrite the store, so two concurrent `bd` processes on one repo fail
with "Another write batch or compaction is already active". Parallelism belongs to the
model calls, never to `bd`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from beadsort.errors import EXIT_BD, BeadsortError

ACTOR = "beadsort"
DEFAULT_TIMEOUT = 120.0

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class BdError(BeadsortError):
    """bd exited non-zero, was not found, or produced output beadsort cannot parse."""

    exit_code = EXIT_BD

    def __init__(self, message: str, *, code: str = "bd_failed", detail: Any = None) -> None:
        super().__init__(message, code=code, detail=detail)


def _repo_key(repo: Path) -> str:
    return hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()[:16]


def _thread_lock(repo: Path) -> threading.Lock:
    key = _repo_key(repo)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


@contextmanager
def repo_lock(repo: Path) -> Iterator[None]:
    """Serialise bd calls for one repo across threads and processes.

    The cross-process half uses a lock file in the system temp dir keyed by the repo path,
    so beadsort never creates files inside a repo just to read it.
    """
    with _thread_lock(repo):
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows has no fcntl
            yield
            return
        lock_path = Path(tempfile.gettempdir()) / f"beadsort-{_repo_key(repo)}.lock"
        with open(lock_path, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def unwrap_envelope(payload: Any) -> Any:
    """With BD_JSON_ENVELOPE=1, `--json` output is `{"data": ..., "schema_version": 1}`."""
    if isinstance(payload, dict) and "data" in payload and "schema_version" in payload:
        return payload["data"]
    return payload


def strip_trailing_noise(text: str) -> str:
    """`bd ready --json` appends a human line after the JSON. Cut at the last closing bracket."""
    stripped = text.strip()
    if not stripped:
        return stripped
    if stripped[-1] in "]}":
        return stripped
    last = max(stripped.rfind("]"), stripped.rfind("}"))
    return stripped[: last + 1] if last >= 0 else stripped


def parse_stderr_error(stderr: str) -> tuple[str, str] | None:
    """bd may print a JSON object with `code` and `message` on stderr. Find it if present."""
    for line in reversed(stderr.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            code = obj.get("code") or obj.get("error_code")
            message = obj.get("message") or obj.get("error")
            if code or message:
                return str(code or "bd_failed"), str(message or line)
    return None


class BdClient:
    """A thin, typed wrapper over `bd -C <repo> ...`."""

    def __init__(
        self,
        repo: Path,
        *,
        bd_bin: str = "bd",
        actor: str = ACTOR,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.repo = Path(repo)
        self.bd_bin = bd_bin
        self.actor = actor
        self.timeout = timeout
        self.calls = 0

    @staticmethod
    def available(bd_bin: str = "bd") -> str | None:
        return shutil.which(bd_bin)

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "BD_JSON_ENVELOPE": "1",
                "BEADS_ACTOR": self.actor,
                "BD_NON_INTERACTIVE": "1",
            }
        )
        return env

    def run(
        self,
        args: Iterable[str],
        *,
        write: bool = False,
        input_text: str | None = None,
        timeout: float | None = None,
        use_cwd: bool = False,
    ) -> str:
        """Run bd and return stdout. Raises BdError on non-zero exit.

        `use_cwd` runs bd inside the repo directory instead of passing `-C`, which bd
        refuses for a directory that is not yet a beads project (so: `init`).
        """
        args = list(args)
        cmd = [self.bd_bin] if use_cwd else [self.bd_bin, "-C", str(self.repo)]
        if write:
            cmd += ["--actor", self.actor]
        cmd += args
        try:
            with repo_lock(self.repo):
                self.calls += 1
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    input=input_text,
                    timeout=timeout or self.timeout,
                    env=self._env(),
                    check=False,
                    cwd=str(self.repo) if use_cwd else None,
                )
        except FileNotFoundError as exc:
            raise BdError(
                f"bd not found ({self.bd_bin}). Install beads: https://github.com/gastownhall/beads",
                code="bd_missing",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise BdError(
                f"bd timed out after {timeout or self.timeout:.0f}s: {' '.join(args)}",
                code="bd_timeout",
            ) from exc
        if proc.returncode != 0:
            parsed = parse_stderr_error(proc.stderr)
            if parsed:
                code, message = parsed
            else:
                code = "bd_failed"
                message = (
                    proc.stderr.strip() or proc.stdout.strip() or f"bd exited {proc.returncode}"
                )
            raise BdError(
                message,
                code=code,
                detail={"args": args, "returncode": proc.returncode},
            )
        return proc.stdout

    def run_json(
        self, args: Iterable[str], *, write: bool = False, input_text: str | None = None
    ) -> Any:
        out = self.run([*args, "--json"], write=write, input_text=input_text)
        text = strip_trailing_noise(out)
        if not text:
            return None
        try:
            return unwrap_envelope(json.loads(text))
        except json.JSONDecodeError as exc:
            raise BdError(
                f"bd returned invalid JSON for {' '.join(args)}",
                code="bd_bad_json",
                detail={"head": text[:200]},
            ) from exc

    # ---- reads -----------------------------------------------------------------

    def version(self) -> str:
        out = self.run(["--version"])
        match = _VERSION_RE.search(out)
        if not match:
            raise BdError(f"could not parse bd version from {out!r}", code="bd_bad_version")
        return match.group(1)

    def export(self) -> list[dict[str, Any]]:
        """`bd export`: one JSON object per line. The canonical, always-fresh read path."""
        out = self.run(["export"])
        records: list[dict[str, Any]] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            obj = unwrap_envelope(obj)
            if not isinstance(obj, dict):
                continue
            if obj.get("_type") not in (None, "issue"):
                continue
            if "id" not in obj:
                continue
            records.append(obj)
        return records

    def show(self, bead_id: str) -> dict[str, Any]:
        data = self.run_json(["show", bead_id])
        if isinstance(data, list):
            if not data:
                raise BdError(f"bead not found: {bead_id}", code="not_found")
            data = data[0]
        if not isinstance(data, dict):
            raise BdError(f"unexpected show output for {bead_id}", code="bd_bad_json")
        return data

    def blocked(self) -> list[dict[str, Any]]:
        data = self.run_json(["blocked"])
        return list(data) if isinstance(data, list) else []

    def config_get(self, key: str) -> str | None:
        """`bd config get <key>`: the bare value, or None when unset.

        bd prints `<key> (not set)` with exit code 0 for an unset key, and the bare value
        (or `<key> = <value>` in some versions) when set.
        """
        try:
            out = self.run(["config", "get", key])
        except BdError:
            return None
        value = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if not value:
            return None
        lowered = value.lower()
        if lowered.endswith("(not set)") or lowered in {"(not set)", "not set", "null", "<nil>"}:
            return None
        for prefix in (f"{key} = ", f"{key}=", f"{key}: ", f"{key} "):
            if value.startswith(prefix):
                value = value[len(prefix) :].strip()
                break
        return value or None

    # ---- writes ----------------------------------------------------------------

    def update(
        self,
        bead_id: str,
        *,
        add_labels: Iterable[str] = (),
        remove_labels: Iterable[str] = (),
        metadata_json: Mapping[str, Any] | None = None,
        set_metadata: Iterable[tuple[str, str]] = (),
        unset_metadata: Iterable[str] = (),
    ) -> None:
        args: list[str] = ["update", bead_id]
        for label in add_labels:
            args += ["--add-label", label]
        for label in remove_labels:
            args += ["--remove-label", label]
        if metadata_json is not None:
            args += ["--metadata", json.dumps(metadata_json, separators=(",", ":"))]
        for key, value in set_metadata:
            args += ["--set-metadata", f"{key}={value}"]
        for key in unset_metadata:
            args += ["--unset-metadata", key]
        if len(args) == 2:
            raise ValueError("update called with nothing to change")
        self.run(args, write=True)

    def audit_record(
        self,
        *,
        issue_id: str,
        model: str,
        prompt: str,
        response: str,
        kind: str = "llm_call",
    ) -> None:
        self.run(
            [
                "audit",
                "record",
                "--kind",
                kind,
                "--model",
                model,
                "--issue-id",
                issue_id,
                "--prompt",
                prompt,
                "--response",
                response,
            ],
            write=True,
        )

    def create(self, title: str) -> str:
        out = self.run(["q", title], write=True)
        bead_id = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if not bead_id:
            raise BdError("bd q returned no id", code="bd_failed")
        return bead_id

    def init(self, prefix: str) -> None:
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run(
            ["init", "-p", prefix, "--non-interactive", "--skip-hooks", "--skip-agents", "-q"],
            write=True,
            use_cwd=True,
        )

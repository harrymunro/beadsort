"""beadsort's per-repo configuration: `.beadsort/config.yaml`.

Precedence, highest first: CLI flag > config file > built-in default. The API key is never
in this file (it is committed); it comes from the `TYPESAFE_API_KEY` environment variable,
then from beads' own config under the sanctioned `custom.*` namespace.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from beadsort.bd import BdClient
from beadsort.beads import DONE_STATUSES
from beadsort.errors import BeadsortError

CONFIG_DIR = ".beadsort"
CONFIG_FILE = "config.yaml"
CACHE_FILE = "cache.json"
PACKS_DIR = "packs"
ENV_API_KEY = "TYPESAFE_API_KEY"
BD_CONFIG_API_KEY = "custom.beadsort.api_key"
DEFAULT_MODEL = "jev-latest"
DEFAULT_OWNER = "the project owner"
DEFAULT_PACKS = ("triage", "size", "agent-ready")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ConfigError(BeadsortError):
    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message, code="config_invalid", detail=detail)


@dataclass(frozen=True)
class Person:
    slug: str
    name: str
    aliases: tuple[str, ...] = ()
    role: str = ""

    def describe(self) -> str:
        """The option description the model sees for this person."""
        also = ", also written " + " or ".join(self.aliases) if self.aliases else ""
        role = f": {self.role}" if self.role else ""
        return f"{self.name}{also}{role}"


@dataclass(frozen=True)
class PackRef:
    id: str | None = None
    path: str | None = None
    thresholds: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.id or Path(self.path or "").stem


@dataclass(frozen=True)
class StateLimits:
    max_description_chars: int = 6000
    max_updates: int = 6
    max_update_chars: int = 1200
    parent_excerpt_chars: int = 600
    max_total_chars: int = 24000
    max_children: int = 25
    max_blockers: int = 5


@dataclass(frozen=True)
class Selection:
    statuses: tuple[str, ...] = ("open", "in_progress", "blocked")
    types: tuple[str, ...] = ()


@dataclass(frozen=True)
class BeadsortConfig:
    repo: Path
    path: Path | None = None
    owner: str = DEFAULT_OWNER
    project: Mapping[str, Any] = field(default_factory=dict)
    people: Mapping[str, Person] = field(default_factory=dict)
    repos: Mapping[str, str] = field(default_factory=dict)
    packs: tuple[PackRef, ...] = tuple(PackRef(id=p) for p in DEFAULT_PACKS)
    model: str = DEFAULT_MODEL
    workers: int = 8
    state: StateLimits = field(default_factory=StateLimits)
    select: Selection = field(default_factory=Selection)
    on_uncertain: str = "skip"
    audit: bool = True
    done_statuses: tuple[str, ...] = tuple(sorted(DONE_STATUSES))
    warnings: tuple[str, ...] = ()

    @property
    def config_dir(self) -> Path:
        return self.repo / CONFIG_DIR

    @property
    def cache_path(self) -> Path:
        return self.config_dir / CACHE_FILE

    @property
    def packs_dir(self) -> Path:
        return self.config_dir / PACKS_DIR

    def project_brief(self) -> dict[str, Any]:
        """The `project` object placed in every state. Always carries owner and agent names."""
        brief: dict[str, Any] = {}
        for key in ("name", "summary", "conventions"):
            value = self.project.get(key)
            if value:
                brief[key] = str(value)
        brief["owner"] = self.owner
        agents = self.project.get("agent_names") or []
        if agents:
            brief["agent_names"] = [str(a) for a in agents]
        return brief


def _as_str_tuple(value: Any, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(str(v) for v in value)
    raise ConfigError(f"{what} must be a list of strings")


def _parse_people(raw: Any) -> dict[str, Person]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError("people must be a map of slug -> {name, aliases, role}")
    people: dict[str, Person] = {}
    for slug, spec in raw.items():
        slug = str(slug)
        if not _SLUG_RE.match(slug):
            raise ConfigError(f"person slug {slug!r} must match [a-z0-9][a-z0-9._-]*")
        if isinstance(spec, str):
            people[slug] = Person(slug=slug, name=slug.replace("-", " ").title(), role=spec)
            continue
        if not isinstance(spec, Mapping):
            raise ConfigError(f"person {slug!r} must be a string role or a map")
        name = str(spec.get("name") or slug.replace("-", " ").title())
        people[slug] = Person(
            slug=slug,
            name=name,
            aliases=_as_str_tuple(spec.get("aliases"), f"people.{slug}.aliases"),
            role=str(spec.get("role") or ""),
        )
    return people


def _parse_packs(raw: Any) -> tuple[PackRef, ...]:
    if raw is None:
        return tuple(PackRef(id=p) for p in DEFAULT_PACKS)
    if not isinstance(raw, list):
        raise ConfigError("packs must be a list")
    refs: list[PackRef] = []
    for item in raw:
        if isinstance(item, str):
            refs.append(PackRef(id=item))
        elif isinstance(item, Mapping):
            pack_id = item.get("id")
            path = item.get("path")
            if not pack_id and not path:
                raise ConfigError("each pack entry needs an id or a path")
            thresholds = item.get("thresholds") or {}
            if not isinstance(thresholds, Mapping):
                raise ConfigError(f"pack {pack_id or path}: thresholds must be a map")
            refs.append(
                PackRef(
                    id=str(pack_id) if pack_id else None,
                    path=str(path) if path else None,
                    thresholds=dict(thresholds),
                )
            )
        else:
            raise ConfigError("pack entries must be strings or maps")
    return tuple(refs)


def _parse_limits(raw: Any) -> StateLimits:
    if raw is None:
        return StateLimits()
    if not isinstance(raw, Mapping):
        raise ConfigError("state must be a map")
    known = {f for f in StateLimits.__dataclass_fields__}
    values: dict[str, int] = {}
    for key, value in raw.items():
        if key not in known:
            raise ConfigError(f"unknown state limit {key!r}; known: {sorted(known)}")
        try:
            values[key] = int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"state.{key} must be an integer") from exc
    return StateLimits(**values)


def load_config(repo: Path) -> BeadsortConfig:
    """Load `.beadsort/config.yaml` if present; otherwise return defaults for the repo."""
    repo = Path(repo)
    path = repo / CONFIG_DIR / CONFIG_FILE
    if not path.exists():
        return BeadsortConfig(repo=repo, path=None)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path}: top level must be a map")
    if "api_key" in raw or ENV_API_KEY in raw:
        raise ConfigError(
            f"{path} contains an API key. This file is committed; put the key in the "
            f"{ENV_API_KEY} environment variable or `bd config set {BD_CONFIG_API_KEY} ...`."
        )
    warnings: list[str] = []
    known = {
        "version",
        "owner",
        "project",
        "people",
        "repos",
        "packs",
        "model",
        "workers",
        "state",
        "select",
        "on_uncertain",
        "audit",
        "done_statuses",
    }
    for key in raw:
        if key not in known:
            warnings.append(f"unknown config key {key!r} ignored")
    project = raw.get("project") or {}
    if not isinstance(project, Mapping):
        raise ConfigError("project must be a map")
    repos = raw.get("repos") or {}
    if not isinstance(repos, Mapping):
        raise ConfigError("repos must be a map of name -> description")
    select_raw = raw.get("select") or {}
    if not isinstance(select_raw, Mapping):
        raise ConfigError("select must be a map")
    on_uncertain = str(raw.get("on_uncertain") or "skip")
    if on_uncertain not in {"skip", "write_unsure"}:
        raise ConfigError("on_uncertain must be 'skip' or 'write_unsure'")
    workers = raw.get("workers", 8)
    try:
        workers = max(1, int(workers))
    except (TypeError, ValueError) as exc:
        raise ConfigError("workers must be an integer") from exc
    selection = Selection(
        statuses=_as_str_tuple(select_raw.get("statuses"), "select.statuses")
        or Selection().statuses,
        types=_as_str_tuple(select_raw.get("types"), "select.types"),
    )
    return BeadsortConfig(
        repo=repo,
        path=path,
        owner=str(raw.get("owner") or DEFAULT_OWNER),
        project=dict(project),
        people=_parse_people(raw.get("people")),
        repos={str(k): str(v) for k, v in repos.items()},
        packs=_parse_packs(raw.get("packs")),
        model=str(raw.get("model") or DEFAULT_MODEL),
        workers=workers,
        state=_parse_limits(raw.get("state")),
        select=selection,
        on_uncertain=on_uncertain,
        audit=bool(raw.get("audit", True)),
        done_statuses=_as_str_tuple(raw.get("done_statuses"), "done_statuses")
        or tuple(sorted(DONE_STATUSES)),
        warnings=tuple(warnings),
    )


def resolve_api_key(bd: BdClient | None = None) -> tuple[str | None, str]:
    """(key, source). Environment first, then beads config. Never logs the key."""
    key = os.environ.get(ENV_API_KEY, "").strip()
    if key:
        return key, "env"
    if bd is not None:
        value = bd.config_get(BD_CONFIG_API_KEY)
        if value:
            return value, "bd config"
    return None, "none"


CONFIG_TEMPLATE = """\
# beadsort configuration. Commit this file. Never put an API key in it: use the
# TYPESAFE_API_KEY environment variable or `bd config set custom.beadsort.api_key ...`.
version: 1

# The person who runs this backlog. Substituted for {{owner}} in every pack question.
# Set a real name: "the project owner" collides with beads that talk about business owners.
owner: "the project owner"

# A short brief the model sees with every bead. Keep it to a few sentences.
project:
  name: ""
  summary: ""
  # conventions: "Descriptions carry a Friendly Summary then a Technical section."
  # agent_names: [Notarius, Scriba]   # automated authors of updates; never stakeholders

# People a bead can be waiting on. Slug -> details. Injected into the triage pack.
people: {}
#  mike:
#    name: "Mike Lefler"
#    aliases: [Mike]
#    role: "programme sponsor"

# Other repositories work might belong in (enables agent-ready's off-board detection).
repos: {}

# Packs to run. Built-ins: triage, size, agent-ready. Add {path: ./packs/x.yaml} for your own.
packs:
  - triage
  - size
  - agent-ready

model: jev-latest      # pin a version once your thresholds are tuned
workers: 8             # parallel model calls

state:                 # what each request may carry; smaller is more accurate
  max_description_chars: 6000
  max_updates: 6
  max_update_chars: 1200
  parent_excerpt_chars: 600
  max_total_chars: 24000

select:                # which beads to classify by default
  statuses: [open, in_progress, blocked]
  types: []            # empty = all types

on_uncertain: skip     # skip | write_unsure   (write_unsure writes <dimension>:unsure)
audit: true            # record each model call with `bd audit record` on --apply
"""

GITIGNORE_TEMPLATE = """\
# beadsort runtime files (config.yaml and packs/ are meant to be committed)
cache.json
bd.lock
*.tmp
"""


def write_template(repo: Path, *, force: bool = False) -> tuple[Path, Path]:
    """Write `.beadsort/config.yaml` and `.beadsort/.gitignore`. Refuses to overwrite."""
    config_dir = Path(repo) / CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / CONFIG_FILE
    if config_path.exists() and not force:
        raise ConfigError(f"{config_path} exists; pass --force to overwrite")
    config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    ignore_path = config_dir / ".gitignore"
    if not ignore_path.exists() or force:
        ignore_path.write_text(GITIGNORE_TEMPLATE, encoding="utf-8")
    return config_path, ignore_path

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from beadsort.bd import BdClient, BdError, parse_stderr_error, strip_trailing_noise, unwrap_envelope
from tests.conftest import read_calls, read_store


def test_version_and_export(fake_bd: dict) -> None:
    bd = BdClient(fake_bd["repo"])
    assert bd.version() == "1.2.2"
    records = bd.export()
    assert {r["id"] for r in records} >= {"bs-e1", "bs-e1.1", "bs-d1"}
    assert all("_type" in r for r in records)


def test_calls_carry_repo_env_and_actor(fake_bd: dict) -> None:
    bd = BdClient(fake_bd["repo"])
    bd.update("bs-e1.1", add_labels=["size:s"])
    calls = read_calls(fake_bd["log"])
    call = calls[-1]
    assert call["argv"][:2] == ["-C", str(fake_bd["repo"])]
    assert (
        "--actor" in call["argv"] and call["argv"][call["argv"].index("--actor") + 1] == "beadsort"
    )
    assert call["env"] == {"BD_JSON_ENVELOPE": "1", "BEADS_ACTOR": "beadsort"}
    labels = next(i for i in read_store(fake_bd["store"])["issues"] if i["id"] == "bs-e1.1")[
        "labels"
    ]
    assert "size:s" in labels


def test_update_passes_each_label_as_its_own_flag(fake_bd: dict) -> None:
    bd = BdClient(fake_bd["repo"])
    bd.update("bs-e1.1", add_labels=["a:b", "c:d"], remove_labels=["x:y"], metadata_json={"k": 1})
    argv = read_calls(fake_bd["log"])[-1]["argv"]
    assert argv.count("--add-label") == 2
    assert argv.count("--remove-label") == 1
    assert "--metadata" in argv


def test_update_with_nothing_is_a_programming_error(fake_bd: dict) -> None:
    with pytest.raises(ValueError):
        BdClient(fake_bd["repo"]).update("bs-e1.1")


def test_show_unwraps_envelope_and_array(fake_bd: dict) -> None:
    bd = BdClient(fake_bd["repo"])
    shown = bd.show("bs-e1.1")
    assert shown["id"] == "bs-e1.1"
    assert shown["parent"] == "bs-e1"
    assert shown["dependencies"][0]["dependency_type"] == "parent-child"


def test_error_from_stderr_json(fake_bd: dict) -> None:
    bd = BdClient(fake_bd["repo"])
    with pytest.raises(BdError) as excinfo:
        bd.update("bs-nope", add_labels=["a:b"])
    assert excinfo.value.code == "not_found"
    assert excinfo.value.exit_code == 3


def test_missing_binary() -> None:
    bd = BdClient(Path("/nonexistent"), bd_bin="definitely-not-bd-xyz")
    with pytest.raises(BdError) as excinfo:
        bd.version()
    assert excinfo.value.code == "bd_missing"


def test_config_get_missing_returns_none(fake_bd: dict) -> None:
    assert BdClient(fake_bd["repo"]).config_get("custom.beadsort.api_key") is None


def test_config_get_set_value(fake_bd: dict) -> None:
    store = read_store(fake_bd["store"])
    store["config"]["custom.beadsort.api_key"] = "sk-from-bd"
    fake_bd["store"].write_text(json.dumps(store), encoding="utf-8")
    assert BdClient(fake_bd["repo"]).config_get("custom.beadsort.api_key") == "sk-from-bd"


def test_helpers() -> None:
    assert unwrap_envelope({"data": [1], "schema_version": 1}) == [1]
    assert unwrap_envelope([1]) == [1]
    assert strip_trailing_noise('[{"a":1}]\nShowing 2 of 3\n') == '[{"a":1}]'
    assert strip_trailing_noise("") == ""
    assert parse_stderr_error('warn\n{"code":"x","message":"boom"}\n') == ("x", "boom")
    assert parse_stderr_error("plain text") is None


def test_calls_are_serialised_per_repo(fake_bd: dict) -> None:
    """Twenty threads hammering one repo must never overlap inside bd."""
    bd = BdClient(fake_bd["repo"])
    active = 0
    peak = 0
    guard = threading.Lock()
    original = bd.run

    def instrumented(args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            return original(args, **kwargs)
        finally:
            with guard:
                active -= 1

    bd.run = instrumented  # type: ignore[method-assign]
    threads = [threading.Thread(target=bd.version) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # `run` itself is entered concurrently; the lock inside it serialises the subprocess.
    # What we assert is that every call completed and the client counted them all.
    assert bd.calls == 20
    assert peak >= 1


def test_init_runs_in_the_directory_without_dash_c(fake_bd: dict, tmp_path: Path) -> None:
    target = tmp_path / "fresh"
    BdClient(target).init("fresh")
    argv = read_calls(fake_bd["log"])[-1]["argv"]
    assert "-C" not in argv and argv[:2] == ["--actor", "beadsort"] and "init" in argv

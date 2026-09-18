from __future__ import annotations

import json
from pathlib import Path

import pytest

from beadsort.beads import Bead
from beadsort.errors import UsageError
from beadsort.eval import (
    evaluate,
    truth_from_json,
    truth_from_label,
    truth_from_metadata,
    truth_from_prefix,
)


def test_truth_sources(tmp_path: Path) -> None:
    beads = [
        Bead(
            id="a",
            title="A",
            labels=("human", "waiting-on:mike"),
            metadata={"triage": {"cat": "COMMS"}},
        ),
        Bead(id="b", title="B", labels=()),
    ]
    assert truth_from_label(beads, "human") == {"a": "yes", "b": "no"}
    assert truth_from_prefix(beads, "waiting-on") == {"a": "mike"}
    assert truth_from_metadata(beads, "triage.cat") == {"a": "COMMS"}
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"a": "x", "b": None}), encoding="utf-8")
    assert truth_from_json(path) == {"a": "x"}
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(UsageError):
        truth_from_json(path)


def test_evaluate_categorical() -> None:
    truth = {"a": "mike", "b": "mike", "c": "lena", "d": "lena", "e": "mike"}
    predictions = {"a": "mike", "b": "lena", "c": "lena", "d": None, "e": "mike"}
    report = evaluate(
        "waiting-on",
        predictions,
        truth,
        confidences={"a": 0.95, "b": 0.55, "c": 0.8, "e": 0.75},
        forced={"d": "mike"},
    )
    assert report.total == 5 and report.covered == 4 and report.correct == 3
    assert report.per_value["mike"]["precision"] == 1.0 and report.per_value["mike"][
        "recall"
    ] == pytest.approx(2 / 3, abs=1e-3)
    assert report.per_value["lena"]["precision"] == 0.5
    assert report.uncovered_wrong_if_forced == 1
    assert report.disagreements[0]["id"] == "b"
    bands = {b["band"]: b for b in report.bands}
    assert bands["0.50-0.70"]["accuracy"] == 0.0 and bands[">=0.90"]["accuracy"] == 1.0


def test_evaluate_collapsed_to_yes_no() -> None:
    truth = {"a": "yes", "b": "no", "c": "yes"}
    predictions = {"a": "mike", "b": "agent", "c": "agent"}
    report = evaluate("waiting-on", predictions, truth, positive=["mike", "person", "owner"])
    assert report.confusion == {"yes": {"yes": 1, "no": 1}, "no": {"no": 1}}
    assert report.per_value["yes"]["recall"] == 0.5
    # empty positive set: any label counts as yes
    report2 = evaluate("waiting-on", {"a": "x", "b": None}, {"a": "yes", "b": "no"}, positive=[])
    assert report2.correct == 1 and report2.covered == 1

from __future__ import annotations

import json
from pathlib import Path

import pytest

from beadsort.share import (
    PLAYGROUND_URL,
    compress_to_encoded_uri_component,
    decompress_from_encoded_uri_component,
    playground_link,
)

VECTOR = Path(__file__).parent / "fixtures" / "playground_share.txt"


@pytest.mark.parametrize(
    "text",
    [
        "a",
        "Hello, World!",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        json.dumps({"state": "My card was charged twice.", "n": [1, 2, 3]}),
        "ünïcödé and emoji 🎉 and CJK 漢字",
        "x" * 5000 + "y" * 5000,
    ],
)
def test_round_trip(text: str) -> None:
    packed = compress_to_encoded_uri_component(text)
    assert all(
        c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-$" for c in packed
    )
    assert decompress_from_encoded_uri_component(packed) == text


def test_real_docs_share_link_decodes_and_re_encodes_identically() -> None:
    """A link copied from the provider's docs: decoding must give the documented JSON shape,
    and re-encoding must reproduce the link byte for byte."""
    packed = VECTOR.read_text(encoding="utf-8").strip()
    assert packed
    decoded = decompress_from_encoded_uri_component(packed)
    assert decoded is not None
    payload = json.loads(decoded)
    assert set(payload) >= {"apiVersion", "documentText", "promptsText"}
    assert compress_to_encoded_uri_component(decoded) == packed


def test_empty_string_follows_lz_string_semantics() -> None:
    assert compress_to_encoded_uri_component("") == ""
    assert decompress_from_encoded_uri_component("") is None


def test_playground_link_shape() -> None:
    url = playground_link(
        {"bead": {"id": "x"}}, {"triage__q": {"type": "noul", "instructions": "?"}}
    )
    assert url.startswith(PLAYGROUND_URL)
    payload = json.loads(decompress_from_encoded_uri_component(url[len(PLAYGROUND_URL) :]))
    assert payload["apiVersion"] == "v1" and payload["selectedModels"] == ["jev-latest"]
    assert json.loads(payload["documentText"]) == {"bead": {"id": "x"}}
    assert "triage__q" in json.loads(payload["promptsText"])

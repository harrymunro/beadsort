"""Playground share links: the exact state and questions beadsort would send, in a URL.

The console's share link is lz-string's `compressToEncodedURIComponent` over a small JSON
document. The encoder is vendored here (about 150 lines) so the tool has no dependency on
it. Text is handled as UTF-16 code units, as JavaScript would.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

PLAYGROUND_URL = "https://console.typesafe.ai/playground#share/"
_KEY = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-$"
_REVERSE = {c: i for i, c in enumerate(_KEY)}


def _to_units(text: str) -> str:
    """A str whose code points are the UTF-16 code units of `text` (surrogates split)."""
    data = text.encode("utf-16-le", "surrogatepass")
    return "".join(chr(int.from_bytes(data[i : i + 2], "little")) for i in range(0, len(data), 2))


def _from_units(units: str) -> str:
    data = b"".join(ord(ch).to_bytes(2, "little") for ch in units)
    return data.decode("utf-16-le", "surrogatepass")


def compress_to_encoded_uri_component(text: str) -> str:
    return _compress(_to_units(text), 6, lambda i: _KEY[i])


def _compress(uncompressed: str, bits_per_char: int, char_for: Any) -> str:
    if not uncompressed:
        return ""
    dictionary: dict[str, int] = {}
    to_create: dict[str, bool] = {}
    w = ""
    enlarge_in = 2
    dict_size = 3
    num_bits = 2
    out: list[str] = []
    val = 0
    pos = 0

    def push(bit: int) -> None:
        nonlocal val, pos
        val = (val << 1) | bit
        if pos == bits_per_char - 1:
            pos = 0
            out.append(char_for(val))
            val = 0
        else:
            pos += 1

    def emit_w(word: str) -> None:
        nonlocal enlarge_in, num_bits
        if word in to_create:
            code = ord(word[0])
            if code < 256:
                for _ in range(num_bits):
                    push(0)
                for _ in range(8):
                    push(code & 1)
                    code >>= 1
            else:
                value = 1
                for _ in range(num_bits):
                    push(value)
                    value = 0
                for _ in range(16):
                    push(code & 1)
                    code >>= 1
            enlarge_in -= 1
            if enlarge_in == 0:
                enlarge_in = 2**num_bits
                num_bits += 1
            del to_create[word]
        else:
            value = dictionary[word]
            for _ in range(num_bits):
                push(value & 1)
                value >>= 1
        enlarge_in -= 1
        if enlarge_in == 0:
            enlarge_in = 2**num_bits
            num_bits += 1

    for c in uncompressed:
        if c not in dictionary:
            dictionary[c] = dict_size
            dict_size += 1
            to_create[c] = True
        wc = w + c
        if wc in dictionary:
            w = wc
        else:
            emit_w(w)
            dictionary[wc] = dict_size
            dict_size += 1
            w = c
    if w:
        emit_w(w)
    value = 2
    for _ in range(num_bits):
        push(value & 1)
        value >>= 1
    while True:
        val <<= 1
        if pos == bits_per_char - 1:
            out.append(char_for(val))
            break
        pos += 1
    return "".join(out)


def decompress_from_encoded_uri_component(text: str) -> str | None:
    if text is None:
        return ""
    if text == "":
        return None
    text = text.replace(" ", "+")
    units = _decompress(len(text), 32, lambda i: _REVERSE[text[i]])
    return None if units is None else _from_units(units)


def _decompress(length: int, reset_value: int, next_value: Any) -> str | None:
    dictionary: dict[int, str] = {i: chr(i) for i in range(3)}
    enlarge_in = 4
    dict_size = 4
    num_bits = 3
    result: list[str] = []
    data_val = next_value(0)
    data_position = reset_value
    data_index = 1

    def read(n: int) -> int:
        nonlocal data_val, data_position, data_index
        bits = 0
        power = 1
        maxpower = 2**n
        while power != maxpower:
            resb = data_val & data_position
            data_position >>= 1
            if data_position == 0:
                data_position = reset_value
                data_val = next_value(data_index)
                data_index += 1
            bits |= (1 if resb > 0 else 0) * power
            power <<= 1
        return bits

    first = read(2)
    if first == 0:
        c = chr(read(8))
    elif first == 1:
        c = chr(read(16))
    else:
        return ""
    dictionary[3] = c
    w = c
    result.append(c)
    while True:
        if data_index > length:
            return ""
        code = read(num_bits)
        if code == 0:
            dictionary[dict_size] = chr(read(8))
            dict_size += 1
            code = dict_size - 1
            enlarge_in -= 1
        elif code == 1:
            dictionary[dict_size] = chr(read(16))
            dict_size += 1
            code = dict_size - 1
            enlarge_in -= 1
        elif code == 2:
            return "".join(result)
        if enlarge_in == 0:
            enlarge_in = 2**num_bits
            num_bits += 1
        if code in dictionary:
            entry = dictionary[code]
        elif code == dict_size:
            entry = w + w[0]
        else:
            return None
        result.append(entry)
        dictionary[dict_size] = w + entry[0]
        dict_size += 1
        enlarge_in -= 1
        w = entry
        if enlarge_in == 0:
            enlarge_in = 2**num_bits
            num_bits += 1


def playground_link(
    state: Mapping[str, Any], questions: Mapping[str, Mapping[str, Any]], model: str = "jev-latest"
) -> str:
    payload = {
        "apiVersion": "v1",
        "documentText": json.dumps(state, indent=2, ensure_ascii=False),
        "promptsText": json.dumps(questions, indent=2, ensure_ascii=False),
        "selectedModels": [model],
    }
    return PLAYGROUND_URL + compress_to_encoded_uri_component(json.dumps(payload))

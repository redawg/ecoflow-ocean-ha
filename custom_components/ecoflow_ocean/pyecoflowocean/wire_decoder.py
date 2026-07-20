"""Generic protobuf wire-format decoder (no .proto schema required)."""

from __future__ import annotations

import struct
from typing import Any


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while i < len(data):
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
    raise ValueError("truncated varint")


def decode_protobuf(data: bytes, *, max_depth: int = 8) -> dict[int, Any]:
    """Decode protobuf bytes into {field_number: value} dict."""
    return _decode_message(data, depth=0, max_depth=max_depth)


def _store_field(fields: dict[int, Any], field_num: int, val: Any) -> None:
    """Accumulate repeated protobuf fields as lists (last-write would drop packs)."""
    if field_num not in fields:
        fields[field_num] = val
        return
    existing = fields[field_num]
    if isinstance(existing, list):
        existing.append(val)
    else:
        fields[field_num] = [existing, val]


def _decode_message(data: bytes, *, depth: int, max_depth: int) -> dict[int, Any]:
    fields: dict[int, Any] = {}
    i = 0
    while i < len(data):
        tag, i = _read_varint(data, i)
        field_num = tag >> 3
        wire_type = tag & 0x7

        if wire_type == 0:
            val, i = _read_varint(data, i)
            _store_field(fields, field_num, val)
        elif wire_type == 1:
            if i + 8 > len(data):
                break
            _store_field(fields, field_num, struct.unpack("<d", data[i : i + 8])[0])
            i += 8
        elif wire_type == 2:
            length, i = _read_varint(data, i)
            chunk = data[i : i + length]
            i += length
            text = _try_utf8_text(chunk)
            nested: dict[int, Any] | None = None
            if depth < max_depth:
                try:
                    candidate = _decode_message(chunk, depth=depth + 1, max_depth=max_depth)
                    if candidate:
                        nested = candidate
                except ValueError:
                    nested = None
            # Circuit/device labels are UTF-8 strings whose bytes often also
            # "parse" as shallow protobuf — prefer the human-readable string.
            if text is not None and (nested is None or _looks_like_label(text)):
                _store_field(fields, field_num, text)
            elif nested is not None:
                _store_field(fields, field_num, nested)
            elif text is not None:
                _store_field(fields, field_num, text)
            else:
                _store_field(fields, field_num, chunk)
        elif wire_type == 5:
            if i + 4 > len(data):
                break
            _store_field(fields, field_num, struct.unpack("<f", data[i : i + 4])[0])
            i += 4
        else:
            break
    return fields


def _try_utf8_text(chunk: bytes) -> str | None:
    if not chunk:
        return None
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    return text


def _looks_like_label(text: str) -> bool:
    """True when a length-delimited blob is more likely a UI label than a sub-message."""
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    letters = sum(1 for c in stripped if c.isalpha())
    if letters < 2:
        return False
    # Mostly printable ASCII / latin text.
    printable = sum(1 for c in stripped if c.isprintable())
    return printable / max(len(stripped), 1) >= 0.9


def unwrap_payload_root(tree: dict[int, Any]) -> dict[int, Any]:
    """Find the protobuf sub-message containing Ocean Panel circuit fields."""
    found = _find_panel_data_root(tree)
    if found is not None:
        return found
    inner = tree.get(1)
    if isinstance(inner, dict) and len(inner) > 1:
        return inner
    return tree


def _find_panel_data_root(node: dict[int, Any], depth: int = 0) -> dict[int, Any] | None:
    if depth > 6:
        return None
    if any(isinstance(k, int) and 1015 <= k <= 1054 for k in node):
        return node
    for value in node.values():
        if isinstance(value, dict):
            found = _find_panel_data_root(value, depth + 1)
            if found is not None:
                return found
    return None


def get_float(block: dict[int, Any] | Any, field: int) -> float | None:
    if not isinstance(block, dict):
        return None
    val = block.get(field)
    if isinstance(val, (int, float)):
        return float(val)
    return None


def flatten_tree(
    node: dict[int, Any],
    prefix: str = "",
    *,
    depth: int = 0,
    max_depth: int = 10,
) -> dict[str, Any]:
    """Flatten a decoded protobuf tree into dotted field paths."""
    out: dict[str, Any] = {}
    if depth > max_depth or not isinstance(node, dict):
        return out
    for field_num, value in node.items():
        path = f"{prefix}{field_num}" if prefix else str(field_num)
        if isinstance(value, list):
            for idx, item in enumerate(value):
                item_path = f"{path}[{idx}]"
                if isinstance(item, dict):
                    out.update(
                        flatten_tree(item, f"{item_path}.", depth=depth + 1, max_depth=max_depth)
                    )
                elif isinstance(item, bytes):
                    continue
                else:
                    out[item_path] = item
        elif isinstance(value, dict):
            out.update(flatten_tree(value, f"{path}.", depth=depth + 1, max_depth=max_depth))
        elif isinstance(value, bytes):
            continue
        else:
            out[path] = value
    return out


def iter_headers(tree: dict[int, Any]) -> list[dict[int, Any]]:
    """Return Header message dicts from a root Envelope (repeated field 1)."""
    headers = tree.get(1)
    if isinstance(headers, list):
        return [h for h in headers if isinstance(h, dict)]
    if isinstance(headers, dict):
        return [headers]
    return []

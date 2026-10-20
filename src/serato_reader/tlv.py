"""Serato's tag-length-value format, shared by "database V2", crates and smart crates.

Every field is a 4-letter ASCII tag, a big-endian u32 length and the value. The tag's first letter says the type:
  o, r   a nested list of fields          t, p   UTF-16 big-endian text ("vrsn" is text too)
  u      u32                              s      u16
  b      one-byte boolean

The parser never raises on damaged input: a field that runs past the end of the data is cut at the end, and parsing stops at the
first field whose header is incomplete.
"""
import struct


def parse(data, start=0, end=None, depth=0):
    """[(tag, value)] for a TLV byte string. Nested lists stay lists; unknown types stay bytes."""
    end = len(data) if end is None else end
    out = []
    i = start
    while i + 8 <= end:
        tag = bytes(data[i:i + 4]).decode("latin-1")
        n = struct.unpack_from(">I", data, i + 4)[0]
        a, b = i + 8, min(i + 8 + n, end)
        i = i + 8 + n
        out.append((tag, _value(tag, data, a, b, depth)))
    return out


def _value(tag, data, a, b, depth):
    c = tag[:1]
    raw = bytes(data[a:b])
    if tag == "vrsn" or c in ("t", "p"):
        return raw.decode("utf-16-be", "ignore").rstrip("\0")
    if c == "u":
        return struct.unpack(">I", raw)[0] if len(raw) == 4 else int.from_bytes(raw, "big") if raw else 0
    if c == "s":
        return struct.unpack(">H", raw)[0] if len(raw) == 2 else int.from_bytes(raw, "big") if raw else 0
    if c == "b":
        return bool(raw[0]) if raw else False
    if c in ("o", "r") and depth < 8:
        if len(raw) >= 8 and _looks_tlv(raw):
            return parse(raw, depth=depth + 1)
        return int.from_bytes(raw, "big") if 0 < len(raw) <= 4 else raw     # "rart" / "rlut" style flags
    return raw


def _looks_tlv(raw):
    i = 0
    while i + 8 <= len(raw):
        if not all(32 < x < 127 for x in raw[i:i + 4]):
            return False
        i += 8 + struct.unpack_from(">I", raw, i + 4)[0]
    return i == len(raw)


def as_dict(fields):
    """Nested field list -> dict (first value wins; nested lists become lists of dicts under their tag)."""
    out = {}
    for k, v in fields:
        if isinstance(v, list):
            out.setdefault(k, []).append(as_dict(v))
        elif k not in out:
            out[k] = v
    return out

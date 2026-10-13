"""ICAO 24-bit address -> country, for the few nations whose roundels the board can draw."""
from __future__ import annotations

RANGES = [("DE", 0x3C0000, 0x3FFFFF), ("GB", 0x400000, 0x43FFFF), ("US", 0xA00000, 0xAFFFFF), ("CA", 0xC00000, 0xC3FFFF)]


def country_of(hex_: str) -> str:
    try:
        n = int(hex_, 16)
    except (TypeError, ValueError):
        return ""
    for code, lo, hi in RANGES:
        if lo <= n <= hi:
            return code
    return ""

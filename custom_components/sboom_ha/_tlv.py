"""Бинарный TLV-кодек: varint + length-delimited + fixed32.

kind=0 → varint, kind=2 → length-delimited (bytes / nested / utf-8),
kind=5 → fixed32 (4 байта LE, в протоколе колонки — float32,
например скорость в op=23 SET_PLAYBACK_SPEED).
Используется для упаковки/распаковки сообщений с колонкой.
"""
from __future__ import annotations

import struct
from typing import Any


class _Truncated(Exception):
    """Данные закончились посреди varint — декодеры возвращают частичный результат."""


def varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7f:
        out.append((n & 0x7f) | 0x80)
        n >>= 7
    out.append(n & 0x7f)
    return bytes(out)


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Читает varint с позиции pos → (значение, новая позиция).

    Бросает _Truncated, если данные оборвались до конца varint.
    """
    value, shift = 0, 0
    n = len(data)
    while True:
        if pos >= n:
            raise _Truncated
        b = data[pos]
        pos += 1
        value |= (b & 0x7f) << shift
        if b & 0x80 == 0:
            return value, pos
        shift += 7


def field(tag: int, kind: int, payload: int | float | bytes) -> bytes:
    """Закодировать одно поле.

    kind=0 → varint, kind=2 → length-delim, kind=5 → fixed32 float
    (4 байта LE IEEE-754; используется для op=23 set-playback-speed —
    varint-кодировка скорости ломает состояние колонки в 0.0).
    """
    key = (tag << 3) | kind
    if kind == 0:
        return varint(key) + varint(int(payload))  # type: ignore[arg-type]
    if kind == 2:
        return varint(key) + varint(len(payload)) + payload  # type: ignore[arg-type]
    if kind == 5:
        return varint(key) + struct.pack("<f", float(payload))  # type: ignore[arg-type]
    raise ValueError(f"unsupported kind: {kind}")


def decode(data: bytes) -> dict[int, Any]:
    """Рекурсивный TLV-декодер.

    Length-delim поля пробуются как UTF-8 → nested → hex.
    Fixed32 (kind=5) читается как float32 LE — единственное применение
    wire-type 5 в протоколе колонки (op=23 playback speed).
    """
    out: dict[int, Any] = {}
    i = 0
    n = len(data)
    while i < n:
        try:
            key, i = _read_varint(data, i)
        except _Truncated:
            return out
        f, kind = key >> 3, key & 0x7
        if kind == 0:
            try:
                v, i = _read_varint(data, i)
            except _Truncated:
                return out
            out[f] = v
        elif kind == 2:
            try:
                ln, i = _read_varint(data, i)
            except _Truncated:
                return out
            payload = data[i : i + ln]
            i += ln
            try:
                s = payload.decode("utf-8")
                if all(c.isprintable() or c in "\n\t" for c in s):
                    out[f] = s
                    continue
            except UnicodeDecodeError:
                pass
            try:
                nested = decode(payload)
                out[f] = nested if nested else payload.hex()
            except Exception:  # pragma: no cover
                out[f] = payload.hex()
        elif kind == 5:
            if i + 4 > n:  # обрезанный fixed32 — вернуть что успели
                return out
            out[f] = struct.unpack("<f", data[i : i + 4])[0]
            i += 4
        else:
            break
    return out


def decode_repeated(data: bytes) -> dict[int, list[Any]]:
    """TLV-декод с сохранением повторяющихся тегов (для proto `repeated`-полей).

    Возвращает `{tag: [values...]}` — каждое вхождение тега собирается в список.
    Length-delimited значения возвращаются СЫРЫМИ bytes (без авто-рекурсии);
    varint — int; fixed32 — 4 байта. Для вложенных сообщений вызывать повторно.
    """
    out: dict[int, list[Any]] = {}
    i, n = 0, len(data)
    while i < n:
        try:
            key, i = _read_varint(data, i)
        except _Truncated:
            return out
        tag, kind = key >> 3, key & 0x7
        if kind == 0:
            try:
                v, i = _read_varint(data, i)
            except _Truncated:
                return out
            out.setdefault(tag, []).append(v)
        elif kind == 2:
            try:
                ln, i = _read_varint(data, i)
            except _Truncated:
                return out
            out.setdefault(tag, []).append(data[i : i + ln])
            i += ln
        elif kind == 5:
            out.setdefault(tag, []).append(data[i : i + 4])
            i += 4
        else:
            break
    return out

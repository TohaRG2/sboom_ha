"""Тесты бинарного TLV-кодека (varint + length-delimited)."""
from __future__ import annotations

import struct

from sboom_ha._tlv import decode, decode_repeated, field, varint

# ─────────────────────── varint ───────────────────────

def test_varint_zero():
    assert varint(0) == b"\x00"


def test_varint_single_byte_boundary():
    assert varint(127) == b"\x7f"


def test_varint_two_bytes():
    # 128 = 0x80; в varint LE: 0x80, 0x01
    assert varint(128) == b"\x80\x01"


def test_varint_large_value():
    assert varint(300) == b"\xac\x02"


# ─────────────────────── field ───────────────────────

def test_field_kind0_varint():
    # tag=1, kind=0 (varint), value=42
    # key = (1<<3) | 0 = 0x08; payload = varint(42) = 0x2a
    assert field(1, 0, 42) == b"\x08\x2a"


def test_field_kind2_length_delim():
    # tag=2, kind=2, value=b"hi"
    # key = (2<<3) | 2 = 0x12; len=2; payload=b"hi"
    assert field(2, 2, b"hi") == b"\x12\x02hi"


def test_field_kind2_empty_payload():
    assert field(5, 2, b"") == b"\x2a\x00"


def test_field_kind5_float():
    # tag=1, kind=5 (fixed32 float), value=1.5
    # key = (1<<3) | 5 = 0x0d; payload = struct.pack("<f", 1.5)
    assert field(1, 5, 1.5) == b"\x0d" + struct.pack("<f", 1.5)


def test_field_kind5_float_reset_value():
    # value=1.0 — кодировка для сброса скорости
    assert field(1, 5, 1.0) == b"\x0d" + struct.pack("<f", 1.0)


def test_field_unsupported_kind_raises():
    import pytest
    # kind=1 (fixed64) не поддерживается кодеком
    with pytest.raises(ValueError, match="unsupported kind"):
        field(1, 1, 0)


# ─────────────────────── decode roundtrips ───────────────────────

def test_decode_single_varint():
    enc = field(1, 0, 99)
    assert decode(enc) == {1: 99}


def test_decode_single_string():
    enc = field(3, 2, b"hello")
    assert decode(enc) == {3: "hello"}


def test_decode_multiple_fields():
    enc = field(1, 0, 7) + field(2, 2, b"abc") + field(3, 0, 42)
    assert decode(enc) == {1: 7, 2: "abc", 3: 42}


def test_decode_nested_message():
    inner = field(1, 0, 100) + field(2, 2, b"xy")
    outer = field(5, 2, inner)
    decoded = decode(outer)
    assert decoded == {5: {1: 100, 2: "xy"}}


def test_decode_empty_bytes():
    assert decode(b"") == {}


def test_decode_handles_non_utf8_as_hex():
    # length-delim payload, который не декодируется как UTF-8 и не парсится как nested
    enc = field(1, 2, b"\xff\xfe\x00\x01")
    decoded = decode(enc)
    assert 1 in decoded
    # должно вернуть либо hex, либо nested с одним полем — главное что не упало
    assert decoded[1] != "" and decoded[1] is not None


def test_decode_truncated_input_returns_partial():
    # Ключ есть, но varint не заканчивается → возвращает то что успели распарсить
    truncated = b"\x08"  # tag=1, kind=0, без payload
    result = decode(truncated)
    assert isinstance(result, dict)


def test_decode_real_envelope_fragment():
    # Эмуляция "конверта": id=req-id (str), type=2 (varint)
    pkt = field(1, 0, 2) + field(2, 2, b"req-123")
    assert decode(pkt) == {1: 2, 2: "req-123"}


# ─────────────────────── decode: fixed32 (wire-type 5) ───────────────────────

def test_decode_fixed32_float():
    # roundtrip: field(kind=5) кодирует float32 LE — decode должен его прочитать
    enc = field(1, 5, 1.5)
    assert decode(enc) == {1: 1.5}


def test_decode_fixed32_does_not_stop_parsing():
    """Регрессия: раньше decode() молча обрывал разбор на wire-type 5,
    теряя все последующие поля."""
    enc = field(1, 5, 1.0) + field(2, 0, 7) + field(3, 2, b"ok")
    assert decode(enc) == {1: 1.0, 2: 7, 3: "ok"}


def test_decode_nested_fixed32_playback_speed():
    """Реальный кейс протокола: op=23 SET_PLAYBACK_SPEED — float32 в fixed32
    внутри вложенного сообщения (envelope 5 → op 23 → поле 1)."""
    pkt = field(5, 2, field(23, 2, field(1, 5, 1.5)))
    assert decode(pkt) == {5: {23: {1: 1.5}}}


def test_decode_truncated_fixed32_returns_partial():
    # fixed32 обрезан (2 байта из 4) — возвращаем уже распарсенное, без падения
    truncated = field(2, 0, 5) + b"\x0d\x00\x00"
    assert decode(truncated) == {2: 5}


# ─────────────────────── decode: multi-nested ───────────────────────

def test_decode_multi_level_nested():
    """Двухуровневая вложенность + сосед-строка — рекурсия после рефакторинга."""
    inner2 = field(1, 0, 3)
    inner1 = field(2, 2, inner2) + field(3, 0, 9)
    pkt = field(5, 2, inner1) + field(6, 2, b"tail")
    assert decode(pkt) == {5: {2: {1: 3}, 3: 9}, 6: "tail"}


# ─────────────────────── decode_repeated ───────────────────────

def test_decode_repeated_collects_duplicate_tags():
    # три раза тег 1 (varint) — decode_repeated собирает все, decode схлопнул бы
    enc = field(1, 0, 10) + field(1, 0, 20) + field(1, 0, 30)
    assert decode_repeated(enc) == {1: [10, 20, 30]}


def test_decode_repeated_single_tag_still_list():
    enc = field(5, 0, 99)
    assert decode_repeated(enc) == {5: [99]}


def test_decode_repeated_length_delim_returns_raw_bytes():
    # length-delimited значения возвращаются сырыми (без авто-рекурсии)
    enc = field(2, 2, b"hi") + field(2, 2, b"yo")
    assert decode_repeated(enc) == {2: [b"hi", b"yo"]}


def test_decode_repeated_empty():
    assert decode_repeated(b"") == {}


def test_decode_repeated_mixed_tags():
    enc = field(1, 2, b"x") + field(3, 0, 7) + field(1, 2, b"y")
    assert decode_repeated(enc) == {1: [b"x", b"y"], 3: [7]}

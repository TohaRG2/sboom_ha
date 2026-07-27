"""Клиент libiio (:30431) — аппаратные датчики платы колонки.

ВАЖНО: порт и датчики есть только у некоторых моделей (SberBoom R2 с
соответствующей платой). На колонках без них порт закрыт или устройств в
контексте нет — тогда probe() вернёт пустую capability, и сенсоры не
создаются. Никаких падений: любой сетевой сбой = «недоступно».

Протокол текстовый: команда + CRLF, ответ libiio = "<len>\\n<value>\\0".
- PRINT            → XML IIO-контекста (список devices/channels)
- READ <dev> INPUT <channel> <attr> → значение
"""
from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

DEFAULT_IIO_PORT = 30431
_CONNECT_TIMEOUT = 4.0
_READ_TIMEOUT = 3.0


@dataclass(frozen=True)
class IioCapability:
    """Что реально нашлось в IIO-контексте конкретной колонки."""

    illuminance_device: str | None = None  # id устройства с каналом illuminance0
    thermal_device: str | None = None      # id устройства с каналом temp1

    @property
    def has_illuminance(self) -> bool:
        return self.illuminance_device is not None

    @property
    def has_thermal(self) -> bool:
        return self.thermal_device is not None

    @property
    def any(self) -> bool:
        return self.has_illuminance or self.has_thermal


@dataclass
class IioReading:
    illuminance_lux: float | None = None
    soc_temp_c: float | None = None


def parse_context(xml: str) -> IioCapability:
    """Разбор XML IIO-контекста → какие датчики доступны.

    Ищем устройство с каналом `illuminance0` (датчик света) и с `temp1`
    (термодатчик SoC). Имена устройств (iio:device0, hwmon0) у разных плат
    различаются, поэтому идентифицируем по каналам, а не по id.
    """
    cap_illum: str | None = None
    cap_thermal: str | None = None
    start = xml.find("<context")
    if start < 0:
        return IioCapability()
    body = re.sub(r"<!DOCTYPE.*?\]>", "", xml, flags=re.S)
    body = body[body.find("<context"):]
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return IioCapability()
    for dev in root.findall("device"):
        chan_ids = {ch.get("id") for ch in dev.findall("channel")}
        dev_id = dev.get("id")
        if "illuminance0" in chan_ids and cap_illum is None:
            cap_illum = dev_id
        if "temp1" in chan_ids and cap_thermal is None:
            cap_thermal = dev_id
    return IioCapability(illuminance_device=cap_illum, thermal_device=cap_thermal)


def parse_read_value(raw: bytes) -> str | None:
    """Ответ libiio READ = "<len>\\n<value>\\0" → строковое значение.

    Отрицательный `len` — ошибка чтения (возвращаем None).
    """
    text = raw.decode("utf-8", "ignore")
    if "\n" not in text:
        return None
    head, _, tail = text.partition("\n")
    try:
        length = int(head.strip())
    except ValueError:
        return None
    if length < 0:
        return None
    return tail.replace("\x00", "").strip() or None


class IioClient:
    """Одноразовые соединения к libiio-демону (per-poll, дёшево)."""

    def __init__(self, host: str, port: int = DEFAULT_IIO_PORT) -> None:
        self._host = host
        self._port = port

    async def _session(self, commands: list[str]) -> list[bytes]:
        """Открыть соединение, выполнить команды, вернуть сырые ответы."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), _CONNECT_TIMEOUT
        )
        out: list[bytes] = []
        try:
            for cmd in commands:
                writer.write((cmd + "\r\n").encode())
                await writer.drain()
                # ответ короткий; читаем до паузы или до \0
                out.append(await self._read_response(reader))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        return out

    @staticmethod
    async def _read_response(reader: asyncio.StreamReader) -> bytes:
        buf = b""
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), _READ_TIMEOUT)
                if not chunk:
                    break
                buf += chunk
                if b"\x00" in buf or b"</context>" in buf:
                    break
        except TimeoutError:
            pass
        return buf

    async def async_probe(self) -> IioCapability:
        """Определить, какие датчики есть (или пустая capability при сбое)."""
        try:
            (xml_raw,) = await self._session(["PRINT"])
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            _LOGGER.debug("iio probe %s:%s недоступен: %s", self._host, self._port, exc)
            return IioCapability()
        return parse_context(xml_raw.decode("utf-8", "ignore"))

    async def async_read(self, cap: IioCapability) -> IioReading:
        """Считать значения доступных датчиков. Сбой поля → None в нём."""
        cmds: list[str] = []
        if cap.has_illuminance:
            cmds.append(f"READ {cap.illuminance_device} INPUT illuminance0 input")
        if cap.has_thermal:
            cmds.append(f"READ {cap.thermal_device} INPUT temp1 input")
        if not cmds:
            return IioReading()
        try:
            responses = await self._session(cmds)
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            _LOGGER.debug("iio read failed: %s", exc)
            return IioReading()

        reading = IioReading()
        idx = 0
        if cap.has_illuminance:
            val = parse_read_value(responses[idx])
            idx += 1
            reading.illuminance_lux = _to_float(val)
        if cap.has_thermal:
            val = parse_read_value(responses[idx])
            idx += 1
            milli = _to_float(val)
            reading.soc_temp_c = round(milli / 1000, 1) if milli is not None else None
        return reading


def _to_float(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None

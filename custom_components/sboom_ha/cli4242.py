"""Клиент debug-CLI колонки (:4242) — инвентарь Zigbee и Matter.

ВАЖНО: CLI есть только у моделей с локальным Zigbee-координатором
(SberBoom R2). На остальных порт закрыт → probe вернёт None, сенсор не
создаётся. CLI даёт ТОЛЬКО инвентарь/топологию (read-only): состояние
устройств (on/off) и управление он не отдаёт — это идёт через облако Sber.

Протокол текстовый: команда + LF, ответ — ASCII-таблица `zigbee list`.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

DEFAULT_CLI_PORT = 4242
_CONNECT_TIMEOUT = 4.0
_READ_TIMEOUT = 3.0


@dataclass(frozen=True)
class ZigbeeDevice:
    ieee: str
    nwk: str
    state: str
    manufacturer: str
    model: str
    power_source: str
    rssi: int | None


def parse_zigbee_list(text: str) -> list[ZigbeeDevice]:
    """ASCII-таблица `zigbee list` → список устройств.

    Заголовок: | # | IEEE Address | NWK | XID | State | Manufacturer name |
    Model ID | Pwr src | OTA perm. | App | Has OTA | RSSI |
    Строки-разделители (|---|) и пустые пропускаем.
    """
    devices: list[ZigbeeDevice] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= {"|", "-", " "}:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 12 or cells[0] in ("#", ""):
            continue  # заголовок или битая строка
        try:
            rssi = int(cells[11])
        except (ValueError, IndexError):
            rssi = None
        devices.append(
            ZigbeeDevice(
                ieee=cells[1],
                nwk=cells[2],
                state=cells[4],
                manufacturer=cells[5],
                model=cells[6],
                power_source=cells[7],
                rssi=rssi,
            )
        )
    return devices


@dataclass(frozen=True)
class MatterDevice:
    node_id: str
    serial: str
    model: str
    rssi: int | None
    xid: str = ""


def _is_node_id(s: str) -> bool:
    """NodeId — 16-значный hex. Отсекает заголовок 'NodeId' и разделители '---'."""
    return len(s) >= 8 and all(c in "0123456789abcdefABCDEF" for c in s)


def parse_matter_list(text: str) -> list[MatterDevice]:
    """ASCII-таблица `matter list` → список Matter-устройств.

    Заголовок: | NodeId | XID | Serial number | Model ID | Can report RSSI | RSSI |
    Строка данных распознаётся по NodeId (hex) в первой колонке — заголовок
    ('NodeId') и разделители ('|---|') отсекаются автоматически. Пустой
    список ('...is empty') → [].
    """
    devices: list[MatterDevice] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or not _is_node_id(cells[0]):
            continue  # заголовок / разделитель / битая строка
        try:
            rssi = int(cells[5])
        except (ValueError, IndexError):
            rssi = None
        devices.append(
            MatterDevice(
                node_id=cells[0], xid=cells[1], serial=cells[2],
                model=cells[3], rssi=rssi,
            )
        )
    return devices


def matter_count(text: str) -> int:
    """Число Matter-устройств из вывода `matter list` (0 для пустого списка)."""
    return len(parse_matter_list(text))


class Cli4242Client:
    """Одноразовые соединения к debug-CLI :4242 (per-poll).

    Обслуживает и Zigbee, и Matter — общий текстовый CLI на одном порту.
    """

    def __init__(self, host: str, port: int = DEFAULT_CLI_PORT) -> None:
        self._host = host
        self._port = port

    async def _run(self, command: str) -> str | None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), _CONNECT_TIMEOUT
            )
        except (OSError, TimeoutError) as exc:
            _LOGGER.debug("zigbee cli %s:%s недоступен: %s", self._host, self._port, exc)
            return None
        try:
            # проглотить приветствие «CLI connection established»
            await self._drain(reader, 0.6)
            writer.write((command + "\n").encode())
            await writer.drain()
            return (await self._drain(reader, _READ_TIMEOUT)).decode("utf-8", "ignore")
        except (OSError, TimeoutError) as exc:
            _LOGGER.debug("zigbee cli command failed: %s", exc)
            return None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    async def _drain(reader: asyncio.StreamReader, timeout: float) -> bytes:
        buf = b""
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), timeout)
                if not chunk:
                    break
                buf += chunk
        except TimeoutError:
            pass
        return buf

    async def async_probe(self) -> bool:
        """CLI доступен и это Zigbee-координатор? (валидный ответ version)."""
        resp = await self._run("zigbee version")
        return resp is not None and "version" in resp.lower()

    async def async_list_devices(self) -> list[ZigbeeDevice] | None:
        """Инвентарь Zigbee-устройств; None при недоступности CLI."""
        resp = await self._run("zigbee list")
        if resp is None:
            return None
        return parse_zigbee_list(resp)

    async def async_matter_probe(self) -> bool:
        """Matter-контроллер доступен? — `matter list` ответил чем угодно, кроме
        ошибки «команды нет».

        ВАЖНО: раньше проверялось `"matter" in resp`, но реальная таблица
        устройства (`| NodeId | XID | Serial ... |`) слова «matter» НЕ содержит,
        из-за чего с подключённым Matter-устройством probe ложно возвращал False
        и сенсор пропадал. Теперь: есть ответ и это НЕ error-вывод CLI.
        """
        resp = await self._run("matter list")
        if resp is None:
            return False
        low = resp.lower()
        return "not found" not in low and "valid commands" not in low

    async def async_matter_list(self) -> tuple[list[MatterDevice], str] | None:
        """Инвентарь Matter → (список устройств, сырой вывод). None — CLI недоступен."""
        resp = await self._run("matter list")
        if resp is None:
            return None
        return parse_matter_list(resp), resp.strip()

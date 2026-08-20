"""Мониторинг аппаратной подсистемы колонки: libiio-датчики + Zigbee/Matter CLI.

Выделен из SboomCoordinator (SRP, аудит #20): у координатора своя
ответственность — WS-сессия и now-playing. Аппаратная часть (есть только у
части моделей, напр. R2) живёт здесь: свои транспорты (libiio XML, debug-CLI
:4242), свой probe и свой цикл опроса с собственным темпом.

По образцу LyricsManager/CoverManager: координатор дергает `async_probe()`
однократно и `async_poll()` из своего цикла; сенсоры читают снапшот-поля
через `coordinator.hw.*`.
"""
from __future__ import annotations

import asyncio
import logging

from .cli4242 import Cli4242Client, MatterDevice, ZigbeeDevice
from .iio_client import IioCapability, IioClient, IioReading

_LOGGER = logging.getLogger(__name__)

# Zigbee/Matter-инвентарь меняется медленно и открывает CLI-сессию — опрашиваем
# раз в N тиков libiio (который дёшев и идёт каждый тик).
_HEAVY_POLL_EVERY = 20


class HwMonitor:
    """Владеет состоянием и опросом аппаратных датчиков/CLI-инвентаря."""

    def __init__(self, host: str) -> None:
        self._iio_client = IioClient(host)
        self._cli = Cli4242Client(host)
        self.iio_cap: IioCapability = IioCapability()
        self.has_zigbee_cli: bool = False
        self.has_matter_cli: bool = False
        self.iio_reading: IioReading = IioReading()
        self.zigbee_devices: list[ZigbeeDevice] = []
        self.matter_devices: list[MatterDevice] = []
        self._poll_tick = 0

    @property
    def any_capability(self) -> bool:
        """Есть ли у модели хоть один аппаратный источник (иначе poll не нужен)."""
        return self.iio_cap.any or self.has_zigbee_cli or self.has_matter_cli

    async def async_probe(self) -> None:
        """Один раз при старте: какие аппаратные источники есть у модели.

        Закрытый порт → мгновенный refused, setup не тормозит; probe'ы
        параллельны. Сбой любого — просто «нет capability».
        """
        try:
            self.iio_cap, self.has_zigbee_cli, self.has_matter_cli = await asyncio.gather(
                self._iio_client.async_probe(),
                self._cli.async_probe(),
                self._cli.async_matter_probe(),
            )
        except Exception as exc:
            _LOGGER.debug("hw capability probe failed: %s", exc)
            return
        _LOGGER.debug(
            "hw capabilities: illuminance=%s thermal=%s zigbee_cli=%s matter_cli=%s",
            self.iio_cap.has_illuminance, self.iio_cap.has_thermal,
            self.has_zigbee_cli, self.has_matter_cli,
        )
        if self.iio_cap.any:
            self.iio_reading = await self._iio_client.async_read(self.iio_cap)
        if self.has_zigbee_cli:
            self.zigbee_devices = await self._cli.async_list_devices() or []
        if self.has_matter_cli:
            await self._poll_matter()

    async def async_poll(self) -> None:
        """Опрос датчиков/CLI. libiio — каждый тик (дёшево), инвентарь — реже."""
        if self.iio_cap.any:
            self.iio_reading = await self._iio_client.async_read(self.iio_cap)
        if self.has_zigbee_cli and self._poll_tick % _HEAVY_POLL_EVERY == 0:
            self.zigbee_devices = await self._cli.async_list_devices() or []
        if self.has_matter_cli and self._poll_tick % _HEAVY_POLL_EVERY == 0:
            await self._poll_matter()
        self._poll_tick += 1

    async def _poll_matter(self) -> None:
        res = await self._cli.async_matter_list()
        if res is not None:
            self.matter_devices, _raw = res

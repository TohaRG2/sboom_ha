"""Тесты config_flow: zeroconf discovery, healing/soft-миграции, reconfigure.

Каждый тест охраняет конкретную регрессию, найденную в ревью:
дубликаты entries при повторном discovery, захват чужого entry по
совпавшему IP, «вечно занятый» legacy unique_id.

Инфраструктура: чистые HA-stubs (tests/_ha_stubs.py), без
pytest-homeassistant-custom-component. AbortFlow, который в реальном HA
flow-manager превращает в abort-результат, здесь ловится обёрткой _run().
"""
from __future__ import annotations

from typing import Any, ClassVar

from tests._ha_stubs import (
    AbortFlow,
    ConfigEntry,
    HomeAssistant,
    ZeroconfServiceInfo,
    install_stubs,
)

install_stubs()

from sboom_ha import config_flow as cf  # noqa: E402
from sboom_ha.config_flow import SboomConfigFlow  # noqa: E402
from sboom_ha.const import (  # noqa: E402
    CONF_CLIENT_ID,
    CONF_CLIENT_NAME,
    CONF_DEVICE_FIRMWARE,
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_HOST,
    CONF_PIN_ACCESS_TOKEN,
    CONF_PORT,
    DEFAULT_PORT,
    DOMAIN,
)

# ───────────────────────── helpers ─────────────────────────

async def _run(coro) -> dict[str, Any]:
    """Эмуляция flow-manager'а HA: AbortFlow → abort-результат."""
    try:
        return await coro
    except AbortFlow as err:
        return {"type": "abort", "reason": err.reason}


def _make_flow(hass: HomeAssistant, context: dict | None = None) -> SboomConfigFlow:
    flow = SboomConfigFlow()
    flow.hass = hass
    flow.context = dict(context or {})
    return flow


def _add_entry(
    hass: HomeAssistant,
    *,
    unique_id: str,
    host: str,
    device_id: str | None = None,
    device_name: str | None = None,
    entry_id: str = "entry-1",
    port: int = DEFAULT_PORT,
) -> ConfigEntry:
    """Существующий config entry в стиле реальных данных интеграции."""
    data: dict[str, Any] = {
        CONF_HOST: host,
        CONF_PORT: port,
        CONF_CLIENT_ID: "00000000-0000-0000-0000-000000000001",
        CONF_CLIENT_NAME: "Home Assistant",
        CONF_PIN_ACCESS_TOKEN: "secret-pin-token",
    }
    if device_id is not None:
        data[CONF_DEVICE_ID] = device_id
    if device_name is not None:
        data[CONF_DEVICE_NAME] = device_name
    entry = ConfigEntry(data=data, entry_id=entry_id, unique_id=unique_id)
    hass.config_entries.add(entry)
    return entry


def _zeroconf_info(
    host: str,
    *,
    device_id: str | None = None,
    port: int = DEFAULT_PORT,
    name: str = "SberBoom Kitchen",
    model: str = "sberboom-r2",
    firmware: str = "1.75.1",
) -> ZeroconfServiceInfo:
    """mDNS-анонс колонки: properties как в реальном zeroconf — bytes."""
    props: dict[Any, Any] = {
        b"name": name.encode(),
        b"type": model.encode(),
        b"v": firmware.encode(),
    }
    if device_id is not None:
        props[b"id"] = device_id.encode()
    return ZeroconfServiceInfo(host=host, port=port, properties=props)


class _FakeSpeakerClient:
    """Замена SberSpeakerClient для reconfigure: connect всегда успешен."""

    instances: ClassVar[list[_FakeSpeakerClient]] = []

    def __init__(self, *, host: str, port: int, client_id: str, client_name: str) -> None:
        self.host = host
        self.port = port
        self.connected = False
        self.closed = False
        _FakeSpeakerClient.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True


# ───────────────────────── zeroconf: healing-миграция ─────────────────────────

async def test_zeroconf_heals_manual_entry_to_device_id_unique_id():
    """Регрессия: entry, добавленный вручную (unique_id по IP, без device_id),
    при discovery той же колонки должен МИГРИРОВАТЬ, а не порождать дубликат.

    Без healing-ветки flow прошёл бы к discovery_confirm, и подтверждение
    создало бы второй entry той же колонки с повторным pairing.
    """
    hass = HomeAssistant()
    entry = _add_entry(
        hass, unique_id=f"{DOMAIN}_192.0.2.10", host="192.0.2.10",
    )  # ручной entry: device_id в data отсутствует

    flow = _make_flow(hass)
    result = await _run(flow.async_step_zeroconf(
        _zeroconf_info("192.0.2.10", device_id="DEV123")
    ))

    # Flow прерван — дубликат не предлагается
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries()) == 1

    # Entry мигрировал на device_id-based unique_id
    assert entry.unique_id == f"{DOMAIN}_DEV123"
    assert entry.data[CONF_DEVICE_ID] == "DEV123"
    assert entry.data[CONF_HOST] == "192.0.2.10"
    assert entry.data[CONF_DEVICE_MODEL] == "sberboom-r2"
    assert entry.data[CONF_DEVICE_FIRMWARE] == "1.75.1"
    # device_name заполнен из discovery (в legacy data его не было)
    assert entry.data[CONF_DEVICE_NAME] == "SberBoom Kitchen"
    # Reload запланирован — интеграция подхватит новые данные
    assert hass.config_entries.reloaded == [entry.entry_id]


async def test_zeroconf_does_not_hijack_entry_of_other_speaker():
    """Регрессия (HIGH): колонка A переехала (DHCP), её старый IP получила
    колонка B. Discovery B по этому IP НЕ должен перезаписывать identity
    entry A — иначе entry A превратился бы в entry колонки B (unique_id,
    device_id, token — всё чужое).

    Ожидание: entry A не тронут, flow идёт дальше к discovery_confirm
    (колонка B — действительно новое устройство).
    """
    hass = HomeAssistant()
    entry_a = _add_entry(
        hass,
        unique_id=f"{DOMAIN}_A",
        host="192.0.2.50",  # устаревший host: теперь на нём живёт колонка B
        device_id="A",
        device_name="Speaker A",
        entry_id="entry-a",
    )

    flow = _make_flow(hass)
    result = await _run(flow.async_step_zeroconf(
        _zeroconf_info("192.0.2.50", device_id="B", name="Speaker B")
    ))

    # Flow НЕ abort'ится по healing — новая колонка идёт на подтверждение
    assert result["type"] == "form"
    assert result["step_id"] == "discovery_confirm"

    # Entry A полностью нетронут
    assert entry_a.unique_id == f"{DOMAIN}_A"
    assert entry_a.data[CONF_DEVICE_ID] == "A"
    assert entry_a.data[CONF_HOST] == "192.0.2.50"
    assert entry_a.data[CONF_DEVICE_NAME] == "Speaker A"
    assert hass.config_entries.reloaded == []


async def test_zeroconf_soft_migration_updates_host_on_ip_change():
    """Регрессия: колонка сменила IP (DHCP) — повторный discovery обязан
    обновить host в data уже настроенного entry и перезагрузить интеграцию.
    Без этого client навсегда ходил бы на мёртвый адрес.
    """
    hass = HomeAssistant()
    entry = _add_entry(
        hass,
        unique_id=f"{DOMAIN}_DEVX",
        host="192.0.2.10",
        device_id="DEVX",
        device_name="Speaker X",
    )

    flow = _make_flow(hass)
    result = await _run(flow.async_step_zeroconf(
        _zeroconf_info("192.0.2.99", device_id="DEVX")
    ))

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.0.2.99"
    assert entry.unique_id == f"{DOMAIN}_DEVX"  # identity не меняется
    assert hass.config_entries.reloaded == [entry.entry_id]
    assert len(hass.config_entries.async_entries()) == 1


async def test_zeroconf_without_device_id_aborts_incomplete():
    """Регрессия: mDNS-анонс без `id` в properties не должен доходить до
    unique_id/healing-логики (unique_id получился бы `sboom_ha_None`) —
    flow обязан прерваться с incomplete_discovery.
    """
    hass = HomeAssistant()
    flow = _make_flow(hass)

    result = await _run(flow.async_step_zeroconf(
        _zeroconf_info("192.0.2.10", device_id=None)
    ))

    assert result["type"] == "abort"
    assert result["reason"] == "incomplete_discovery"
    assert flow.unique_id is None  # unique_id не был установлен


# ───────────────────────── manual (user) step ─────────────────────────

async def test_user_step_aborts_for_host_of_discovered_entry():
    """Регрессия: entry, созданный через discovery, имеет unique_id по
    device_id — host-based unique_id ручного шага его НЕ поймает. Без
    сверки по host ручной ввод IP уже настроенной колонки создал бы
    дубликат с повторным pairing.
    """
    hass = HomeAssistant()
    _add_entry(
        hass,
        unique_id=f"{DOMAIN}_DEVZ",  # device_id-based: unique_id-проверка не спасёт
        host="192.0.2.20",
        device_id="DEVZ",
    )

    flow = _make_flow(hass)
    result = await _run(flow.async_step_user({CONF_HOST: "192.0.2.20"}))

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries()) == 1


# ───────────────────────── reconfigure ─────────────────────────

async def test_reconfigure_migrates_legacy_host_based_unique_id(monkeypatch):
    """Регрессия: legacy unique_id `sboom_ha_<старый IP>` без обновления при
    reconfigure навсегда «занимает» старый адрес — добавление другой колонки
    на нём упиралось бы в ложный already_configured.
    """
    hass = HomeAssistant()
    entry = _add_entry(
        hass, unique_id=f"{DOMAIN}_192.0.2.10", host="192.0.2.10",
    )
    _FakeSpeakerClient.instances = []
    monkeypatch.setattr(cf, "SberSpeakerClient", _FakeSpeakerClient)

    flow = _make_flow(hass, context={"entry_id": entry.entry_id, "source": "reconfigure"})
    result = await _run(flow.async_step_reconfigure({CONF_HOST: "192.0.2.99"}))

    assert result["type"] == "abort"
    # unique_id переехал на новый host — старый адрес освобождён
    assert entry.unique_id == f"{DOMAIN}_192.0.2.99"
    assert entry.data[CONF_HOST] == "192.0.2.99"
    assert hass.config_entries.reloaded == [entry.entry_id]
    # Валидация действительно ходила на НОВЫЙ адрес и соединение закрыто
    (client,) = _FakeSpeakerClient.instances
    assert client.host == "192.0.2.99"
    assert client.connected and client.closed


async def test_reconfigure_keeps_device_id_based_unique_id(monkeypatch):
    """Контроль к миграции legacy unique_id: device_id-based unique_id при
    reconfigure меняться НЕ должен (identity колонки не зависит от IP).
    Регрессия — если бы условие миграции сравнивало не с host-based шаблоном,
    смена IP переписала бы стабильный unique_id.
    """
    hass = HomeAssistant()
    entry = _add_entry(
        hass,
        unique_id=f"{DOMAIN}_DEVX",
        host="192.0.2.10",
        device_id="DEVX",
    )
    _FakeSpeakerClient.instances = []
    monkeypatch.setattr(cf, "SberSpeakerClient", _FakeSpeakerClient)

    flow = _make_flow(hass, context={"entry_id": entry.entry_id, "source": "reconfigure"})
    result = await _run(flow.async_step_reconfigure({CONF_HOST: "192.0.2.99"}))

    assert result["type"] == "abort"
    assert entry.unique_id == f"{DOMAIN}_DEVX"  # не тронут
    assert entry.data[CONF_HOST] == "192.0.2.99"  # host при этом обновлён
    assert hass.config_entries.reloaded == [entry.entry_id]

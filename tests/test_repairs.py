"""Тесты Repairs platform: issue создаётся при долгой недоступности, удаляется при reconnect."""
from __future__ import annotations

from sboom_ha.const import DOMAIN
from sboom_ha.coordinator import UNREACHABLE_ISSUE_THRESHOLD_SEC

from tests._fakes import build_coordinator, make_state, make_track
from tests._ha_stubs import _ISSUE_REGISTRY, _IssueSeverity


def _issue_key(coord) -> tuple[str, str]:
    return (DOMAIN, f"unreachable_{coord.entry.entry_id}")


def setup_function(_func):
    """Перед каждым тестом — чистый registry."""
    _ISSUE_REGISTRY.clear()


# ─────────────────── creation ───────────────────

def test_no_issue_when_recently_disconnected():
    """Сразу после disconnect issue ещё не создаётся (threshold не истёк)."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord._set_connected(False)  # disconnect — _unreachable_since = now
    coord._maybe_create_unreachable_issue()
    assert _issue_key(coord) not in _ISSUE_REGISTRY


def test_issue_created_when_threshold_exceeded(monkeypatch):
    """Если прошло > UNREACHABLE_ISSUE_THRESHOLD_SEC — issue создаётся."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord._set_connected(False)

    # эмулируем что прошло threshold + 1 секунда
    import time as time_mod
    real_monotonic = time_mod.monotonic
    coord._unreachable_since = real_monotonic() - UNREACHABLE_ISSUE_THRESHOLD_SEC - 1

    coord._maybe_create_unreachable_issue()

    assert _issue_key(coord) in _ISSUE_REGISTRY
    issue = _ISSUE_REGISTRY[_issue_key(coord)]
    assert issue["is_fixable"] is True
    assert issue["severity"] == _IssueSeverity.WARNING
    assert issue["translation_key"] == "speaker_unreachable"
    assert "name" in issue["translation_placeholders"]
    assert "minutes" in issue["translation_placeholders"]


def test_issue_not_created_when_connected():
    """Не создавать issue для connected coordinator."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord._maybe_create_unreachable_issue()
    assert _issue_key(coord) not in _ISSUE_REGISTRY


# ─────────────────── deletion ───────────────────

def test_issue_cleared_on_reconnect():
    """Когда колонка возвращается online — issue удаляется."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord._set_connected(False)

    # Манипулируем _unreachable_since и форсим issue
    import time as time_mod
    coord._unreachable_since = time_mod.monotonic() - UNREACHABLE_ISSUE_THRESHOLD_SEC - 1
    coord._maybe_create_unreachable_issue()
    assert _issue_key(coord) in _ISSUE_REGISTRY

    # Reconnect → должно очистить
    coord._set_connected(True)
    assert _issue_key(coord) not in _ISSUE_REGISTRY


def test_unreachable_since_resets_on_reconnect():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord._set_connected(False)
    assert coord._unreachable_since is not None
    coord._set_connected(True)
    assert coord._unreachable_since is None


# ─────────────────── issue data (entry_id для fix flow) ───────────────────

def test_issue_carries_entry_id():
    """Issue содержит entry_id — без него fix-flow не найдёт entry."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord._set_connected(False)

    import time as time_mod
    coord._unreachable_since = time_mod.monotonic() - UNREACHABLE_ISSUE_THRESHOLD_SEC - 1
    coord._maybe_create_unreachable_issue()

    issue = _ISSUE_REGISTRY[_issue_key(coord)]
    assert issue["data"] == {"entry_id": coord.entry.entry_id}


# ─────────────────── fix flow (смена IP из issue) ───────────────────

from sboom_ha import repairs as repairs_mod  # noqa: E402
from sboom_ha.const import CONF_HOST, CONF_PORT  # noqa: E402

from tests._fakes import make_entry  # noqa: E402
from tests._ha_stubs import HomeAssistant  # noqa: E402


class _FakeClient:
    """Подменяет SberSpeakerClient в repairs: connect либо ок, либо OSError."""

    fail_connect = False

    def __init__(self, *, host, port, client_id, client_name):
        self.host = host
        self.port = port
        self.closed = False

    async def connect(self):
        if _FakeClient.fail_connect:
            raise OSError("no route to host")

    async def close(self):
        self.closed = True


def _build_flow(monkeypatch, *, entry=None, data=...):
    monkeypatch.setattr(repairs_mod, "SberSpeakerClient", _FakeClient)
    _FakeClient.fail_connect = False
    hass = HomeAssistant()
    if entry is not None:
        hass.config_entries.add(entry)
    if data is ...:
        data = {"entry_id": entry.entry_id} if entry is not None else None
    flow = repairs_mod.SpeakerUnreachableRepairFlow(hass, data)
    return hass, flow


async def test_fix_flow_shows_form_with_current_host(monkeypatch):
    entry = make_entry(host="192.0.2.10")
    _hass, flow = _build_flow(monkeypatch, entry=entry)

    result = await flow.async_step_init(None)

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {}


async def test_fix_flow_updates_host_and_reloads(monkeypatch):
    """Успешный connect по новому IP → entry обновлён, reload запланирован."""
    entry = make_entry(host="192.0.2.10")
    hass, flow = _build_flow(monkeypatch, entry=entry)

    result = await flow.async_step_init({CONF_HOST: "192.0.2.99", CONF_PORT: 20000})

    assert result["type"] == "create_entry"
    assert entry.data[CONF_HOST] == "192.0.2.99"
    assert entry.data[CONF_PORT] == 20000
    # pair-токен и device_id не тронуты
    assert entry.data["pin_access_token"] == "test-pin-token-1234567890abcdef"
    assert entry.data["device_id"] == "test-device-id"
    assert hass.config_entries.reloaded == [entry.entry_id]


async def test_fix_flow_migrates_legacy_host_unique_id(monkeypatch):
    """Legacy host-based unique_id мигрирует на новый host при смене IP.

    Без миграции старый unique_id навсегда «занимает» прежний адрес —
    добавление другой колонки на нём упрётся в ложный already_configured.
    """
    entry = make_entry(host="192.0.2.10")
    entry.unique_id = f"{DOMAIN}_192.0.2.10"
    hass, flow = _build_flow(monkeypatch, entry=entry)

    result = await flow.async_step_init({CONF_HOST: "192.0.2.99"})

    assert result["type"] == "create_entry"
    assert entry.unique_id == f"{DOMAIN}_192.0.2.99"
    assert entry.data[CONF_HOST] == "192.0.2.99"
    assert hass.config_entries.reloaded == [entry.entry_id]


async def test_fix_flow_keeps_device_id_unique_id(monkeypatch):
    """device_id-based unique_id (не host-based) при смене host НЕ трогается."""
    entry = make_entry(host="192.0.2.10")
    entry.unique_id = f"{DOMAIN}_test-device-id"
    _hass, flow = _build_flow(monkeypatch, entry=entry)

    result = await flow.async_step_init({CONF_HOST: "192.0.2.99"})

    assert result["type"] == "create_entry"
    assert entry.unique_id == f"{DOMAIN}_test-device-id"
    assert entry.data[CONF_HOST] == "192.0.2.99"


async def test_fix_flow_connect_failure_keeps_entry(monkeypatch):
    """Колонка не отвечает по введённому адресу → ошибка, entry не изменён."""
    entry = make_entry(host="192.0.2.10")
    hass, flow = _build_flow(monkeypatch, entry=entry)
    _FakeClient.fail_connect = True

    result = await flow.async_step_init({CONF_HOST: "192.0.2.99"})

    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF_HOST] == "192.0.2.10"
    assert hass.config_entries.reloaded == []


async def test_fix_flow_without_entry_id_degrades_to_confirm(monkeypatch):
    """Issue от старой версии (data=None) → flow просто закрывает issue."""
    _hass, flow = _build_flow(monkeypatch, entry=None, data=None)

    result = await flow.async_step_init(None)

    assert result["type"] == "create_entry"


async def test_fix_flow_with_stale_entry_id_degrades_to_confirm(monkeypatch):
    """entry уже удалён из HA → flow закрывает issue без ошибок."""
    _hass, flow = _build_flow(monkeypatch, entry=None, data={"entry_id": "ghost"})

    result = await flow.async_step_init(None)

    assert result["type"] == "create_entry"


async def test_create_fix_flow_factory(monkeypatch):
    """Фабрика: unreachable_* → кастомный flow, прочее → ConfirmRepairFlow."""
    monkeypatch.setattr(repairs_mod, "SberSpeakerClient", _FakeClient)
    hass = HomeAssistant()

    flow = await repairs_mod.async_create_fix_flow(hass, "unreachable_abc", None)
    assert isinstance(flow, repairs_mod.SpeakerUnreachableRepairFlow)

    other = await repairs_mod.async_create_fix_flow(hass, "some_other_issue", None)
    assert not isinstance(other, repairs_mod.SpeakerUnreachableRepairFlow)

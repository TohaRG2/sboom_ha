"""Минимальные stub'ы HA-классов для unit-тестирования entity без установки homeassistant.

Регистрируем поддельные модули в sys.modules ДО импорта sboom_ha.* — тогда любой
`from homeassistant.components.media_player import MediaPlayerEntity` в коде проекта
получит наш заглушечный класс.

Покрытие: только то, что реально используют тестируемые модули. Расширять по мере
добавления тестов на новые платформы.
"""
from __future__ import annotations

# ruff: noqa: UP042  # str+Enum в стабах намеренно: совместимо с py3.11 без StrEnum
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, IntFlag
from typing import Any

# ── exceptions ───────────────────────────────────────────────────────────

class HomeAssistantError(Exception):
    """stub homeassistant.exceptions.HomeAssistantError.

    Принимает translation_* kwargs как настоящий HA, чтобы код,
    бросающий переводимые ошибки, тестировался без реального HA.
    """

    def __init__(
        self,
        *args: Any,
        translation_domain: str | None = None,
        translation_key: str | None = None,
        translation_placeholders: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


class ConfigEntryNotReady(HomeAssistantError):
    """stub: setup не готов, HA повторит попытку."""


class ServiceValidationError(HomeAssistantError):
    """Stub: ошибка валидации service call."""

    def __init__(self, *args, translation_domain=None, translation_key=None, **kwargs):
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key


class ConfigEntryAuthFailed(HomeAssistantError):
    """stub: требуется переавторизация."""


# ── core ─────────────────────────────────────────────────────────────────

class _FakeBus:
    """Сборщик event'ов вместо реальной шины событий HA."""

    def __init__(self) -> None:
        self.fired: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, event_data: dict[str, Any] | None = None) -> None:
        self.fired.append((event_type, dict(event_data or {})))


def callback(fn):
    """Stub @callback decorator (no-op)."""
    return fn


@dataclass
class ServiceCall:
    """Stub для ServiceCall."""
    domain: str = ""
    service: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class _FakeDeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, Any] = {}

    def async_get(self, device_id: str):
        return self._devices.get(device_id)

    def register(self, device) -> None:
        self._devices[device.id] = device


_DR_INSTANCE: _FakeDeviceRegistry | None = None


def _async_get_device_registry(hass) -> _FakeDeviceRegistry:
    global _DR_INSTANCE
    if _DR_INSTANCE is None:
        _DR_INSTANCE = _FakeDeviceRegistry()
    return _DR_INSTANCE


class _FakeServices:
    def __init__(self) -> None:
        self._registered: dict[tuple[str, str], Any] = {}

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self._registered

    def async_register(self, domain: str, service: str, handler, schema=None) -> None:
        self._registered[(domain, service)] = handler
        self._schemas = getattr(self, "_schemas", {})
        self._schemas[(domain, service)] = schema


class _FakeConfigEntries:
    """Stub hass.config_entries: get/update/schedule_reload для repairs-flow."""

    def __init__(self) -> None:
        self._entries: dict[str, Any] = {}
        self.reloaded: list[str] = []

    def add(self, entry) -> None:
        self._entries[entry.entry_id] = entry

    def async_get_entry(self, entry_id: str):
        return self._entries.get(entry_id)

    def async_update_entry(
        self, entry, *, data=None, options=None, unique_id=None, title=None
    ) -> None:
        if data is not None:
            entry.data = dict(data)
        if options is not None:
            entry.options = dict(options)
        if unique_id is not None:
            entry.unique_id = unique_id
        if title is not None:
            entry.title = title

    def async_schedule_reload(self, entry_id: str) -> None:
        self.reloaded.append(entry_id)

    def async_entries(self, domain=None):
        return list(self._entries.values())


class HomeAssistant:
    """Stub HA: bus + data + services + config_entries + create_background_task."""

    def __init__(self) -> None:
        self.bus = _FakeBus()
        self.services = _FakeServices()
        self.config_entries = _FakeConfigEntries()
        self.data: dict[str, Any] = {}
        self._tasks: list[Any] = []

    def async_create_background_task(self, coro, name: str | None = None):
        # Не запускаем — храним для интроспекции и закрываем coroutine.
        try:
            coro.close()
        except Exception:  # pragma: no cover
            pass
        self._tasks.append(name)
        return None


# ── config_entries ───────────────────────────────────────────────────────

class ConfigEntry:
    def __init__(
        self,
        data: dict[str, Any] | None = None,
        entry_id: str = "test_entry",
        options: dict[str, Any] | None = None,
        title: str = "Test Entry",
        version: int = 1,
        minor_version: int = 1,
        unique_id: str | None = None,
    ) -> None:
        self.data = dict(data or {})
        self.options = dict(options or {})
        self.entry_id = entry_id
        self.title = title
        self.version = version
        self.minor_version = minor_version
        self.unique_id = unique_id

        self.runtime_data = None

    def async_on_unload(self, fn) -> None:
        pass

    def add_update_listener(self, fn):
        return lambda: None

    def async_create_background_task(self, hass, coro, name=None, **kwargs):
        # Как и HomeAssistant-стаб: не запускаем, закрываем coroutine.
        try:
            coro.close()
        except Exception:  # pragma: no cover
            pass
        return None

    def async_start_reauth(self, hass) -> None:
        self.reauth_started = True


SOURCE_IGNORE = "ignore"

_UNDEFINED = object()


class AbortFlow(HomeAssistantError):
    """Stub homeassistant.data_entry_flow.AbortFlow.

    Реальный flow-manager HA ловит это исключение и превращает его в
    abort-результат. В unit-тестах без manager'а исключение долетает до
    теста — ловить его там (или через обёртку) и есть контракт.
    """

    def __init__(self, reason: str, description_placeholders: dict | None = None) -> None:
        super().__init__(f"Flow aborted: {reason}")
        self.reason = reason
        self.description_placeholders = description_placeholders


class _FlowHandlerBase:
    """Общие async_show_form/async_create_entry/async_abort для Config/Options flow."""

    def async_show_form(
        self,
        *,
        step_id: str | None = None,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
        description_placeholders: dict[str, str] | None = None,
        last_step: bool | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": dict(errors or {}),
            "description_placeholders": dict(description_placeholders or {}),
        }

    def async_create_entry(
        self,
        *,
        title: str = "",
        data: dict | None = None,
        options: dict | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "type": "create_entry",
            "title": title,
            "data": dict(data or {}),
            "options": dict(options or {}),
        }

    def async_abort(
        self, *, reason: str, description_placeholders: dict | None = None
    ) -> dict[str, Any]:
        return {
            "type": "abort",
            "reason": reason,
            "description_placeholders": dict(description_placeholders or {}),
        }


class ConfigFlow(_FlowHandlerBase):
    """Рабочий минимум config_entries.ConfigFlow для unit-тестов config_flow.py.

    Семантика unique_id / _abort_if_unique_id_configured /
    _async_abort_entries_match повторяет HA. `hass` и `context` в реальном HA
    проставляет flow-manager после инстанцирования — в тестах присваивать
    вручную: `flow.hass = hass; flow.context = {...}`.
    """

    hass: Any = None

    def __init_subclass__(cls, **kwargs) -> None:
        # Поглощаем `domain=DOMAIN` kwarg в `class SboomConfigFlow(ConfigFlow, domain=...)`.
        cls._domain = kwargs.pop("domain", None)
        super().__init_subclass__(**kwargs)

    # context/unique_id — как в HA: context назначается менеджером,
    # unique_id живёт внутри context. Ленивое создание, потому что
    # подклассы не зовут super().__init__().
    @property
    def context(self) -> dict[str, Any]:
        if not hasattr(self, "_context"):
            self._context: dict[str, Any] = {}
        return self._context

    @context.setter
    def context(self, value: dict[str, Any]) -> None:
        self._context = dict(value)

    @property
    def unique_id(self) -> str | None:
        return self.context.get("unique_id")

    async def async_set_unique_id(self, unique_id: str | None = None, *, raise_on_progress: bool = True):
        self.context["unique_id"] = unique_id
        if unique_id is None:
            return None
        for entry in self._async_current_entries(include_ignore=True):
            if entry.unique_id == unique_id:
                return entry
        return None

    def _async_current_entries(self, include_ignore: bool | None = False) -> list[Any]:
        entries = self.hass.config_entries.async_entries(getattr(self, "_domain", None))
        if include_ignore:
            return list(entries)
        return [e for e in entries if getattr(e, "source", None) != SOURCE_IGNORE]

    def _abort_if_unique_id_configured(
        self,
        updates: dict[str, Any] | None = None,
        reload_on_update: bool = True,
        *,
        error: str = "already_configured",
    ) -> None:
        """Как в HA: найти entry с тем же unique_id; при наличии — применить
        updates к data, при изменении и reload_on_update запланировать reload,
        поднять AbortFlow(error)."""
        uid = self.unique_id
        if uid is None:
            return
        for entry in self._async_current_entries(include_ignore=True):
            if entry.unique_id != uid:
                continue
            if updates:
                changed = any(entry.data.get(k) != v for k, v in updates.items())
                if changed:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, **updates}
                    )
                    if reload_on_update:
                        self.hass.config_entries.async_schedule_reload(entry.entry_id)
            raise AbortFlow(error)

    def _async_abort_entries_match(self, match_dict: dict[str, Any] | None = None) -> None:
        """AbortFlow('already_configured'), если у какого-то entry data
        содержит все пары match_dict."""
        if not match_dict:
            return
        for entry in self._async_current_entries(include_ignore=False):
            if all(entry.data.get(k) == v for k, v in match_dict.items()):
                raise AbortFlow("already_configured")

    def _get_entry_from_context(self):
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            raise RuntimeError(f"config entry {self.context['entry_id']!r} not found")
        return entry

    def _get_reconfigure_entry(self):
        return self._get_entry_from_context()

    def _get_reauth_entry(self):
        return self._get_entry_from_context()

    def async_update_reload_and_abort(
        self,
        entry,
        *,
        unique_id: Any = _UNDEFINED,
        title: Any = _UNDEFINED,
        data: Any = _UNDEFINED,
        data_updates: Any = _UNDEFINED,
        options: Any = _UNDEFINED,
        reason: str | None = None,
        reload_even_if_entry_is_unchanged: bool = True,
    ) -> dict[str, Any]:
        if data_updates is not _UNDEFINED:
            data = {**entry.data, **data_updates}
        kwargs: dict[str, Any] = {}
        if data is not _UNDEFINED:
            kwargs["data"] = data
        if options is not _UNDEFINED:
            kwargs["options"] = options
        if unique_id is not _UNDEFINED:
            kwargs["unique_id"] = unique_id
        if title is not _UNDEFINED:
            kwargs["title"] = title
        if kwargs:
            self.hass.config_entries.async_update_entry(entry, **kwargs)
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        if reason is None:
            reason = (
                "reauth_successful"
                if self.context.get("source") == "reauth"
                else "reconfigure_successful"
            )
        return self.async_abort(reason=reason)


class OptionsFlow(_FlowHandlerBase):
    """Stub config_entries.OptionsFlow: config_entry присваивается тестом."""

    @property
    def config_entry(self):
        return self._config_entry

    @config_entry.setter
    def config_entry(self, value) -> None:
        self._config_entry = value


# ── helpers.selector ─────────────────────────────────────────────────────

class NumberSelectorMode(str, Enum):
    BOX = "box"
    SLIDER = "slider"


class NumberSelectorConfig(dict):
    """Как в HA — dict с известными ключами."""

    def __init__(
        self,
        *,
        min: float | None = None,
        max: float | None = None,
        step: float | None = None,
        mode: Any = None,
        unit_of_measurement: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            min=min, max=max, step=step, mode=mode,
            unit_of_measurement=unit_of_measurement, **kwargs,
        )


class NumberSelector:
    """Вызываемый валидатор: значение → float в границах, иначе ValueError.

    voluptuous оборачивает ValueError в vol.Invalid внутри vol.All —
    поведение совпадает с реальным NumberSelector."""

    def __init__(self, config: NumberSelectorConfig | dict | None = None) -> None:
        self.config = dict(config or {})

    def __call__(self, value: Any) -> float:
        val = float(value)
        min_v = self.config.get("min")
        max_v = self.config.get("max")
        if min_v is not None and val < min_v:
            raise ValueError(f"Value {val} is below minimum {min_v}")
        if max_v is not None and val > max_v:
            raise ValueError(f"Value {val} is above maximum {max_v}")
        return val


# ── helpers.device_registry ──────────────────────────────────────────────

@dataclass
class DeviceInfo:
    identifiers: set = field(default_factory=set)
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    sw_version: str | None = None
    hw_version: str | None = None
    serial_number: str | None = None
    configuration_url: str = ""


@dataclass
class DeviceEntry:
    """Stub для diagnostics device-level + device automation."""
    id: str = "test-device-entry-id"
    identifiers: set = field(default_factory=set)
    config_entries: set = field(default_factory=set)


# ── helpers.issue_registry ──────────────────────────────────────────────

class _IssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    CRITICAL = "critical"


# Контейнер для созданных issue (для тестов)
_ISSUE_REGISTRY: dict[tuple[str, str], dict[str, Any]] = {}


def async_create_issue(
    hass,
    domain: str,
    issue_id: str,
    *,
    is_fixable: bool = False,
    severity: Any = _IssueSeverity.WARNING,
    translation_key: str = "",
    translation_placeholders: dict[str, str] | None = None,
    **kwargs,
) -> None:
    _ISSUE_REGISTRY[(domain, issue_id)] = {
        "is_fixable": is_fixable,
        "severity": severity,
        "translation_key": translation_key,
        "translation_placeholders": dict(translation_placeholders or {}),
        "data": kwargs.get("data"),
    }


def async_delete_issue(hass, domain: str, issue_id: str) -> None:
    _ISSUE_REGISTRY.pop((domain, issue_id), None)


# ── components.repairs ──────────────────────────────────────────────────

class RepairsFlow:
    """Stub базового класса: минимальные async_show_form/async_create_entry."""

    def async_show_form(
        self,
        *,
        step_id: str,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
        description_placeholders: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": dict(errors or {}),
            "description_placeholders": dict(description_placeholders or {}),
        }

    def async_create_entry(self, *, title: str = "", data: dict | None = None) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": dict(data or {})}


class ConfirmRepairFlow(RepairsFlow):
    pass


# ── components.diagnostics ──────────────────────────────────────────────

def async_redact_data(data: Any, to_redact: set[str]) -> Any:
    """Рекурсивно заменяет значения по ключам в to_redact на '**REDACTED**'."""
    REDACTED = "**REDACTED**"
    if isinstance(data, dict):
        return {
            k: (REDACTED if k in to_redact else async_redact_data(v, to_redact))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [async_redact_data(v, to_redact) for v in data]
    return data


# ── helpers.update_coordinator ───────────────────────────────────────────

class _Generic:
    """Mixin: позволяет писать `Class[T]` без typing.Generic."""

    def __class_getitem__(cls, item):
        return cls


class DataUpdateCoordinator(_Generic):
    def __init__(
        self,
        hass: HomeAssistant,
        logger,
        *,
        config_entry=None,
        name: str = "",
        update_interval=None,
    ) -> None:
        self.hass = hass
        self.logger = logger
        self.config_entry = config_entry
        self.name = name
        self.update_interval = update_interval
        self.data: Any = None
        self.last_update_success = True

    def async_set_updated_data(self, data: Any) -> None:
        self.data = data
        # эмулируем уведомление подписчиков (async_update_listeners)
        self._notify_listeners()

    def async_update_listeners(self) -> None:
        self._notify_listeners()

    def _notify_listeners(self) -> None:
        # счётчик для тестов; реальный HA дёргает callbacks
        self._listener_calls = getattr(self, "_listener_calls", 0) + 1

    async def async_request_refresh(self) -> None:
        pass


class CoordinatorEntity(_Generic):
    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self.hass = getattr(coordinator, "hass", None)

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        pass

    def async_on_remove(self, fn) -> None:
        pass

    def async_write_ha_state(self) -> None:
        pass

    def _handle_coordinator_update(self) -> None:
        # Реальный CoordinatorEntity пишет state на каждый тик координатора.
        self.async_write_ha_state()


# ── helpers.aiohttp_client / entity_platform / event ─────────────────────

def async_get_clientsession(hass):
    return None


AddEntitiesCallback = Callable[..., None]


def async_track_time_interval(hass, fn, interval):
    return lambda: None


def async_call_later(hass, delay, fn):
    """Stub: не планирует — тесты зовут fn вручную. Возвращает cancel."""
    return lambda: None


class Store:
    """Минимальный стаб homeassistant.helpers.storage.Store (in-memory)."""

    def __init__(self, hass, version, key, **kwargs):
        self._data = None

    async def async_load(self):
        return self._data

    async def async_save(self, data):
        self._data = data

    def async_delay_save(self, data_func, delay=0):
        self._data = data_func()


# ── const / data_entry_flow ──────────────────────────────────────────────

class Platform(str, Enum):
    MEDIA_PLAYER = "media_player"
    BUTTON = "button"
    NUMBER = "number"
    SWITCH = "switch"
    SELECT = "select"
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    CAMERA = "camera"
    DEVICE_TRACKER = "device_tracker"
    CALENDAR = "calendar"


class EntityCategory(str, Enum):
    DIAGNOSTIC = "diagnostic"
    CONFIG = "config"


CONTENT_TYPE_MULTIPART = "multipart/x-mixed-replace; boundary={}"


class FlowResult(dict):
    pass


# ── components.media_player ──────────────────────────────────────────────

class MediaPlayerState(str, Enum):
    OFF = "off"
    ON = "on"
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    STANDBY = "standby"
    BUFFERING = "buffering"


class MediaType(str, Enum):
    MUSIC = "music"
    TVSHOW = "tvshow"
    MOVIE = "movie"


class RepeatMode(str, Enum):
    OFF = "off"
    ALL = "all"
    ONE = "one"


class MediaPlayerEntityFeature(IntFlag):
    PAUSE = 1
    SEEK = 2
    VOLUME_SET = 4
    VOLUME_MUTE = 8
    PREVIOUS_TRACK = 16
    NEXT_TRACK = 32
    VOLUME_STEP = 1024
    PLAY = 16384
    SHUFFLE_SET = 32768
    REPEAT_SET = 262144


class MediaPlayerEntity:
    """Пустой stub — атрибуты берутся через дескрипторы свойств в SboomMediaPlayer."""


# ── components.{camera,sensor,button,number,switch,select} ───────────────

class Camera:
    _attr_entity_registry_enabled_default = True

    def __init__(self) -> None:
        pass


class SensorEntity:
    pass


class CalendarEntity:
    pass


@dataclass
class CalendarEvent:
    start: Any = None
    end: Any = None
    summary: str = ""
    description: str | None = None
    uid: str | None = None


class TrackerEntity:
    pass


class _SourceType(str, Enum):
    GPS = "gps"
    ROUTER = "router"
    BLUETOOTH = "bluetooth"


class ButtonEntity:
    pass


class NumberEntity:
    pass


class SwitchEntity:
    pass


class SelectEntity:
    pass


# ── service_info.zeroconf ────────────────────────────────────────────────

@dataclass
class ZeroconfServiceInfo:
    host: str = ""
    port: int = 0
    properties: dict[str, Any] = field(default_factory=dict)


# ── регистрация в sys.modules ────────────────────────────────────────────

def _make_module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def install_stubs() -> None:
    """Зарегистрировать stub-модули.

    Идемпотентно: повторный вызов с уже установленными нашими stub'ами —
    no-op (распознаём по маркеру `_SBOOM_STUB`).

    Если в окружении установлен НАСТОЯЩИЙ пакет `homeassistant`, он
    перехватит импорты вместо тестовых stub'ов, и тесты начнут падать с
    невнятным `RuntimeError: Frame helper not set up`. Это бывает, когда
    тесты запускают в venv соседнего проекта, где HA установлен (плюс
    `pytest-homeassistant-custom-component` подтягивает HA ещё до conftest
    и его уже не выбить из sys.modules безопасно). Вместо тихой деградации
    падаем сразу с понятным сообщением.
    """
    existing = sys.modules.get("homeassistant")
    if existing is not None:
        if getattr(existing, "_SBOOM_STUB", False):
            return  # наши stub'ы уже стоят
        raise RuntimeError(
            "В окружении установлен настоящий пакет 'homeassistant' — он "
            "перехватывает импорты вместо тестовых stub'ов. Тесты sboom_ha "
            "рассчитаны на чистый venv. Запускайте их так:\n"
            "  python -m venv /tmp/sboom_venv\n"
            "  /tmp/sboom_venv/bin/pip install pytest pytest-asyncio "
            "aiohttp Pillow websockets\n"
            "  /tmp/sboom_venv/bin/python -m pytest tests/\n"
            "(см. CLAUDE.md → раздел «Тесты»)."
        )

    ha = _make_module("homeassistant")
    ha._SBOOM_STUB = True
    _make_module(
        "homeassistant.const",
        Platform=Platform,
        EntityCategory=EntityCategory,
        CONTENT_TYPE_MULTIPART=CONTENT_TYPE_MULTIPART,
        CONF_DEVICE_ID="device_id",
        CONF_DOMAIN="domain",
        CONF_PLATFORM="platform",
        CONF_TYPE="type",
        PERCENTAGE="%",
    )
    _make_module(
        "homeassistant.core",
        HomeAssistant=HomeAssistant,
        callback=callback,
        ServiceCall=ServiceCall,
        Context=object,
        CALLBACK_TYPE=object,
    )
    _make_module(
        "homeassistant.exceptions",
        HomeAssistantError=HomeAssistantError,
        ConfigEntryNotReady=ConfigEntryNotReady,
        ConfigEntryAuthFailed=ConfigEntryAuthFailed,
        ServiceValidationError=ServiceValidationError,
        InvalidDeviceAutomationConfig=type(
            "InvalidDeviceAutomationConfig", (HomeAssistantError,), {}
        ),
    )
    _make_module(
        "homeassistant.config_entries",
        ConfigEntry=ConfigEntry,
        ConfigFlow=ConfigFlow,
        ConfigFlowResult=FlowResult,
        OptionsFlow=OptionsFlow,
        SOURCE_IGNORE=SOURCE_IGNORE,
    )
    _make_module(
        "homeassistant.data_entry_flow",
        FlowResult=FlowResult,
        AbortFlow=AbortFlow,
    )

    _make_module("homeassistant.helpers")
    _make_module(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=DataUpdateCoordinator,
        CoordinatorEntity=CoordinatorEntity,
    )
    _make_module(
        "homeassistant.helpers.device_registry",
        DeviceInfo=DeviceInfo,
        DeviceEntry=DeviceEntry,
        async_get=_async_get_device_registry,
    )
    _make_module(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=async_get_clientsession,
    )
    _make_module(
        "homeassistant.helpers.entity_platform",
        AddEntitiesCallback=AddEntitiesCallback,
    )
    _make_module(
        "homeassistant.helpers.event",
        async_track_time_interval=async_track_time_interval,
        async_call_later=async_call_later,
    )
    _make_module("homeassistant.helpers.storage", Store=Store)
    _make_module(
        "homeassistant.helpers.selector",
        NumberSelector=NumberSelector,
        NumberSelectorConfig=NumberSelectorConfig,
        NumberSelectorMode=NumberSelectorMode,
    )
    _make_module("homeassistant.helpers.service_info")
    _make_module(
        "homeassistant.helpers.service_info.zeroconf",
        ZeroconfServiceInfo=ZeroconfServiceInfo,
    )

    _make_module("homeassistant.components")
    _make_module(
        "homeassistant.components.media_player",
        MediaPlayerEntity=MediaPlayerEntity,
        MediaPlayerEntityFeature=MediaPlayerEntityFeature,
        MediaPlayerState=MediaPlayerState,
        MediaType=MediaType,
        RepeatMode=RepeatMode,
    )
    _make_module("homeassistant.components.camera", Camera=Camera)
    _make_module(
        "homeassistant.components.device_tracker",
        TrackerEntity=TrackerEntity,
        SourceType=_SourceType,
    )
    _make_module(
        "homeassistant.components.calendar",
        CalendarEntity=CalendarEntity,
        CalendarEvent=CalendarEvent,
    )
    # ── device automation (trigger/action) ──
    import voluptuous as _vol

    _dev_autom_base = _vol.Schema(
        {
            _vol.Required("platform"): str,
            _vol.Required("domain"): str,
            _vol.Required("device_id"): str,
        },
        extra=_vol.ALLOW_EXTRA,
    )
    _make_module(
        "homeassistant.components.device_automation",
        DEVICE_TRIGGER_BASE_SCHEMA=_dev_autom_base,
        DEVICE_ACTION_BASE_SCHEMA=_dev_autom_base,
    )

    async def _fake_attach_trigger(hass, config, action, info, platform_type=None):
        return lambda: None

    _make_module("homeassistant.components.homeassistant")
    _make_module("homeassistant.components.homeassistant.triggers")
    _make_module(
        "homeassistant.components.homeassistant.triggers.event",
        TRIGGER_SCHEMA=lambda cfg: cfg,
        async_attach_trigger=_fake_attach_trigger,
        CONF_PLATFORM="platform",
        CONF_EVENT_TYPE="event_type",
        CONF_EVENT_DATA="event_data",
    )
    _make_module("homeassistant.helpers.typing", ConfigType=dict)
    _make_module(
        "homeassistant.helpers.trigger",
        TriggerActionType=object,
        TriggerInfo=object,
    )
    _make_module("homeassistant.components.sensor", SensorEntity=SensorEntity)
    _make_module("homeassistant.components.button", ButtonEntity=ButtonEntity)
    _make_module("homeassistant.components.number", NumberEntity=NumberEntity)
    _make_module("homeassistant.components.switch", SwitchEntity=SwitchEntity)
    _make_module("homeassistant.components.select", SelectEntity=SelectEntity)
    _make_module(
        "homeassistant.components.zeroconf",
        ZeroconfServiceInfo=ZeroconfServiceInfo,
    )
    _make_module(
        "homeassistant.components.diagnostics",
        async_redact_data=async_redact_data,
    )
    # Создаём подмодуль issue_registry с правильными типами
    _make_module(
        "homeassistant.helpers.issue_registry",
        async_create_issue=async_create_issue,
        async_delete_issue=async_delete_issue,
        IssueSeverity=_IssueSeverity,
    )
    _make_module(
        "homeassistant.components.repairs",
        RepairsFlow=RepairsFlow,
        ConfirmRepairFlow=ConfirmRepairFlow,
    )

    class _SystemHealthRegistration:
        def __init__(self) -> None:
            self.info_callback = None
            self.manage_url = None

        def async_register_info(self, callback_, manage_url=None):
            self.info_callback = callback_
            self.manage_url = manage_url

    def _async_check_can_reach_url(hass, url):
        return "ok"

    _make_module(
        "homeassistant.components.system_health",
        SystemHealthRegistration=_SystemHealthRegistration,
        async_check_can_reach_url=_async_check_can_reach_url,
    )

    # ── frontend / http / websocket_api / loader (встроенная панель) ─────────
    def _async_register_built_in_panel(*_a, **_k):
        return None

    def _async_remove_panel(*_a, **_k):
        return None

    _make_module(
        "homeassistant.components.frontend",
        async_register_built_in_panel=_async_register_built_in_panel,
        async_remove_panel=_async_remove_panel,
    )

    class _StaticPathConfig:
        def __init__(self, url_path, path, cache_headers=True):
            self.url_path = url_path
            self.path = path
            self.cache_headers = cache_headers

    _make_module(
        "homeassistant.components.http", StaticPathConfig=_StaticPathConfig
    )

    def _ws_command(_schema):
        def _deco(func):
            return func

        return _deco

    def _ws_async_response(func):
        return func

    def _ws_register_command(_hass, _command):
        return None

    class _ActiveConnection:  # только для аннотаций (runtime не используется)
        pass

    ws_mod = _make_module(
        "homeassistant.components.websocket_api",
        websocket_command=_ws_command,
        async_response=_ws_async_response,
        async_register_command=_ws_register_command,
        ActiveConnection=_ActiveConnection,
    )
    # `from homeassistant.components import websocket_api` требует атрибут пакета
    sys.modules["homeassistant.components"].websocket_api = ws_mod

    class _Integration:
        version = "0.0.0-test"

    async def _async_get_integration(_hass, _domain):
        return _Integration()

    _make_module(
        "homeassistant.loader", async_get_integration=_async_get_integration
    )

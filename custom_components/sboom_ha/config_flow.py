"""UI Config Flow: zeroconf discovery → IP подтверждение → pair с '+' на колонке."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant import config_entries

try:  # HA 2024.4+ — актуальный тип результата config flow
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # legacy
    from homeassistant.data_entry_flow import (
        FlowResult as ConfigFlowResult,  # type: ignore[assignment]
    )

try:  # HA 2023.12+
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # legacy
    from homeassistant.components.zeroconf import ZeroconfServiceInfo  # type: ignore[no-redef]

from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import PairTimeout, SberSpeakerClient
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_NAME,
    CONF_DEVICE_FIRMWARE,
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_HOST,
    CONF_PIN_ACCESS_TOKEN,
    CONF_PORT,
    DEFAULT_AVAILABILITY_THRESHOLD,
    DEFAULT_KARAOKE_FILL,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_LYRICS_ENABLED,
    DEFAULT_LYRICS_NETEASE,
    DEFAULT_LYRICS_OFFSET,
    DEFAULT_NAME,
    DEFAULT_PANEL_ENABLED,
    DEFAULT_PORT,
    DEFAULT_VOLUME_POLL_INTERVAL,
    DOMAIN,
    MDNS_PROP_FIRMWARE,
    MDNS_PROP_ID,
    MDNS_PROP_NAME,
    MDNS_PROP_TYPE,
    OPT_AVAILABILITY_THRESHOLD,
    OPT_KARAOKE_FILL,
    OPT_KEEPALIVE_INTERVAL,
    OPT_LYRICS_ENABLED,
    OPT_LYRICS_NETEASE,
    OPT_LYRICS_OFFSET,
    OPT_PANEL_ENABLED,
    OPT_VOLUME_POLL_INTERVAL,
    PAIR_BUTTON_TIMEOUT_SEC,
)

_LOGGER = logging.getLogger(__name__)


def _decode_props(raw: dict) -> dict[str, str]:
    """zeroconf properties приходят как bytes — декодируем в str."""
    out: dict[str, str] = {}
    for k, v in (raw or {}).items():
        if isinstance(k, bytes):
            try:
                k = k.decode("utf-8")
            except UnicodeDecodeError:
                continue
        if isinstance(v, bytes):
            try:
                v = v.decode("utf-8")
            except UnicodeDecodeError:
                v = v.hex()
        if v is not None:
            out[str(k)] = str(v)
    return out


class SboomOptionsFlow(config_entries.OptionsFlow):
    """Опции, редактируемые после установки (Settings → Integrations → SBoom → Configure)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Текущие значения = из options, fallback на дефолты.
        # NumberSelector вместо голых int-полей: UI показывает единицы
        # измерения и границы, а не безымянное числовое поле.
        def _seconds(min_v: float, max_v: float, step: float = 1) -> NumberSelector:
            return NumberSelector(
                NumberSelectorConfig(
                    min=min_v, max=max_v, step=step,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            )

        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        OPT_VOLUME_POLL_INTERVAL,
                        default=opts.get(OPT_VOLUME_POLL_INTERVAL, DEFAULT_VOLUME_POLL_INTERVAL),
                    ): vol.All(_seconds(1, 60), vol.Coerce(int)),
                    vol.Optional(
                        OPT_KEEPALIVE_INTERVAL,
                        default=opts.get(OPT_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL),
                    ): vol.All(_seconds(5, 120), vol.Coerce(int)),
                    vol.Optional(
                        OPT_AVAILABILITY_THRESHOLD,
                        default=opts.get(OPT_AVAILABILITY_THRESHOLD, DEFAULT_AVAILABILITY_THRESHOLD),
                    ): vol.All(
                        NumberSelector(
                            NumberSelectorConfig(
                                min=1, max=20, step=1, mode=NumberSelectorMode.BOX,
                            )
                        ),
                        vol.Coerce(int),
                    ),
                    vol.Optional(
                        OPT_LYRICS_ENABLED,
                        default=opts.get(OPT_LYRICS_ENABLED, DEFAULT_LYRICS_ENABLED),
                    ): bool,
                    vol.Optional(
                        OPT_LYRICS_NETEASE,
                        default=opts.get(OPT_LYRICS_NETEASE, DEFAULT_LYRICS_NETEASE),
                    ): bool,
                    vol.Optional(
                        OPT_LYRICS_OFFSET,
                        default=opts.get(OPT_LYRICS_OFFSET, DEFAULT_LYRICS_OFFSET),
                    ): vol.All(_seconds(-10, 10, 0.1), vol.Coerce(float)),
                    vol.Optional(
                        OPT_KARAOKE_FILL,
                        default=opts.get(OPT_KARAOKE_FILL, DEFAULT_KARAOKE_FILL),
                    ): bool,
                    vol.Optional(
                        OPT_PANEL_ENABLED,
                        default=opts.get(OPT_PANEL_ENABLED, DEFAULT_PANEL_ENABLED),
                    ): bool,
                }
            ),
        )


class SboomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SboomOptionsFlow:
        return SboomOptionsFlow()

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._client_id: str = str(uuid.uuid4())
        self._client_name: str = "Home Assistant"
        self._device_id: str | None = None
        self._device_model: str | None = None
        self._device_name: str | None = None
        self._device_firmware: str | None = None

    # ───────────────────────── Manual step ─────────────────────────

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input.get(CONF_PORT, DEFAULT_PORT)
            self._client_name = user_input.get(CONF_CLIENT_NAME, self._client_name)
            # Entry, добавленный через discovery, имеет unique_id по device_id —
            # host-based unique_id ниже его не поймает. Сверяем по host, иначе
            # ручной ввод IP уже настроенной колонки создаст дубликат.
            self._async_abort_entries_match({CONF_HOST: self._host})
            await self.async_set_unique_id(f"{DOMAIN}_{self._host}")
            self._abort_if_unique_id_configured()
            return await self.async_step_pair()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Optional(CONF_CLIENT_NAME, default="Home Assistant"): str,
                }
            ),
            errors=errors,
        )

    # ───────────────────────── Reauth flow ─────────────────────────

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Повторная авторизация (новый pair-handshake).

        Автоматического триггера НЕТ: локальный протокол колонки не отдаёт
        различимого признака «токен отвергнут» (недоступность и отзыв токена
        выглядят одинаково), поэтому ложные reauth-баннеры при сетевых сбоях
        были бы хуже. Запускается вручную: сервис `sboom_ha.reauth`.

        Восстанавливаем контекст из существующего entry — host, port, ids,
        и переходим к подтверждению (тот же pair-handshake).
        """
        self._host = entry_data.get(CONF_HOST)
        self._port = entry_data.get(CONF_PORT, DEFAULT_PORT)
        self._client_id = entry_data.get(CONF_CLIENT_ID, str(uuid.uuid4()))
        self._client_name = entry_data.get(CONF_CLIENT_NAME, "Home Assistant")
        self._device_id = entry_data.get(CONF_DEVICE_ID)
        self._device_model = entry_data.get(CONF_DEVICE_MODEL)
        self._device_name = entry_data.get(CONF_DEVICE_NAME)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Повторный pair-handshake: пользователь снова жмёт `+` на колонке."""
        errors: dict[str, str] = {}

        if user_input is not None:
            token, errors = await self._async_do_pair()
            if not errors:
                # Обновляем entry новым токеном — остальные данные не трогаем
                entry = self._get_reauth_entry()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PIN_ACCESS_TOKEN: token},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "host": str(self._host),
                "name": self._device_name or "SberBoom",
                "timeout": str(PAIR_BUTTON_TIMEOUT_SEC),
            },
            errors=errors,
        )

    # ───────────────────────── Reconfigure flow ─────────────────────────

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Изменить host/port для существующего entry без удаления.

        Полезно когда колонка получила новый статический IP, или при переезде
        в другую подсеть. Pair-токен и device_id сохраняются.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            new_host = user_input[CONF_HOST]
            new_port = user_input.get(CONF_PORT, entry.data.get(CONF_PORT, DEFAULT_PORT))
            # Проверяем, что по новому адресу действительно отвечает колонка —
            # иначе entry перезагрузится в заведомо мёртвое состояние, а юзер
            # не увидит почему (опечатка в IP — частый случай).
            client = SberSpeakerClient(
                host=new_host,
                port=new_port,
                client_id=entry.data.get(CONF_CLIENT_ID, str(uuid.uuid4())),
                client_name=entry.data.get(CONF_CLIENT_NAME, "Home Assistant"),
            )
            try:
                await client.connect()
            except Exception:
                _LOGGER.debug(
                    "reconfigure: connect to %s:%s failed", new_host, new_port,
                    exc_info=True,
                )
                errors["base"] = "cannot_connect"
            else:
                # Legacy unique_id привязан к старому IP: без обновления он
                # навсегда «занимает» старый адрес — добавление другой колонки
                # на нём упрётся в ложный already_configured.
                old_host = entry.data.get(CONF_HOST)
                if entry.unique_id == f"{DOMAIN}_{old_host}" and new_host != old_host:
                    self.hass.config_entries.async_update_entry(
                        entry, unique_id=f"{DOMAIN}_{new_host}"
                    )
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: new_host, CONF_PORT: new_port},
                )
            finally:
                try:
                    await client.close()
                except Exception:
                    _LOGGER.debug("client.close in reconfigure finally failed", exc_info=True)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST)): str,
                    vol.Optional(
                        CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)
                    ): int,
                }
            ),
            errors=errors,
            description_placeholders={
                "name": entry.data.get(CONF_DEVICE_NAME) or "SberBoom",
            },
        )

    # ───────────────────────── Zeroconf discovery ─────────────────────────

    def _async_find_legacy_entry(
        self, host: str, device_id: str
    ) -> config_entries.ConfigEntry | None:
        """Существующий entry этой же колонки с host-based unique_id.

        Сюда попадаем только когда unique_id по device_id НЕ совпал ни с одним
        entry (иначе `_abort_if_unique_id_configured` уже прервал flow).

        ВАЖНО: entry с CONF_DEVICE_ID другой колонки НЕ считается legacy, даже
        при совпадении host. Иначе сценарий «колонка A переехала (DHCP), её
        старый IP получила колонка B» перезаписал бы identity entry A данными
        колонки B. Устаревший host entry A обновится при её собственном
        discovery через штатную soft-migration.
        """
        legacy_uid = f"{DOMAIN}_{host}"
        for entry in self._async_current_entries(include_ignore=False):
            known_id = entry.data.get(CONF_DEVICE_ID)
            if known_id and known_id != device_id:
                continue  # другая колонка — не трогаем
            if entry.unique_id == legacy_uid or entry.data.get(CONF_HOST) == host:
                return entry
        return None

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Колонка обнаружена через mDNS (_staros._tcp.local.)."""
        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT
        props = _decode_props(discovery_info.properties)

        device_id = props.get(MDNS_PROP_ID)
        device_model = props.get(MDNS_PROP_TYPE)
        device_name = props.get(MDNS_PROP_NAME) or "SberBoom"
        firmware = props.get(MDNS_PROP_FIRMWARE)

        _LOGGER.debug(
            "discovery: host=%s port=%s id=%s model=%s name=%s fw=%s",
            host, port, device_id, device_model, device_name, firmware,
        )

        if not device_id:
            return self.async_abort(reason="incomplete_discovery")

        await self.async_set_unique_id(f"{DOMAIN}_{device_id}")
        # Soft-migration: при повторном discovery обновляем host/port и firmware
        # для уже сконфигурированных entries (ранние версии не сохраняли firmware).
        # reload_on_update=True — если IP сменился, перезагружаем интеграцию
        # автоматически; иначе client продолжит ходить на старый адрес.
        updates: dict[str, Any] = {CONF_HOST: host, CONF_PORT: port}
        if firmware:
            updates[CONF_DEVICE_FIRMWARE] = firmware
        if device_model:
            updates[CONF_DEVICE_MODEL] = device_model
        self._abort_if_unique_id_configured(updates=updates, reload_on_update=True)

        # Healing: entry, добавленный вручную, имеет unique_id по IP и не
        # матчится по device_id выше. Без миграции HA предложит «новое
        # устройство», подтверждение создаст дубликат с повторным pairing,
        # а при смене IP старый entry навсегда останется на мёртвом адресе.
        # Находим такой entry по host и переводим на device_id-unique_id.
        legacy = self._async_find_legacy_entry(host, device_id)
        if legacy is not None:
            _LOGGER.info(
                "migrating legacy entry %s (unique_id=%s) to unique_id=%s",
                legacy.entry_id, legacy.unique_id, self.unique_id,
            )
            new_data = {**legacy.data, **updates, CONF_DEVICE_ID: device_id}
            if device_name and not legacy.data.get(CONF_DEVICE_NAME):
                new_data[CONF_DEVICE_NAME] = device_name
            self.hass.config_entries.async_update_entry(
                legacy, unique_id=self.unique_id, data=new_data
            )
            self.hass.config_entries.async_schedule_reload(legacy.entry_id)
            return self.async_abort(reason="already_configured")

        self._host = host
        self._port = port
        self._device_id = device_id
        self._device_model = device_model
        self._device_name = device_name
        self._device_firmware = firmware
        self.context["title_placeholders"] = {
            "name": device_name,
            "host": host,
        }
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Подтверждаем обнаружение и переходим к pair."""
        if user_input is not None:
            self._client_name = user_input.get(CONF_CLIENT_NAME, self._client_name)
            return await self.async_step_pair()

        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema(
                {vol.Optional(CONF_CLIENT_NAME, default="Home Assistant"): str}
            ),
            description_placeholders={
                "name": self._device_name or "SberBoom",
                "host": str(self._host),
                "model": self._device_model or "—",
            },
        )

    # ───────────────────────── Pair handshake ─────────────────────────

    async def _async_do_pair(self) -> tuple[str | None, dict[str, str]]:
        """Общий pair-handshake для pair- и reauth-шагов.

        connect → pair_with_button (с таймаутом) → close. Возвращает
        (token, errors): при успехе errors пуст, при ошибке token=None
        и errors["base"] содержит "pair_timeout" либо "cannot_connect".
        Что делать с полученным токеном — решает вызывающий step.
        """
        errors: dict[str, str] = {}
        token: str | None = None
        client: SberSpeakerClient | None = None
        try:
            client = SberSpeakerClient(
                host=self._host,  # type: ignore[arg-type]
                port=self._port,
                client_id=self._client_id,
                client_name=self._client_name,
            )
            await client.connect()
            token = await asyncio.wait_for(
                client.pair_with_button(),
                timeout=PAIR_BUTTON_TIMEOUT_SEC + 5,
            )
        except PairTimeout:
            errors["base"] = "pair_timeout"
        except Exception:
            _LOGGER.exception("pair failed")
            errors["base"] = "cannot_connect"
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    _LOGGER.debug("client.close in pair finally failed", exc_info=True)
        return token, errors

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            token, errors = await self._async_do_pair()
            if not errors:
                title = self._device_name or f"{DEFAULT_NAME} {self._host}"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_CLIENT_ID: self._client_id,
                        CONF_CLIENT_NAME: self._client_name,
                        CONF_PIN_ACCESS_TOKEN: token,
                        CONF_DEVICE_ID: self._device_id,
                        CONF_DEVICE_MODEL: self._device_model,
                        CONF_DEVICE_NAME: self._device_name,
                        CONF_DEVICE_FIRMWARE: self._device_firmware,
                    },
                )

        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({}),
            description_placeholders={
                "host": str(self._host),
                "name": self._device_name or "SberBoom",
                "timeout": str(PAIR_BUTTON_TIMEOUT_SEC),
            },
            errors=errors,
        )

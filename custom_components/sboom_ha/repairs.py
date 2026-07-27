"""Repairs platform — создаёт fixable/info issues для пользователя.

Активные issue видно в Settings → System → Repairs. Сейчас регистрируем
один тип: `unreachable_<entry_id>` — колонка недоступна > 5 минут. Fix-flow
позволяет ввести новый IP прямо из issue (частая причина недоступности —
DHCP выдал колонке другой адрес): проверяем соединение, обновляем entry и
перезагружаем интеграцию. Pair-токен и device_id сохраняются. Issue
удаляется автоматически когда coordinator восстанавливает связь.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .api import SberSpeakerClient
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_NAME,
    CONF_DEVICE_NAME,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SpeakerUnreachableRepairFlow(RepairsFlow):
    """Fix-flow для `unreachable_*`: смена host/port без удаления entry."""

    def __init__(self, hass: HomeAssistant, data: dict[str, Any] | None) -> None:
        self.hass = hass
        self._entry_id: str | None = (data or {}).get("entry_id")

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        entry = (
            self.hass.config_entries.async_get_entry(self._entry_id)
            if self._entry_id
            else None
        )
        if entry is None:
            # Issue от старой версии (без entry_id) или entry уже удалён —
            # чинить нечего, закрываем issue подтверждением.
            return self.async_create_entry(title="", data={})

        errors: dict[str, str] = {}
        if user_input is not None:
            new_host = user_input[CONF_HOST]
            new_port = user_input.get(CONF_PORT, entry.data.get(CONF_PORT, DEFAULT_PORT))
            # Как и в reconfigure-flow: сперва проверяем что по адресу отвечает
            # колонка, иначе entry перезагрузится в заведомо мёртвое состояние.
            client = SberSpeakerClient(
                host=new_host,
                port=new_port,
                client_id=entry.data.get(CONF_CLIENT_ID, ""),
                client_name=entry.data.get(CONF_CLIENT_NAME, "Home Assistant"),
            )
            try:
                await client.connect()
            except Exception:
                _LOGGER.debug(
                    "repair: connect to %s:%s failed", new_host, new_port,
                    exc_info=True,
                )
                errors["base"] = "cannot_connect"
            else:
                # Как и в reconfigure-flow: legacy unique_id привязан к старому
                # IP — без обновления он навсегда «занимает» старый адрес, и
                # добавление другой колонки на нём упрётся в ложный
                # already_configured.
                old_host = entry.data.get(CONF_HOST)
                if entry.unique_id == f"{DOMAIN}_{old_host}" and new_host != old_host:
                    self.hass.config_entries.async_update_entry(
                        entry, unique_id=f"{DOMAIN}_{new_host}"
                    )
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_HOST: new_host, CONF_PORT: new_port},
                )
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(title="", data={})
            finally:
                try:
                    await client.close()
                except Exception:
                    _LOGGER.debug("client.close in repair-flow finally failed", exc_info=True)

        return self.async_show_form(
            step_id="init",
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


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Фабрика fix-flow для issue."""
    if issue_id.startswith("unreachable_"):
        return SpeakerUnreachableRepairFlow(hass, data)
    return ConfirmRepairFlow()

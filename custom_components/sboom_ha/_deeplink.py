"""ServerAction / DEEPLINK — отправка команд-действий на колонку SberBoom.

Переиспользует существующий :class:`~custom_components.sboom_ha.api.SberSpeakerClient`
(его WS-сокет, корреляцию request/response и auth-атрибуты) БЕЗ модификации
``api.py``. Envelope собирается вручную из :func:`custom_components.sboom_ha._tlv.field`,
т.к. формат ServerAction отличается от обычного op-конверта клиента:

* тип сообщения ``field(1,0,1)`` (у обычных запросов клиента — ``2``);
* полезная нагрузка живёт в ``field(4)`` (``ServerAction``), а не в ``field(5)``
  (``request_data``);
* внутри ``field(4)`` — вложенное ``field(1,2, <json_bytes>)`` с JSON-командой.

Ответ колонки — такой же envelope; ``field(4)`` при успехе декодится в
``{1: 'OK'}`` (или строку с ``ParseError`` при ошибке разбора).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any

from ._tlv import decode as _decode_tlv
from ._tlv import field as _field

if TYPE_CHECKING:  # избегаем циклического импорта и HA-тяжёлого api в рантайме
    from .api import SberSpeakerClient

__all__ = ["build_server_action", "play_deeplink", "send_server_action"]

# Пустой smartAppInfo — 6 обязательных строковых полей (иначе колонка отвечает
# ParseError). Значения не важны для DEEPLINK, но структура должна присутствовать.
_EMPTY_SMART_APP_INFO: dict[str, str] = {
    "appVersionId": "",
    "applicationId": "",
    "frontendEndpoint": "",
    "frontendType": "",
    "projectId": "",
    "systemName": "",
}


def build_server_action(
    client: SberSpeakerClient,
    name: str,
    payload: dict[str, Any],
    msg_id: str,
) -> bytes:
    """Собрать бинарный envelope ServerAction для колонки.

    Auth-атрибуты (``client_id``/``pin_access_token``/``client_name``) берутся
    из переданного клиента — те же, что использует ``SberSpeakerClient._envelope``.

    Структура (теги TLV):
      field(1,0,1)                          — type
      field(2,2,<msg_id>)                   — id для корреляции ответа
      field(3,2,<pin_access_token>)         — только если токен задан
      field(4,2, field(1,2,<json_bytes>))   — ServerAction (вложенный JSON)
      field(6,0,1)                          — token_type = PIN_AUTH
      field(7,2,<client_name>)
      field(10,0,1)                         — is_request
      field(11,2,<client_id>)
    """
    body = {
        "name": name,
        "payload": payload,
        "smartAppInfo": dict(_EMPTY_SMART_APP_INFO),
        "type": 1,
    }
    json_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    server_action = _field(1, 2, json_bytes)

    parts = [
        _field(1, 0, 1),                    # type
        _field(2, 2, msg_id.encode()),      # id
    ]
    if client.pin_access_token:
        parts.append(_field(3, 2, client.pin_access_token.encode()))
    parts += [
        _field(4, 2, server_action),        # ServerAction { field(1)=json }
        _field(6, 0, 1),                     # token_type = PIN_AUTH
        _field(7, 2, client.client_name.encode()),
        _field(10, 0, 1),                    # is_request
        _field(11, 2, client.client_id.encode()),
    ]
    return b"".join(parts)


async def send_server_action(
    client: SberSpeakerClient,
    name: str,
    payload: dict[str, Any],
    timeout: float = 6.0,
) -> dict[int, Any] | str:
    """Отправить ServerAction и дождаться ответа колонки.

    Корреляция — через ``client._pending``: регистрируем future по ``msg_id``,
    listener (``SberSpeakerClient._listen_loop``) матчит входящий envelope по
    ``field(2)`` и делает ``fut.set_result(raw_bytes)``.

    Возвращает декодированное ``field(4)`` ответа — обычно ``{1: 'OK'}`` при
    успехе, либо строку (например, с ``ParseError``) при ошибке разбора.
    """
    ws = client._ws
    if ws is None:
        raise RuntimeError("not connected")

    msg_id = str(uuid.uuid4())
    envelope = build_server_action(client, name, payload, msg_id)

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    client._pending[msg_id] = fut
    try:
        # Тот же lock, что сериализует все send() клиента.
        async with client._lock:
            await ws.send(envelope)
        raw: bytes = await asyncio.wait_for(fut, timeout=timeout)
    finally:
        client._pending.pop(msg_id, None)

    parsed = _decode_tlv(raw)
    # field(4) — ServerAction-ответ: {1: 'OK'} при успехе или строка при ошибке.
    return parsed.get(4, {})


async def play_deeplink(client: SberSpeakerClient, deeplink_url: str) -> bool:
    """Проиграть deeplink (``staros://music?...``) на колонке.

    Возвращает ``True``, если ответ колонки содержит ``'OK'``.
    """
    result = await send_server_action(
        client, "DEEPLINK", {"deeplink": deeplink_url}
    )
    return _is_ok(result)


def _is_ok(result: dict[int, Any] | str) -> bool:
    """Признать ответ успешным, если где-то в нём есть 'OK'."""
    if isinstance(result, str):
        return "OK" in result
    if isinstance(result, dict):
        return any(
            isinstance(v, str) and "OK" in v for v in result.values()
        )
    return False

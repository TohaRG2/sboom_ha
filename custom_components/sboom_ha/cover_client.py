"""Поиск обложки по исполнителю+треку (для BT/радио — нет каталожного id).

Каталожный контент (Zvuk) получает обложку по release_id через CDN (см.
helpers.cover_url). У Bluetooth/радио каталожного id нет — ищем обложку по
названию и исполнителю в публичных Search API (без ключей):
  1) iTunes Search  — широкое покрытие, high-res (апскейл 100→600);
  2) Deezer         — резерв.
Функции чистые (session передаётся параметром) — тестируются через respx.
"""
from __future__ import annotations

import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
DEEZER_SEARCH_URL = "https://api.deezer.com/search"
_TIMEOUT = 8.0


async def fetch_cover(
    session: aiohttp.ClientSession, title: str, artist: str
) -> str | None:
    """URL обложки по title+artist. iTunes → Deezer. None если не нашлось."""
    if not title or not artist:
        return None
    for provider in (_itunes_cover, _deezer_cover):
        try:
            url = await provider(session, title, artist)
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            _LOGGER.debug("cover %s failed: %s", provider.__name__, exc)
            continue
        if url:
            return url
    return None


async def _itunes_cover(
    session: aiohttp.ClientSession, title: str, artist: str
) -> str | None:
    """iTunes Search: artworkUrl100 → апскейл до 600×600."""
    params = {"term": f"{artist} {title}", "entity": "song", "limit": "1"}
    async with session.get(
        ITUNES_SEARCH_URL, params=params,
        timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
    ) as r:
        if r.status != 200:
            return None
        data = await r.json(content_type=None)  # iTunes отдаёт text/javascript
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        return None
    art = results[0].get("artworkUrl100")
    if not isinstance(art, str) or not art:
        return None
    # "…/100x100bb.jpg" → "…/600x600bb.jpg" (iTunes отдаёт любой размер по URL).
    return art.replace("100x100bb", "600x600bb")


async def _deezer_cover(
    session: aiohttp.ClientSession, title: str, artist: str
) -> str | None:
    """Deezer Search: album.cover_xl (1000×1000) / cover_big."""
    params = {"q": f'artist:"{artist}" track:"{title}"', "limit": "1"}
    async with session.get(
        DEEZER_SEARCH_URL, params=params,
        timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
    ) as r:
        if r.status != 200:
            return None
        data = await r.json(content_type=None)
    items = data.get("data") if isinstance(data, dict) else None
    if not items:
        return None
    album = items[0].get("album") or {}
    cover = album.get("cover_xl") or album.get("cover_big")
    return cover if isinstance(cover, str) and cover else None

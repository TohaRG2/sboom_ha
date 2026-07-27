"""Side-effect анализ «немых» op'ов (которые не отвечают, напр. op 18).

Fuzzer'ы бесполезны для op без ответа — нет feedback. Здесь feedback —
ИЗМЕНЕНИЕ СОСТОЯНИЯ: шлём op с payload и сравниваем GET_STATE до/после.
Волатильные подсистемы (время, позиция, z-order приложений) исключаем,
иначе шум. Если op что-то меняет — увидим по diff'у стабильных полей.

Запуск (свой pairing recon'а — параллельно с HA):
    SBOOM_HOST=192.168.1.61 python3 research/op_side_effects.py 18
    (число — op для анализа; по умолчанию 18)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from tests._ha_stubs import install_stubs  # noqa: E402

install_stubs()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components"))
from sboom_ha._parsers import _extract_json_object  # noqa: E402
from sboom_ha._tlv import field as _field  # noqa: E402
from sboom_ha.api import SberSpeakerClient  # noqa: E402

HOST = os.environ.get("SBOOM_HOST", "192.168.1.61")
OP = int(sys.argv[1]) if len(sys.argv) > 1 else 18
CREDS = Path(__file__).resolve().parent / "fw_snapshots" / ".recon_creds.json"

# Волатильные top-level ключи GET_STATE — меняются сами, исключаем из diff.
VOLATILE = {
    "time", "timesync", "background_apps", "current_app", "volume",
    "location", "proactivityNotification", "deviceSleep",
}

PAYLOADS = [
    ("empty", b""),
    ("1:v=1", _field(1, 0, 1)),
    ("1:v=0", _field(1, 0, 0)),
    ("1:str", _field(1, 2, b"on")),
    ("1:float", _field(1, 5, 1.0)),
    ("nested", _field(1, 2, _field(1, 0, 1))),
    ("multi", _field(1, 0, 1) + _field(2, 0, 1)),
    ("2:v=1", _field(2, 0, 1)),
    ("bigv", _field(1, 0, 2**16)),
]


def _flatten(d, prefix=""):
    """GET_STATE → плоский dict path→value, без волатильных верхних веток."""
    out = {}
    for k, v in d.items():
        if prefix == "" and k in VOLATILE:
            continue
        p = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, p + "."))
        elif isinstance(v, list):
            out[p] = f"list[{len(v)}]"
        else:
            out[p] = v
    return out


def _diff(a: dict, b: dict) -> list[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            out.append(f"    {k}: {a.get(k)!r} → {b.get(k)!r}")
    return out


async def _state(client) -> dict:
    st = await client.get_state()
    if not st or not st.raw_state_json:
        return {}
    return _flatten(json.loads(st.raw_state_json))


async def main():
    creds = json.loads(CREDS.read_text())
    c = SberSpeakerClient(host=HOST, port=20000, client_id=creds["client_id"],
                          client_name="fw-recon", pin_access_token=creds["token"])
    await c.connect()
    c.start_listening()
    print(f"=== side-effect анализ op={OP} (стабильные поля GET_STATE) ===\n")
    any_change = False
    try:
        for label, inner in PAYLOADS:
            before = await _state(c)
            # шлём op fire-and-forget (не ждём ответа — op немой)
            body = _field(OP, 2, inner) if inner else _field(OP, 2, b"")
            await c._fire_and_forget(body)
            await asyncio.sleep(1.5)  # дать колонке применить эффект
            after = await _state(c)
            d = _diff(before, after)
            if d:
                any_change = True
                print(f"  ⚡ payload={label}: ИЗМЕНЕНИЕ состояния:")
                print("\n".join(d))
            else:
                print(f"  · payload={label}: без изменений")
    finally:
        await c.close()
    print()
    if not any_change:
        print(f"op {OP} НЕ меняет стабильное состояние ни на одном payload — "
              "либо чистый keepalive/no-op, либо эффект вне GET_STATE.")


asyncio.run(main())

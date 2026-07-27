"""Снятие живого JSON-состояния колонки → реальные имена proto-полей.

GET_STATE (op12) / GET_META_DATA (op10) / QUEUE (op17) возвращают JSON,
ключи которого = имена proto-полей. Заполняет пробел «имена не-строковых
полей» в реконструкции .proto.

Креды берём из окружения (в git НЕ хранить). Взять можно из конфига HA:
    core.config_entries → entry domain=sboom_ha → data.{client_id,pin_access_token}

Запуск:
    SBOOM_HOST=192.168.1.61 SBOOM_CID=<client_id> SBOOM_TOKEN=<token> \
        python3 research/capture_state.py
"""
from __future__ import annotations
import asyncio, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from tests._ha_stubs import install_stubs  # noqa: E402
install_stubs()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components"))
from sboom_ha.api import SberSpeakerClient  # noqa: E402
from sboom_ha._tlv import field as _field  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import find_jsons, decode, pretty  # noqa: E402

HOST = os.environ.get("SBOOM_HOST", "192.168.1.61")
OPS = {"state": 12, "metadata": 10, "queue": 17, "paired_bt": 19}


def keys_of(obj, prefix=""):
    """Рекурсивно: path → имя-типа (без значений — безопасно для коммита)."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}{k}"
            out[p] = type(v).__name__
            out.update(keys_of(v, p + "."))
    elif isinstance(obj, list) and obj:
        out.update(keys_of(obj[0], prefix + "[]."))
    return out


async def main():
    cid, tok = os.environ.get("SBOOM_CID"), os.environ.get("SBOOM_TOKEN")
    if not (cid and tok):
        sys.exit("нужны SBOOM_CID и SBOOM_TOKEN (креды из конфига HA)")
    c = SberSpeakerClient(host=HOST, port=20000, client_id=cid, pin_access_token=tok)
    await c.connect()
    c.start_listening()
    print(f"=== подключено к {HOST}:20000 ===\n")
    allkeys = {}
    dumpdir = Path(__file__).resolve().parent / "state_dump"
    dumpdir.mkdir(exist_ok=True)
    try:
        for name, op in OPS.items():
            try:
                raw = await c._request_response(_field(op, 2, _field(1, 2, b"")), timeout=6.0)
            except Exception as e:
                print(f"[{name} op{op}] ошибка: {type(e).__name__}: {e!r}")
                continue
            jsons = find_jsons(raw)
            if not jsons:
                print(f"[{name} op{op}] не-JSON {len(raw)}B (protobuf):\n{pretty(decode(raw))}")
                continue
            obj = json.loads(max(jsons, key=len))
            (dumpdir / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1))
            allkeys[name] = keys_of(obj)
            print(f"[{name} op{op}] ключей: {len(allkeys[name])}  → state_dump/{name}.json")
    finally:
        await c.close()
    (dumpdir / "all_keys.json").write_text(json.dumps(allkeys, ensure_ascii=False, indent=1))
    print("\n=== схема (имена+типы) → state_dump/all_keys.json ===")


if __name__ == "__main__":
    asyncio.run(main())

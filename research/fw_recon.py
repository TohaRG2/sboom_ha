"""Разведка прошивки колонки: полный структурный отпечаток + diff между запусками.

Снимает «схему» (НЕ значения — чтобы diff не шумел): версия прошивки,
структура GET_STATE (ключи→типы), карта op-ответов (какие op отвечают
JSON / error-ack / timeout), команды debug-CLI :4242, устройства libiio,
открытые порты. Сохраняет снимок и печатает разницу с прошлым.

Свой pairing (не конфликтует с активной HA-сессией — останавливать
интеграцию НЕ нужно): при первом запуске нажать «+» на колонке, дальше
recon автономен.

Запуск:
    SBOOM_HOST=192.168.1.61 python3 research/fw_recon.py           # обычный прогон
    SBOOM_HOST=192.168.1.61 python3 research/fw_recon.py --pair    # пере-спарить

Снимки: research/fw_snapshots/<fw_version>__<n>.json (в .gitignore — приватные).
При обновлении прошивки запусти снова — увидишь, что появилось/исчезло.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from tests._ha_stubs import install_stubs  # noqa: E402

install_stubs()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components"))
from sboom_ha._parsers import _extract_json_object  # noqa: E402
from sboom_ha._tlv import decode as _decode  # noqa: E402
from sboom_ha._tlv import field as _field  # noqa: E402
from sboom_ha.api import SberSpeakerClient  # noqa: E402
from sboom_ha.iio_client import parse_context  # noqa: E402

HOST = os.environ.get("SBOOM_HOST", "192.168.1.61")

SNAP_DIR = Path(__file__).resolve().parent / "fw_snapshots"
# Свой pairing recon'а — отдельный client_id+token, чтобы НЕ конфликтовать с
# активной HA-сессией (не надо останавливать интеграцию). Одноразовое
# нажатие «+» при первом запуске; дальше recon автономен.
CREDS = SNAP_DIR / ".recon_creds.json"
# ACTION-op'ы не трогаем (что-то делают на колонке).
SKIP_OPS = {4, 13, 14, 15, 16, 20, 22, 23}
PORTS = [20000, 4242, 30431, 33000, 4040, 1883, 5540, 8080]
CLI_SECTIONS = ["zigbee", "matter", "sys", "lc", "telzb", "test_flow"]


def schema_of(value):
    """Структурный тип значения (без самих значений).

    Для списков — ОБЪЕДИНЁННАЯ схема всех элементов (union ключей), а не
    первого: иначе волатильные списки (background_apps — z-order стек
    приложений) давали бы ложный diff в зависимости от того, что играет.
    """
    if isinstance(value, dict):
        return {k: schema_of(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        merged: dict = {}
        non_dict = None
        for item in value:
            sch = schema_of(item)
            if isinstance(sch, dict):
                _merge(merged, sch)
            else:
                non_dict = sch
        if merged:
            return [merged]
        return [non_dict] if non_dict is not None else []
    return type(value).__name__


def _merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            _merge(dst[k], v)
        else:
            dst.setdefault(k, v)


def scan_ports() -> list[int]:
    out = []
    for p in PORTS:
        try:
            s = socket.create_connection((HOST, p), timeout=1.5)
            s.close()
            out.append(p)
        except OSError:
            pass
    return out


def cli_commands() -> dict[str, list[str]]:
    """Список подкоманд каждого раздела debug-CLI :4242."""
    result: dict[str, list[str]] = {}
    try:
        s = socket.create_connection((HOST, 4242), timeout=4)
        s.settimeout(1.2)
        import time
        time.sleep(0.3)
        try:
            s.recv(4096)
        except OSError:
            pass

        def run(cmd: str) -> str:
            s.sendall((cmd + "\n").encode())
            time.sleep(0.4)
            buf = b""
            try:
                while True:
                    d = s.recv(4096)
                    if not d:
                        break
                    buf += d
            except OSError:
                pass
            return buf.decode("utf-8", "ignore")

        # верхнеуровневые команды
        top = run("help")
        result["<root>"] = _parse_cmd_names(top)
        for sec in CLI_SECTIONS:
            result[sec] = _parse_cmd_names(run(sec))
        s.close()
    except OSError:
        pass
    return result


def _parse_cmd_names(text: str) -> list[str]:
    """Извлечь имена команд из вывода 'Valid commands:'."""
    names = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        # имя команды — строка с отступом 4, следующая строка — описание с бОльшим
        if line.startswith("    ") and not line.startswith("        ") and stripped:
            names.append(stripped)
    return sorted(set(names))


def libiio_devices() -> dict:
    try:
        s = socket.create_connection((HOST, 30431), timeout=4)
        s.settimeout(2.5)
        s.sendall(b"PRINT\r\n")
        buf = b""
        try:
            while True:
                d = s.recv(8192)
                if not d:
                    break
                buf += d
                if b"</context>" in buf:
                    break
        except OSError:
            pass
        s.close()
        cap = parse_context(buf.decode("utf-8", "ignore"))
        return {"illuminance_device": cap.illuminance_device,
                "thermal_device": cap.thermal_device}
    except OSError:
        return {}


async def collect_ws(client: SberSpeakerClient) -> dict:
    """GET_STATE-схема + карта op-ответов."""
    out: dict = {}
    # GET_STATE структура. get_metadata первым активирует subscribe-stream;
    # несколько ретраев — на случай гонки с активной HA-сессией (тот же
    # client_id). Для чистого снимка лучше временно выключить интеграцию.
    try:
        await client.get_metadata()
    except Exception:  # noqa: BLE001
        pass
    for attempt in range(4):
        try:
            state = await client.get_state()
        except Exception:  # noqa: BLE001
            state = None
        if state is not None and state.raw_state_json:
            out["get_state_schema"] = schema_of(json.loads(state.raw_state_json))
            break
        await asyncio.sleep(0.8)
    else:
        out["get_state_schema"] = "error: GET_STATE не вернул JSON (конфликт сессии?)"

    # op-sweep (только read-probe пустым телом)
    op_map: dict[str, str] = {}
    for op in list(range(1, 13)) + list(range(17, 63)):
        if op in SKIP_OPS:
            continue
        try:
            resp = await client._request_response(_field(op, 2, _field(1, 2, b"")), timeout=2.0)
        except TimeoutError:
            op_map[str(op)] = "timeout"
            continue
        except Exception as exc:  # noqa: BLE001
            op_map[str(op)] = f"err:{type(exc).__name__}"
            continue
        s = resp.decode("utf-8", "ignore")
        i = s.find("{")
        if i >= 0 and (obj := _extract_json_object(s, i)):
            try:
                d = json.loads(obj)
                op_map[str(op)] = "json:" + ",".join(sorted(d.keys())[:20])
                continue
            except Exception:  # noqa: BLE001
                pass
        try:
            dec = _decode(resp)
            # Поле 3 = код ошибки → error; его отсутствие → успешный ack.
            op_map[str(op)] = "error" if 3 in dec else "ack"
        except Exception:  # noqa: BLE001
            op_map[str(op)] = f"raw:{len(resp)}b"
    out["op_map"] = op_map
    return out


async def _ensure_creds() -> dict:
    """Свой pairing recon'а (client_id+token). Первый раз — нажать «+»."""
    SNAP_DIR.mkdir(exist_ok=True)
    if CREDS.exists() and "--pair" not in sys.argv:
        return json.loads(CREDS.read_text())
    import uuid
    client_id = str(uuid.uuid4())
    client = SberSpeakerClient(host=HOST, port=20000, client_id=client_id,
                               client_name="fw-recon")
    await client.connect()
    print("👉 НАЖМИТЕ «+» на колонке (окно ~120 c)...", flush=True)
    try:
        token = await asyncio.wait_for(client.pair_with_button(), timeout=125)
    finally:
        await client.close()
    creds = {"client_id": client_id, "token": token}
    CREDS.write_text(json.dumps(creds))
    print("✅ recon получил собственный pairing — дальше без остановки HA.\n")
    return creds


async def main() -> None:
    creds = await _ensure_creds()
    snapshot: dict = {"host": HOST}

    client = SberSpeakerClient(host=HOST, port=20000, client_id=creds["client_id"],
                               client_name="fw-recon", pin_access_token=creds["token"])
    await client.connect()
    client.start_listening()
    try:
        snapshot.update(await collect_ws(client))
    finally:
        await client.close()

    snapshot["ports"] = scan_ports()
    snapshot["cli_commands"] = cli_commands()
    snapshot["libiio"] = libiio_devices()

    # версия прошивки из sys info (short — для имени снимка, full — для diff)
    fw, fw_build = _fw_version(snapshot["cli_commands"])
    snapshot["fw_version"] = fw
    snapshot["fw_build"] = fw_build
    # WSS-часть (op-map, GET_STATE) достоверна только без активной HA-сессии.
    snapshot["wss_reliable"] = isinstance(snapshot.get("get_state_schema"), dict)

    SNAP_DIR.mkdir(exist_ok=True)
    prev = _latest_snapshot()
    _save(snapshot, fw)

    if prev is None:
        print(f"✅ Первый снимок сохранён (fw={fw}). Запусти снова после обновления прошивки.")
        _print_summary(snapshot)
    else:
        print(f"=== DIFF: {prev.get('fw_version')} → {fw} ===\n")
        _print_diff(prev, snapshot)


def _fw_version(cli: dict) -> tuple[str, str]:
    """→ (short, full). short='26.1.7' — имя файла; full с git-хешем
    ('26.1.7+0.git.0a87d0bc...') ловит CI-пересборку при том же номере."""
    short, full = "unknown", "unknown"
    # sys info недоступен в cli_commands — снимем отдельно быстро
    try:
        s = socket.create_connection((HOST, 4242), timeout=3)
        s.settimeout(1.0)
        import time
        time.sleep(0.2)
        try:
            s.recv(4096)
        except OSError:
            pass
        s.sendall(b"sys info\n")
        time.sleep(0.4)
        buf = b""
        try:
            while True:
                d = s.recv(4096)
                if not d:
                    break
                buf += d
        except OSError:
            pass
        s.close()
        for line in buf.decode("utf-8", "ignore").splitlines():
            if "App version short" in line:
                short = line.split(":", 1)[1].strip()
            elif "App version" in line:  # полная строка с git-хешем
                full = line.split(":", 1)[1].strip()
    except OSError:
        pass
    return short, full


def _latest_snapshot() -> dict | None:
    if not SNAP_DIR.exists():
        return None
    snaps = sorted(SNAP_DIR.glob("*.json"))
    return json.loads(snaps[-1].read_text()) if snaps else None


def _save(snap: dict, fw: str) -> None:
    n = len(list(SNAP_DIR.glob(f"{fw}__*.json")))
    path = SNAP_DIR / f"{fw}__{n}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    print(f"снимок: {path.relative_to(SNAP_DIR.parent)}\n")


def _print_summary(snap: dict) -> None:
    print(f"  прошивка : {snap['fw_version']}")
    print(f"  порты    : {snap['ports']}")
    st = snap.get("get_state_schema", {})
    if isinstance(st, dict):
        print(f"  GET_STATE: {len(st)} подсистем")
    else:
        print("  ⚠ GET_STATE/op-map недостоверны — конфликт с активной HA-сессией.")
        print("    Для полного WSS-снимка временно выключите интеграцию sboom_ha")
        print("    (Settings → Devices → SBoom → ⋮ → Disable), затем запустите снова.")
    om = snap.get("op_map", {})
    jsonops = [op for op, v in om.items() if v.startswith("json")]
    print(f"  op с JSON-ответом: {jsonops}")
    for sec, cmds in snap.get("cli_commands", {}).items():
        print(f"  CLI {sec}: {cmds}")


def _diff_keys(a: dict, b: dict, path: str = "") -> list[str]:
    """Рекурсивный diff ключей двух схем."""
    out = []
    ak, bk = set(a) if isinstance(a, dict) else set(), set(b) if isinstance(b, dict) else set()
    for k in sorted(bk - ak):
        out.append(f"  + {path}{k}")
    for k in sorted(ak - bk):
        out.append(f"  - {path}{k}")
    for k in sorted(ak & bk):
        if isinstance(a[k], dict) and isinstance(b[k], dict):
            out += _diff_keys(a[k], b[k], f"{path}{k}.")
        elif a[k] != b[k]:
            out.append(f"  ~ {path}{k}: {a[k]} → {b[k]}")
    return out


def _print_diff(prev: dict, cur: dict) -> None:
    any_change = False
    # op-map/GET_STATE сравниваем только если ОБА снимка сняты без конфликта.
    wss_ok = prev.get("wss_reliable") and cur.get("wss_reliable")
    if not wss_ok:
        print("⚠ WSS-часть (op-map/GET_STATE) пропущена — один из снимков снят при\n"
              "  активной интеграции. Для сравнения op/полей снимайте оба при выключенной.\n")
    # порты
    if set(prev.get("ports", [])) != set(cur.get("ports", [])):
        any_change = True
        print("ПОРТЫ:")
        for p in sorted(set(cur["ports"]) - set(prev.get("ports", []))):
            print(f"  + порт {p}")
        for p in sorted(set(prev.get("ports", [])) - set(cur["ports"])):
            print(f"  - порт {p}")
        print()
    # GET_STATE схема + op-карта — только если обе стороны достоверны.
    if wss_ok:
        d = _diff_keys(prev.get("get_state_schema", {}), cur.get("get_state_schema", {}), "get_state.")
        if d:
            any_change = True
            print("GET_STATE (новые/изменённые поля):")
            print("\n".join(d), "\n")
        d = _diff_keys(prev.get("op_map", {}), cur.get("op_map", {}), "op ")
        if d:
            any_change = True
            print("OP-КАРТА (новые/изменившие ответ op):")
            print("\n".join(d), "\n")
    # CLI команды
    d = _diff_keys(prev.get("cli_commands", {}), cur.get("cli_commands", {}), "cli ")
    if d:
        any_change = True
        print("CLI-КОМАНДЫ (новые/удалённые):")
        print("\n".join(d), "\n")
    if not any_change:
        print("Изменений в схеме нет — прошивка не добавила новых op/полей/команд.")


if __name__ == "__main__":
    asyncio.run(main())

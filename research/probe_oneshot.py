"""Одноразовый максимально-информативный probe для ONE-SHOT порта.

Порт 33000 на колонке закрывается после первого TCP-connect и оживает только
после ребута. Поэтому за ЕДИНСТВЕННОЕ подключение надо выжать максимум:
  1) пассивно ждём unsolicited-баннер (telnet/ssh/http-push/repl);
  2) в ТОМ ЖЕ сокете шлём батарею проб от наименее к наиболее «разрушительным»,
     читая после каждой — если соединение оборвётся, ранние ответы уже собраны.

Запуск (ТОЛЬКО когда порт свеж — сразу после ребута колонки):
    python3 research/probe_oneshot.py 192.168.1.61 33000
"""
from __future__ import annotations

import os
import socket
import sys
import time

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.61"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 33000

# Пробы: (метка, байты). Порядок — от безобидного к более «странному».
PROBES: list[tuple[str, bytes]] = [
    ("LF", b"\n"),
    ("CRLF", b"\r\n"),
    ("help", b"help\r\n"),
    ("question", b"?\r\n"),
    ("version", b"version\r\n"),
    ("http-get", b"GET / HTTP/1.0\r\nHost: x\r\n\r\n"),
    ("gdb-rsp", b"+$?#3f"),          # GDB Remote Serial Protocol: halt reason
    ("json", b'{"cmd":"info"}\n'),
    ("nul", b"\x00"),
]


def _dump(tag: str, data: bytes) -> None:
    if not data:
        print(f"  [{tag}] (нет ответа)")
        return
    print(f"  [{tag}] {len(data)} байт")
    ascii_ = data.decode("utf-8", "replace")
    print("    ascii:", repr(ascii_[:400]))
    print("    hex  :", data[:80].hex(" "))


def _read(sock: socket.socket, wait: float) -> bytes:
    buf = b""
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait:
        try:
            d = sock.recv(8192)
            if d:
                buf += d
            else:
                break  # peer closed
        except (TimeoutError, socket.timeout):
            pass
        except OSError:
            break
        time.sleep(0.1)
    return buf


# ВАЖНО: 33000 умирает от ЛЮБОГО connect (даже refused). Поэтому его НЕЛЬЗЯ
# поллить. Готовность колонки после ребута определяем по БЕЗОПАСНОМУ порту
# (:4242 — не one-shot), потом ждём margin и делаем РОВНО ОДИН connect к 33000.
READY_PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 4242
MARGIN_SEC = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0


def _wait_ready(host: str, port: int, timeout: float = 240.0) -> bool:
    """Колонка поднялась? Поллим БЕЗОПАСНЫЙ порт (можно тыкать сколько угодно)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            s = socket.create_connection((host, port), timeout=3)
            s.close()
            return True
        except OSError:
            time.sleep(3)
    return False


def main() -> None:
    print(f"=== ONE-SHOT probe {HOST}:{PORT} ===")
    print(f"1) жду готовности колонки по :{READY_PORT} (безопасно поллить)…")
    if not _wait_ready(HOST, READY_PORT):
        print("колонка не поднялась за 240с — прервано")
        return
    print(f"2) колонка на :{READY_PORT} отвечает. Пауза {MARGIN_SEC:.0f}с "
          "(дать 33000 подняться)…")
    time.sleep(MARGIN_SEC)
    print(f"3) ЕДИНСТВЕННЫЙ выстрел по :{PORT}…\n")
    try:
        sock = socket.create_connection((HOST, PORT), timeout=6)
    except OSError as exc:
        print(f"connect REFUSED/FAILED: {exc}")
        print("→ 33000 ещё не открылся к моменту выстрела. Увеличь margin "
              "(4-й арг) и ребутни колонку снова.")
        return

    # TLS-режим (env TLS=1): тишина на весь плейнтекст → возможно это TLS-сервер
    # (как :20000). Пробуем handshake — узнаём версию/шифр/сертификат, затем
    # пробуем WS-upgrade поверх TLS (вдруг тот же WSS-стек, что на :20000).
    if os.environ.get("TLS"):
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ss = ctx.wrap_socket(sock, server_hostname="sberboom")
            print(f"TLS handshake OK: {ss.version()} | cipher={ss.cipher()}")
            der = ss.getpeercert(binary_form=True)
            print(f"cert DER: {len(der) if der else 0} байт")
            if der:
                try:
                    import ssl as _s
                    print("cert PEM (первые строки):")
                    print(_s.DER_cert_to_PEM_cert(der)[:200])
                except Exception:
                    pass
            ss.settimeout(1.0)
            ss.sendall(
                b"GET / HTTP/1.1\r\nHost: sberboom\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                b"Sec-WebSocket-Version: 13\r\nUser-Agent: WebSocket++/0.8.2\r\n\r\n"
            )
            _dump("ws-over-tls", _read(ss, 3.0))
            ss.close()
        except Exception as exc:  # noqa: BLE001
            print(f"TLS handshake FAILED: {exc!r}")
            print("→ значит НЕ TLS. Сервер ждёт иной бинарный протокол.")
        return

    # PASSIVE-режим (env PASSIVE=<секунды>): только слушаем, НИЧЕГО не шлём —
    # вдруг сервер стримит лог/дамп с задержкой, а наши send'ы сбивали.
    passive = os.environ.get("PASSIVE")
    if passive:
        sock.settimeout(1.0)
        secs = float(passive)
        print(f"PASSIVE: слушаю {secs:.0f}с без отправки…")
        _dump("passive-stream", _read(sock, secs))
        sock.close()
        print("\n=== конец (passive). ===")
        return
    sock.settimeout(0.8)
    print("connected. Фаза 1 — пассивный баннер (3с)...")
    _dump("banner", _read(sock, 3.0))

    print("\nФаза 2 — активные пробы (в том же сокете):")
    for label, payload in PROBES:
        try:
            sock.sendall(payload)
        except OSError as exc:
            print(f"  [{label}] send failed ({exc}) — соединение закрыто, стоп.")
            break
        _dump(label, _read(sock, 1.5))
    sock.close()
    print("\n=== конец. Порт теперь, вероятно, закрыт до следующего ребута. ===")


if __name__ == "__main__":
    main()

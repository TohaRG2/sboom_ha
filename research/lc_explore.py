"""Разведка `lc` (local control / движок сценариев) на debug-CLI :4242.

recon показал: lc → {db, script}. Здесь обходим поддерево и снимаем help
каждого узла + пробуем read-only запросы (list/dump/get/status). НИЧЕГО
не создаём и не запускаем — только help и перечисление. Текстовый CLI:
команда + LF, ответ читаем до тишины.

Запуск:  SBOOM_HOST=192.168.1.61 python3 research/lc_explore.py
"""
from __future__ import annotations

import os
import socket
import time

HOST = os.environ.get("SBOOM_HOST", "192.168.1.61")
PORT = 4242

# Дерево (проверено на fw 26.1.7): lc → {db, script}, у каждого — только
# read-only лист `print`. `lc script reload` мутирует (перечитывает
# хранилище) — НЕ дёргаем. Help-узлы безопасны, показывают структуру.
#   lc script print → "Total N scripts"  (движок AutomationController)
#   lc db print      → дамп БД в лог устройства, в сокет только "...complete"
PROBES = [
    "help",
    "lc",
    "lc db",
    "lc db print",
    "lc script",
    "lc script print",
]


def main() -> None:
    s = socket.create_connection((HOST, PORT), timeout=4)
    s.settimeout(1.2)
    time.sleep(0.3)
    try:
        s.recv(4096)  # приветственный баннер, если есть
    except OSError:
        pass

    def run(cmd: str) -> str:
        s.sendall((cmd + "\n").encode())
        time.sleep(1.0)  # print-команды отвечают с задержкой
        buf = b""
        try:
            while True:
                d = s.recv(4096)
                if not d:
                    break
                buf += d
        except OSError:
            pass
        return buf.decode("utf-8", "ignore").strip()

    for cmd in PROBES:
        out = run(cmd)
        print(f"\n$ {cmd}")
        print("-" * 60)
        print(out if out else "  (пусто)")
    s.close()


if __name__ == "__main__":
    main()

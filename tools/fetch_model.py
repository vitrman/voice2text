"""Параллельная загрузка чекпойнта GigaAM (CDN отдаёт ~30 КБ/с на соединение).

    python tools/fetch_model.py [--model v3_e2e_rnnt] [--workers 8]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import threading
import urllib.request
from pathlib import Path

HASHES = {
    "v3_e2e_rnnt": "2730de7545ac43ad256485a462b0a27a",
    "v3_e2e_ctc": "367074d6498f426d960b25f49531cf68",
    "v3_ctc": "73413e7be9c6a5935827bfab5c0dd678",
    "v3_rnnt": "0fd2c9a1ff66abd8d32a3a07f7592815",
}
BASE = "https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "gigaam"
CHUNK = 4 * 1024 * 1024


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_range(url: str, start: int, end: int, dest: Path) -> int:
    request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            with open(dest, "r+b") as fh:
                fh.seek(start)
                fh.write(data)
            return len(data)
        except Exception as exc:
            if attempt == 3:
                raise RuntimeError(f"фрагмент {start}-{end} не скачался: {exc}") from exc
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="v3_e2e_rnnt", choices=sorted(HASHES))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"{args.model}.ckpt"
    if dest.exists() and md5(dest) == HASHES[args.model]:
        print(f"{dest} уже скачан и совпадает по md5")
        return 0

    url = f"{BASE}/{args.model}.ckpt"
    head = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30)
    size = int(head.headers["Content-Length"])
    print(f"{args.model}: {size / 1e6:.0f} МБ, потоков {args.workers}")

    with open(dest, "wb") as fh:
        fh.truncate(size)

    ranges = [(start, min(start + CHUNK, size) - 1) for start in range(0, size, CHUNK)]
    queue = list(ranges)
    queue_lock = threading.Lock()
    done = {"bytes": 0}
    done_lock = threading.Lock()

    def worker() -> None:
        while True:
            with queue_lock:
                if not queue:
                    return
                start, end = queue.pop(0)
            chunk = fetch_range(url, start, end, dest)
            with done_lock:
                done["bytes"] += chunk

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for thread in threads:
        thread.start()
    while any(thread.is_alive() for thread in threads):
        print(f"  {done['bytes'] / 1e6:.0f}/{size / 1e6:.0f} МБ", flush=True)
        threading.Event().wait(10)
    for thread in threads:
        thread.join()

    actual = md5(dest)
    if actual != HASHES[args.model]:
        print(f"md5 не совпал: {actual}, ожидался {HASHES[args.model]}")
        return 1
    print(f"готово: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Поиск, проверка и докачка чекпойнтов GigaAM.

Модель весит 428 МБ, поэтому:
* сначала ищем валидный файл в нескольких каталогах и используем его на месте;
* качаем в несколько потоков (CDN отдаёт ~30 КБ/с на соединение);
* недокачанные куски запоминаем и при следующем запуске докачиваем, а не начинаем заново.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.request
from pathlib import Path

BASE = "https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM"
HASHES = {
    "v3_e2e_rnnt": "2730de7545ac43ad256485a462b0a27a",
    "v3_e2e_ctc": "367074d6498f426d960b25f49531cf68",
    "v3_rnnt": "0fd2c9a1ff66abd8d32a3a07f7592815",
    "v3_ctc": "73413e7be9c6a5935827bfab5c0dd678",
}
CHUNK = 4 * 1024 * 1024


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_valid(path: Path, name: str) -> bool:
    expected = HASHES.get(name)
    if not expected or not path.exists():
        return False
    try:
        return path.stat().st_size > 0 and md5(path) == expected
    except OSError:
        return False


def search_dirs(primary: Path) -> list[Path]:
    """Где искать готовую модель: сначала рядом с exe, потом кэш, потом temp."""
    dirs: list[Path] = []
    env = os.environ.get("VOICE2TEXT_MODEL_DIR")
    if env:
        dirs.append(Path(env))

    from paths import ROOT

    dirs += [ROOT.parent / "models", ROOT / "models", primary,
             ROOT / ".cache" / "gigaam",
             Path(os.environ.get("TEMP", "")) / "voice2text" / ".cache" / "gigaam"]
    result = []
    for directory in dirs:
        if directory and directory not in result:
            result.append(directory)
    return result


def find_model(name: str, primary: Path) -> Path | None:
    for directory in search_dirs(primary):
        candidate = directory / f"{name}.ckpt"
        if is_valid(candidate, name):
            return candidate
    return None


def _load_state(state_path: Path) -> set[int]:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return set(int(index) for index in data.get("done", []))
    except (OSError, ValueError):
        return set()


def _save_state(state_path: Path, done: set[int]) -> None:
    try:
        state_path.write_text(json.dumps({"done": sorted(done)}), encoding="utf-8")
    except OSError:
        pass


def _download(url: str, dest: Path, size: int, workers: int, progress, log) -> None:
    state_path = dest.with_suffix(".json")
    done = _load_state(state_path)
    if not dest.exists() or dest.stat().st_size != size:
        with open(dest, "wb") as fh:
            fh.truncate(size)
        done = set()

    ranges = [(start, min(start + CHUNK, size) - 1) for start in range(0, size, CHUNK)]
    queue = [(index, start, end) for index, (start, end) in enumerate(ranges)
             if index not in done]
    if done:
        log(f"докачиваю модель: осталось {len(queue)} из {len(ranges)} кусков")

    queue_lock = threading.Lock()
    state_lock = threading.Lock()
    counter = {"bytes": sum(end - start + 1 for index, start, end in ranges if index in done)}

    def worker() -> None:
        while True:
            with queue_lock:
                if not queue:
                    return
                index, start, end = queue.pop(0)
            request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
            for attempt in range(1, 4):
                try:
                    with urllib.request.urlopen(request, timeout=60) as response:
                        data = response.read()
                    with open(dest, "r+b") as fh:
                        fh.seek(start)
                        fh.write(data)
                    with state_lock:
                        done.add(index)
                        _save_state(state_path, done)
                        counter["bytes"] += len(data)
                        if progress:
                            progress(int(counter["bytes"] * 100 / max(size, 1)))
                    break
                except Exception:
                    if attempt == 3:
                        raise

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def _ensure_tokenizer(name: str, directory: Path, log) -> None:
    if "e2e" not in name:
        return
    tokenizer = directory / f"{name}_tokenizer.model"
    if tokenizer.exists() and tokenizer.stat().st_size > 1024:
        return
    for source_dir in search_dirs(directory):
        candidate = source_dir / f"{name}_tokenizer.model"
        if candidate.exists() and candidate.stat().st_size > 1024:
            try:
                tokenizer.write_bytes(candidate.read_bytes())
                return
            except OSError:
                pass
    log(f"скачиваю токенизатор {name}")
    urllib.request.urlretrieve(f"{BASE}/{name}_tokenizer.model", tokenizer)


def ensure_model(name: str, directory: Path, workers: int = 8, progress=None, log=print) -> Path:
    """Возвращает каталог с валидным чекпойнтом, скачивая только если нигде нет."""
    found = find_model(name, directory)
    if found is not None:
        log(f"модель {name}: использую {found}")
        _ensure_tokenizer(name, found.parent, log)
        return found.parent

    directory.mkdir(parents=True, exist_ok=True)
    ckpt = directory / f"{name}.ckpt"
    if ckpt.exists():
        log(f"чекпойнт {name} битый, перекачиваю")
        ckpt.unlink(missing_ok=True)

    url = f"{BASE}/{name}.ckpt"
    head = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30)
    size = int(head.headers["Content-Length"])
    log(f"скачиваю модель {name}: {size / 1e6:.0f} МБ")

    part = ckpt.with_suffix(".part")
    _download(url, part, size, workers, progress, log)
    if not is_valid(part, name):
        part.unlink(missing_ok=True)
        part.with_suffix(".json").unlink(missing_ok=True)
        raise RuntimeError(f"чекпойнт {name} скачался с ошибкой (md5 не совпал)")
    part.replace(ckpt)
    part.with_suffix(".json").unlink(missing_ok=True)

    _ensure_tokenizer(name, directory, log)
    return directory

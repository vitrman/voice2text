"""Обновление приложения из GitHub Releases.

Работает без токена и без gh: читает последний релиз через публичный API,
сравнивает версии, скачивает `*-installer.zip` (с проверкой sha256 и отменой),
запускает установку в тихом режиме и перезапускает приложение.

Установщику передаётся `/WAITPID=<наш pid>`: он сам ждёт выхода приложения,
ставит версию поверх и запускает exe. Раньше для этого писался пакетный файл
с путями внутри, и на машинах с кириллицей в имени пользователя cmd читал их
в чужой кодировке — обновление молча не устанавливалось.

Пользовательские данные (конфиг, модель, журналы) лежат в %LOCALAPPDATA%,
поэтому замена папки программы их не затрагивает.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

API = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = "Voice2Text-updater"
TIMEOUT = 20
CHUNK = 1 << 20
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CACHE_KEEP_DAYS = 7


class UpdateError(RuntimeError):
    """Общая ошибка обновления."""


class NoReleases(UpdateError):
    """На GitHub ещё нет ни одного опубликованного релиза (404)."""


class UpdateCancelled(UpdateError):
    """Пользователь отменил скачивание."""


def version_tuple(text: str) -> tuple[int, ...]:
    """`0.7.3.1` -> (0, 7, 3, 1); мусор в конце игнорируется."""
    numbers = [int(part) for part in re.findall(r"\d+", str(text))][:4]
    while len(numbers) < 4:
        numbers.append(0)
    return tuple(numbers)


def _request(url: str):
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NoReleases("на GitHub ещё нет опубликованных релизов") from exc
        if exc.code == 403:
            raise UpdateError("GitHub ограничил частоту запросов, попробуйте позже") from exc
        raise


def _read_small(url: str, limit: int = 4096) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read(limit).decode("utf-8", "replace")


def _sha_from_release(data: dict, asset: dict) -> str:
    """sha256 архива: поле digest у файла или отдельный `*.zip.sha256` в релизе."""
    digest = str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    for item in data.get("assets") or []:
        name = str(item.get("name") or "").lower()
        if not name.endswith("installer.zip.sha256"):
            continue
        try:
            parts = _read_small(item.get("browser_download_url") or "").split()
        except Exception:
            continue
        if parts and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return ""


def fetch_latest(repo: str) -> dict:
    """Последний опубликованный релиз: версия, ссылка, размер и sha256."""
    with _request(API.format(repo=repo)) as response:
        data = json.loads(response.read().decode("utf-8"))

    tag = str(data.get("tag_name") or data.get("name") or "").lstrip("vV")
    if not tag:
        raise UpdateError("в релизе нет тега версии")

    asset = None
    for item in data.get("assets") or []:
        name = str(item.get("name") or "")
        if name.lower().endswith("installer.zip"):
            asset = item
            break
    if asset is None:
        raise UpdateError(f"в релизе {tag} нет файла *-installer.zip")

    return {
        "version": tag,
        "name": asset["name"],
        "url": asset["browser_download_url"],
        "size": int(asset.get("size") or 0),
        "sha256": _sha_from_release(data, asset),
        "page": data.get("html_url") or "",
    }


def check(repo: str, current: str) -> dict | None:
    """Возвращает описание обновления, если на GitHub версия новее текущей."""
    latest = fetch_latest(repo)
    if version_tuple(latest["version"]) <= version_tuple(current):
        return None
    return latest


def cache_dir() -> Path:
    folder = Path(tempfile.gettempdir()) / "voice2text-update"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def archive_path(name: str) -> Path:
    return cache_dir() / Path(name).name


def cached(name: str, size: int = 0) -> Path | None:
    """Уже скачанный архив этой версии, если размер совпадает с релизом."""
    path = archive_path(name)
    if not path.is_file():
        return None
    if size and path.stat().st_size != size:
        return None
    return path


def verify_archive(archive: Path, expected_sha: str = "") -> None:
    """Проверяет архив до запуска установки: хеш и целостность zip."""
    if expected_sha:
        digest = hashlib.sha256()
        with open(archive, "rb") as fh:
            for block in iter(lambda: fh.read(CHUNK), b""):
                digest.update(block)
        if digest.hexdigest() != expected_sha:
            archive.unlink(missing_ok=True)
            raise UpdateError("архив обновления повреждён (не совпал sha256), скачайте ещё раз")
    try:
        with zipfile.ZipFile(archive) as bundle:
            broken = bundle.testzip()
    except (OSError, zipfile.BadZipFile) as exc:
        archive.unlink(missing_ok=True)
        raise UpdateError(f"архив обновления повреждён: {exc}") from exc
    if broken:
        archive.unlink(missing_ok=True)
        raise UpdateError(f"архив обновления повреждён: файл {broken}")


def download(url: str, name: str, on_progress=None, cancel=None, expected_size: int = 0,
             expected_sha: str = "") -> Path:
    """Качает архив в %TEMP%\\voice2text-update; повторно не качает уже скачанное."""
    target = archive_path(name)
    ready = cached(name, expected_size)
    if ready is not None:
        if on_progress is not None:
            on_progress(ready.stat().st_size, ready.stat().st_size)
        return ready

    part = target.with_suffix(target.suffix + ".part")
    part.unlink(missing_ok=True)
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(part, "wb") as out:
        total = expected_size or int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel is not None and cancel.is_set():
                out.close()
                part.unlink(missing_ok=True)
                raise UpdateCancelled("скачивание отменено")
            block = response.read(CHUNK)
            if not block:
                break
            out.write(block)
            digest.update(block)
            done += len(block)
            if on_progress is not None:
                on_progress(done, total)

    size = part.stat().st_size
    if expected_size and size != expected_size:
        part.unlink(missing_ok=True)
        raise UpdateError(f"архив скачался не целиком ({size} из {expected_size} байт)")
    if expected_sha and digest.hexdigest() != expected_sha:
        part.unlink(missing_ok=True)
        raise UpdateError("архив обновления повреждён (не совпал sha256)")
    part.replace(target)
    return target


def unpack(archive: Path) -> Path:
    """Распаковывает установщик во временную папку и возвращает путь к setup.exe."""
    folder = cache_dir() / f"unpacked-{archive.stem}"
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(folder)
    for candidate in folder.rglob("*setup*.exe"):
        return candidate
    raise UpdateError("в архиве обновления нет setup.exe")


def apply(archive: Path, target: Path | None, pid: int | None = None) -> Path:
    """Запускает установщик, передав ему PID: он дождётся выхода приложения.

    Аргументы процесса — Unicode, поэтому кириллица в путях безопасна.
    """
    setup = unpack(archive)
    if target is None:
        raise UpdateError("обновление доступно только для установленной версии")
    subprocess.Popen([str(setup), "/S", f"/D={target}", f"/WAITPID={pid or os.getpid()}"],
                     creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                     close_fds=True)
    return setup


def _dir_size(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def cleanup_cache(current_version: str) -> int:
    """Убирает отработавшие архивы и распаковки обновлений; возвращает освобождённые байты.

    Кэш нельзя чистить сразу после установки: он же служит «уже скачано, не качай
    заново». Зато на следующем запуске приложение точно работает с новой версии,
    значит всё, что не новее её, больше не нужно.
    """
    folder = cache_dir()
    current = version_tuple(current_version)
    now = time.time()
    freed = 0
    for entry in list(folder.iterdir()):
        try:
            old = now - entry.stat().st_mtime > CACHE_KEEP_DAYS * 86400
            if entry.is_dir():
                match = re.search(r"(\d+\.\d+\.\d+\.\d+)", entry.name)
                version = version_tuple(match.group(1)) if match else (0, 0, 0, 0)
                if old or version <= current:
                    freed += _dir_size(entry)
                    shutil.rmtree(entry, ignore_errors=True)
                continue
            if entry.name.endswith(".part"):
                freed += entry.stat().st_size
                entry.unlink(missing_ok=True)
                continue
            match = re.search(r"(\d+\.\d+\.\d+\.\d+)", entry.name)
            version = version_tuple(match.group(1)) if match else None
            if old or (version is not None and version <= current):
                freed += entry.stat().st_size
                entry.unlink(missing_ok=True)
        except OSError:
            continue
    return freed


def cleanup_install_leftovers(target: Path | None, days: int = 1) -> int:
    """Удаляет `*.old`, оставшиеся от неудачных установок рядом с exe."""
    if target is None or not target.is_dir():
        return 0
    now = time.time()
    freed = 0
    for path in target.glob("*.old"):
        try:
            if now - path.stat().st_mtime < days * 86400:
                continue
            size = path.stat().st_size
            path.unlink()
            freed += size
        except OSError:
            continue
    return freed


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def current_target() -> Path | None:
    """Папка программы для обновления; None, если запущено из исходников."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None

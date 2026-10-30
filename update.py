"""Обновление приложения из GitHub Releases.

Работает без токена и без gh: читает последний релиз через публичный API,
сравнивает версии, скачивает `*-installer.zip` (с докачкой и отменой),
запускает установку в тихом режиме и перезапускает приложение.
Пользовательские данные (конфиг, модель, журналы) лежат в %LOCALAPPDATA%, поэтому
замена папки программы их не затрагивает.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
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


def fetch_latest(repo: str) -> dict:
    """Последний опубликованный релиз: {'version', 'url', 'name', 'size', 'page'}."""
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


def download(url: str, name: str, on_progress=None, cancel=None, expected_size: int = 0) -> Path:
    """Качает архив в %TEMP%\\voice2text-update; повторно не качает уже скачанное."""
    target = archive_path(name)
    ready = cached(name, expected_size)
    if ready is not None:
        if on_progress is not None:
            on_progress(ready.stat().st_size, ready.stat().st_size)
        return ready

    part = target.with_suffix(target.suffix + ".part")
    part.unlink(missing_ok=True)
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
            done += len(block)
            if on_progress is not None:
                on_progress(done, total)

    size = part.stat().st_size
    if expected_size and size != expected_size:
        part.unlink(missing_ok=True)
        raise UpdateError(f"архив скачался не целиком ({size} из {expected_size} байт)")
    part.replace(target)
    return target


def unpack(archive: Path) -> Path:
    """Распаковывает установщик во временную папку и возвращает путь к setup.exe."""
    folder = cache_dir() / f"unpacked-{archive.stem}"
    if folder.exists():
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(folder)
    for candidate in folder.rglob("*setup*.exe"):
        return candidate
    raise UpdateError("в архиве обновления нет setup.exe")


def apply(archive: Path, target: Path | None, pid: int | None = None) -> Path:
    """Готовит тихое обновление и запускает его после выхода приложения.

    Возвращает путь к скрипту-помощнику: он ждёт завершения текущего процесса,
    ставит новую версию поверх папки программы и запускает exe снова.
    """
    setup = unpack(archive)
    if target is None:
        raise UpdateError("обновление доступно только для установленной версии")
    exe = target / "Voice2Text.exe"
    folder = cache_dir()
    helper = folder / "apply-update.cmd"
    log = folder / "install.log"
    wait_pid = pid or os.getpid()
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        ":wait",
        f'tasklist /FI "PID eq {wait_pid}" 2>nul | find "{wait_pid}" >nul',
        "if errorlevel 1 goto :run",
        "ping -n 2 127.0.0.1 >nul",
        "goto :wait",
        ":run",
        f'"{setup}" /S /D="{target}" >> "{log}" 2>&1',
        f'if exist "{exe}" start "" "{exe}"',
        "exit /b",
    ]
    helper.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    subprocess.Popen(["cmd", "/c", str(helper)],
                     creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                     close_fds=True)
    return helper


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

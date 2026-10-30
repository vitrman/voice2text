"""Обновление приложения из GitHub Releases.

Работает без токена и без gh: читает последний релиз через публичный API,
сравнивает версии, скачивает `*-installer.zip` и запускает его в тихом режиме.
Пользовательские данные (конфиг, модель, журналы) лежат в %LOCALAPPDATA%, поэтому
замена папки программы их не затрагивает.
"""

from __future__ import annotations

import ctypes
import json
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


def version_tuple(text: str) -> tuple[int, ...]:
    """`0.7.3.1` -> (0, 7, 3, 1); мусор в конце игнорируется."""
    numbers = [int(part) for part in re.findall(r"\d+", str(text))][:4]
    while len(numbers) < 4:
        numbers.append(0)
    return tuple(numbers)


class NoReleases(RuntimeError):
    """На GitHub ещё нет ни одного опубликованного релиза (404)."""


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
            raise RuntimeError("GitHub ограничил частоту запросов, попробуйте позже") from exc
        raise


def fetch_latest(repo: str) -> dict:
    """Последний опубликованный релиз: {'version', 'url', 'name', 'size', 'page'}."""
    with _request(API.format(repo=repo)) as response:
        data = json.loads(response.read().decode("utf-8"))

    tag = str(data.get("tag_name") or data.get("name") or "").lstrip("vV")
    if not tag:
        raise RuntimeError("в релизе нет тега версии")

    asset = None
    for item in data.get("assets") or []:
        name = str(item.get("name") or "")
        if name.lower().endswith("installer.zip"):
            asset = item
            break
    if asset is None:
        raise RuntimeError(f"в релизе {tag} нет файла *-installer.zip")

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


def download(url: str, name: str, on_progress=None) -> Path:
    """Качает архив в %TEMP%\\voice2text-update и возвращает путь."""
    folder = Path(tempfile.gettempdir()) / "voice2text-update"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (name or f"voice2text-{version_tuple(url)}.zip")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(target, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            block = response.read(CHUNK)
            if not block:
                break
            out.write(block)
            done += len(block)
            if on_progress is not None:
                on_progress(done, total)
    return target


def unpack(archive: Path) -> Path:
    """Распаковывает установщик во временную папку и возвращает путь к setup.exe."""
    folder = archive.parent / f"unpacked-{version_tuple(archive.stem)}"
    if folder.exists():
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(folder)
    for candidate in folder.rglob("*setup*.exe"):
        return candidate
    raise RuntimeError("в архиве обновления нет setup.exe")


def apply(archive: Path, target: Path | None) -> None:
    """Запускает тихую установку поверх текущей папки и перезапуск приложения."""
    setup = unpack(archive)
    if target is None:
        raise RuntimeError("обновление доступно только для установленной версии")
    exe = target / "Voice2Text.exe"
    command = (
        f'timeout /t 3 >nul & "{setup}" /S /D="{target}" & start "" "{exe}"'
    )
    subprocess.Popen(["cmd", "/c", command], creationflags=CREATE_NO_WINDOW)
    return None


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

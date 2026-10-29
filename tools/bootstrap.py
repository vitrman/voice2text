"""Доустановка двух пакетов, которых нет готовыми колёсами.

1. antlr4-python3-runtime — нужен hydra-core, на PyPI только sdist (сборка через
   setuptools в песочнице падает на chmod). Кладём пакет из tar.gz вручную.
2. gigaam с GitHub — в PyPI лежит 0.1.0 без моделей v3. Пакет чистый Python,
   поэтому копируем каталог gigaam в site-packages, без сборки.

Запускать интерпретатором venv: .venv\\Scripts\\python.exe tools\\bootstrap.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_PACKAGES = Path(sys.prefix) / "Lib" / "site-packages"
GIGAAM_TARBALL = "https://codeload.github.com/salute-developers/GigaAM/tar.gz/refs/heads/main"
ANTLR_PACKAGE = "antlr4-python3-runtime"
ANTLR_VERSION = "4.9.3"


def _download(url: str, dest: Path) -> Path:
    with urllib.request.urlopen(url, timeout=60) as response, open(dest, "wb") as fh:
        shutil.copyfileobj(response, fh)
    return dest


def _extract(archive: Path, dest: Path) -> None:
    """bsdtar есть в Windows 10+; tarfile с chmod в песочнице может упасть."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["tar", "-xf", str(archive), "-C", str(dest)], check=True,
                       capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        with tarfile.open(archive) as tar:
            tar.extractall(dest, filter="data")


def ensure_antlr4(tmp: Path) -> None:
    if (SITE_PACKAGES / "antlr4").exists():
        print("antlr4: уже на месте")
        return
    meta_url = f"https://pypi.org/pypi/{ANTLR_PACKAGE}/{ANTLR_VERSION}/json"
    with urllib.request.urlopen(meta_url, timeout=60) as response:
        meta = json.load(response)
    sdist = next(item for item in meta["urls"] if item["packagetype"] == "sdist")
    archive = _download(sdist["url"], tmp / "antlr4.tar.gz")
    unpacked = tmp / "antlr4"
    _extract(archive, unpacked)
    src = next(unpacked.glob("antlr4-python3-runtime-*/src/antlr4"))
    shutil.copytree(src, SITE_PACKAGES / "antlr4")
    print(f"antlr4 {ANTLR_VERSION}: установлен")


def ensure_gigaam(tmp: Path) -> None:
    target = SITE_PACKAGES / "gigaam"
    init = target / "__init__.py"
    if init.exists() and "v3_e2e_rnnt" in init.read_text(encoding="utf-8"):
        print("gigaam: версия с v3 уже на месте")
        return
    archive = _download(GIGAAM_TARBALL, tmp / "gigaam.tar.gz")
    unpacked = tmp / "gigaam-src"
    _extract(archive, unpacked)
    src = next(unpacked.glob("GigaAM-*/gigaam"))
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)
    print("gigaam: исходники с GitHub скопированы в site-packages")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="bootstrap-"))
    try:
        ensure_antlr4(tmp)
        ensure_gigaam(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    import importlib
    importlib.invalidate_caches()
    import gigaam  # noqa: F401  — проверка, что пакет импортируется

    print("проверка импорта: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

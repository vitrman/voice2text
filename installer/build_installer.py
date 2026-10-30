"""Сборка установщика Voice2Text.

1) пакует dist\\Voice2Text (без моделей) в payload.zip;
2) собирает setup exe через PyInstaller с payload.zip внутри;
3) подписывает самотестовым сертификатом, если он есть.

Запуск: .venv\\Scripts\\python.exe installer\\build_installer.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# свежая сборка приложения: dist-final, иначе старая dist
DIST = next((path for path in (ROOT / "dist-final" / "Voice2Text",
                               ROOT / "dist" / "Voice2Text") if path.is_dir()),
            ROOT / "dist" / "Voice2Text")
OUT = ROOT / "dist-installer"
WORK = ROOT / "build-installer"
SKIP_DIRS = {"models", "__pycache__"}


def read_version() -> str:
    text = (ROOT / "version.py").read_text(encoding="utf-8")
    match = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


def make_payload(target: Path) -> int:
    files = [path for path in DIST.rglob("*")
             if path.is_file() and not (SKIP_DIRS & set(path.relative_to(DIST).parts))]
    total_bytes = 0
    started = time.monotonic()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for index, path in enumerate(files, 1):
            archive.write(path, path.relative_to(DIST).as_posix())
            total_bytes += path.stat().st_size
            if index % 400 == 0 or index == len(files):
                done = index / max(1, len(files))
                print(f"  payload: {done * 100:5.1f}%  {index}/{len(files)} файлов", flush=True)
    print(f"payload.zip: {target.stat().st_size / 1e6:.0f} МБ "
          f"(исходные файлы {total_bytes / 1e6:.0f} МБ) за {time.monotonic() - started:.0f} с")
    return target.stat().st_size


def main() -> int:
    if not (DIST / "Voice2Text.exe").is_file():
        print("Сначала соберите приложение: build.cmd")
        return 1
    version = read_version()
    OUT.mkdir(exist_ok=True)
    payload = OUT / "payload.zip"
    print(f"[1/3] Упаковка {DIST} (версия {version})")
    make_payload(payload)

    (OUT / "version.txt").write_text(version, encoding="utf-8")
    print("[2/3] Сборка установщика (onedir: без распаковки во временную папку)")
    name = f"Voice2Text-{version}-setup"
    command = [
        str(ROOT / ".venv" / "Scripts" / "pyinstaller.exe"),
        "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", name,
        "--icon", str(ROOT / "assets" / "voice2text.ico"),
        "--distpath", str(OUT),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        str(ROOT / "installer" / "setup_app.py"),
    ]
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        print("PyInstaller завершился с ошибкой")
        return result.returncode

    setup_dir = OUT / name
    shutil.copy2(payload, setup_dir / "payload.zip")
    shutil.copy2(OUT / "version.txt", setup_dir / "version.txt")
    setup_exe = setup_dir / f"{name}.exe"
    print(f"[3/3] Подпись {setup_exe.name}")
    sign = ROOT / "tools" / "sign.ps1"
    if sign.is_file():
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(sign), "-Exe", str(setup_exe)], cwd=ROOT)

    archive = OUT / f"Voice2Text-{version}-installer.zip"
    if archive.exists():
        archive.unlink()
    print(f"Упаковка {archive.name}")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as bundle:
        for path in setup_dir.rglob("*"):
            if path.is_file():
                bundle.write(path, Path(name) / path.relative_to(setup_dir))
    print(f"\nГотово: {archive}  ({archive.stat().st_size / 1e6:.0f} МБ)")
    print(f"Внутри папка {name}: setup.exe + payload.zip")
    print("Проверка: setup.exe /S /D=<папка> /NOICONS /NOREG /NORUN")
    return 0


if __name__ == "__main__":
    sys.exit(main())

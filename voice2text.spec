# -*- mode: python ; coding: utf-8 -*-
"""Сборка: pyinstaller voice2text.spec --noconfirm"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# torch грузит msvcp140/vcruntime140_1 из Visual C++ Redistributable. На чистой
# Windows 10 этих файлов нет, и модель падала с «[WinError 126] ... shm.dll».
# Кладём их рядом с программой: установка перезапуском не требует.
_VC_DLLS = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
            "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll",
            "vcruntime140_1.dll", "vcruntime140_threads.dll", "concrt140.dll")
_system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
binaries = [(os.path.join(_system32, name), ".") for name in _VC_DLLS
            if os.path.isfile(os.path.join(_system32, name))]

hiddenimports = ["PIL._tkinter_finder", "pystray._win32", "uninstall",
                 "first_run", "mic_check", "autostart", "history", "jobs", "win32",
                 "calls", "call_transcribe", "loopback", "soundcard"]
for package in ("gigaam", "pystray", "hydra", "omegaconf", "sounddevice", "sentencepiece",
                "soundcard"):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        pass

datas = []
for package in ("gigaam", "pystray", "customtkinter"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["matplotlib", "pandas", "IPython", "notebook", "pytest", "tests",
              "torchvision", "tensorboard", "tkinter.test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Voice2Text",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/voice2text.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Voice2Text",
)

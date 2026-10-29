# -*- mode: python ; coding: utf-8 -*-
"""Сборка: pyinstaller voice2text.spec --noconfirm"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = ["PIL._tkinter_finder", "pystray._win32"]
for package in ("gigaam", "pystray", "hydra", "omegaconf", "sounddevice", "sentencepiece"):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        pass

datas = []
for package in ("gigaam", "pystray"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
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

"""Источники модели: основной CDN, своё зеркало и запасной GitHub Releases."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import model_fetch as mf  # noqa: E402


def test_sources_order() -> None:
    base = mf.sources()
    assert base[0] == mf.BASE, base
    assert mf.GITHUB_BASE in base, base
    assert base[-1] == mf.GITHUB_BASE, base

    os.environ["VOICE2TEXT_MODEL_BASE"] = "http://192.168.0.10/gigaam/"
    try:
        with_mirror = mf.sources()
    finally:
        os.environ.pop("VOICE2TEXT_MODEL_BASE", None)
    assert with_mirror[1] == "http://192.168.0.10/gigaam", with_mirror
    assert with_mirror[-1] == mf.GITHUB_BASE
    print("порядок источников: ок")


def test_fallback_to_github() -> None:
    folder = Path(tempfile.gettempdir()) / "model-fallback"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)

    calls: list[tuple[str, str]] = []
    original = (mf._probe, mf._download, mf.is_valid, mf._ensure_tokenizer, mf.search_dirs)

    def fake_probe(url: str) -> int:
        calls.append(("probe", url))
        if "cdn.chatwm" in url:
            raise OSError("CERTIFICATE_VERIFY_FAILED certificate has expired")
        return 4096

    def fake_download(url, dest, size, workers, progress, log, cancel=None) -> None:
        calls.append(("download", url))
        dest.write_bytes(b"checkpoint")

    mf.search_dirs = lambda primary: [primary]      # не смотрим реальные каталоги машины
    mf._probe = fake_probe
    mf._download = fake_download
    mf.is_valid = lambda path, name: Path(path).exists()   # «валиден» только реальный файл
    mf._ensure_tokenizer = lambda name, directory, log: None
    try:
        result = mf.ensure_model("v3_e2e_rnnt", folder, log=lambda _m: None)
    finally:
        (mf._probe, mf._download, mf.is_valid, mf._ensure_tokenizer,
         mf.search_dirs) = original
        shutil.rmtree(folder, ignore_errors=True)

    assert result == folder, result
    downloaded = [url for kind, url in calls if kind == "download"]
    assert len(downloaded) == 1 and "github.com" in downloaded[0], downloaded
    print("переключение на GitHub: ок")


def test_all_sources_fail() -> None:
    folder = Path(tempfile.gettempdir()) / "model-fail"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    original = (mf._probe, mf._ensure_tokenizer, mf.search_dirs)

    def broken(url: str) -> int:
        raise OSError("CERTIFICATE_VERIFY_FAILED certificate has expired")

    mf.search_dirs = lambda primary: [primary]
    mf._probe = broken
    mf._ensure_tokenizer = lambda name, directory, log: None
    try:
        mf.ensure_model("v3_e2e_rnnt", folder, log=lambda _m: None)
    except mf.ModelDownloadError as exc:
        text = str(exc)
    else:
        raise AssertionError("ожидалась ModelDownloadError")
    finally:
        mf._probe, mf._ensure_tokenizer, mf.search_dirs = original
        shutil.rmtree(folder, ignore_errors=True)

    assert "ни с одного источника" in text
    assert "сертификат не принят" in text
    assert "VOICE2TEXT_MODEL_DIR" in text
    print("понятная ошибка, когда нет ни одного источника: ок")


if __name__ == "__main__":
    test_sources_order()
    test_fallback_to_github()
    test_all_sources_fail()
    print("все проверки источников модели прошли")

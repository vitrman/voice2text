"""Выбор релиза для обновления: релиз моделей не должен ломать проверку версии."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import update  # noqa: E402

MODELS_RELEASE = {
    "tag_name": "models-v1",
    "name": "Модели GigaAM v3",
    "html_url": "https://github.com/vitrman/voice2text/releases/tag/models-v1",
    "prerelease": True,
    "assets": [
        {"name": "v3_e2e_rnnt.ckpt", "size": 448929252,
         "browser_download_url": "https://example/v3_e2e_rnnt.ckpt"},
    ],
}

APP_RELEASE = {
    "tag_name": "v0.15.1.1",
    "name": "0.15.1.1",
    "html_url": "https://github.com/vitrman/voice2text/releases/tag/v0.15.1.1",
    "assets": [
        {"name": "Voice2Text-0.15.1.1-installer.zip", "size": 1000,
         "digest": "sha256:" + "a" * 64,
         "browser_download_url": "https://example/installer.zip"},
    ],
}


class Response(io.BytesIO):
    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args) -> bool:
        return False


def install_fake(pages: dict[str, object]) -> list[str]:
    calls: list[str] = []

    def fake_request(url: str):
        calls.append(url)
        if "/releases/latest" in url:
            payload = pages["latest"]
        else:
            payload = pages["list"]
        return Response(json.dumps(payload).encode("utf-8"))

    update._request = fake_request          # type: ignore[assignment]
    return calls


def test_skips_models_release() -> None:
    calls = install_fake({"latest": MODELS_RELEASE, "list": [APP_RELEASE, MODELS_RELEASE]})
    result = update.fetch_latest("vitrman/voice2text")
    assert result["version"] == "0.15.1.1", result
    assert result["name"] == "Voice2Text-0.15.1.1-installer.zip"
    assert len(calls) == 2, calls          # /latest, затем список
    print("релиз моделей пропущен, взят установщик: ок")


def test_no_installer_anywhere() -> None:
    install_fake({"latest": MODELS_RELEASE, "list": [MODELS_RELEASE]})
    try:
        update.fetch_latest("vitrman/voice2text")
    except update.UpdateError as exc:
        text = str(exc)
    else:
        raise AssertionError("ожидалась UpdateError")
    assert "нет файла" in text or "не версия программы" in text, text
    print("нет установщика ни в одном релизе: ок")


if __name__ == "__main__":
    original = update._request
    try:
        test_skips_models_release()
        test_no_installer_anywhere()
    finally:
        update._request = original
    print("все проверки выбора релиза прошли")

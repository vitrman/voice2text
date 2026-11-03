"""Список микрофонов: дубли host API схлопываются, выбор устройства восстанавливается."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import recorder  # noqa: E402

APIS = [
    {"name": "MME"},
    {"name": "Windows DirectSound"},
    {"name": "Windows WASAPI"},
    {"name": "Windows WDM-KS"},
]

# (имя, hostapi) — как их отдаёт PortAudio на типичной машине
DEVICES = [
    {"name": "Микрофон (USB Audio Device)", "hostapi": 0, "max_input_channels": 2},
    {"name": "Микрофон (USB Audio Device)", "hostapi": 1, "max_input_channels": 2},
    {"name": "Микрофон (USB Audio Device)", "hostapi": 2, "max_input_channels": 2},
    {"name": "Микрофон (USB Audio Device)", "hostapi": 3, "max_input_channels": 2},
    {"name": "Микрофон (Realtek(R) Audio)", "hostapi": 2, "max_input_channels": 2},
    {"name": "Микрофон (Realtek(R) Audio)", "hostapi": 0, "max_input_channels": 2},
    {"name": "Стерео микшер (Realtek HD Audio Stereo input)", "hostapi": 0,
     "max_input_channels": 2},
    {"name": "Лин. вход (Realtek HD Audio Line input)", "hostapi": 3,
     "max_input_channels": 2},
    {"name": "Динамики (Realtek(R) Audio)", "hostapi": 2, "max_input_channels": 0},
]


def install_fake() -> None:
    recorder.sd.query_devices = lambda: DEVICES
    recorder.sd.query_hostapis = lambda: APIS


def main() -> int:
    results: list[bool] = []
    install_fake()

    items = recorder.input_device_items()
    labels = [item["label"] for item in items]
    print("список:", labels)
    print("всего вариантов:", len(recorder.input_device_items(True)))

    unique = len(labels) == len(set(labels))
    results.append(unique)
    print(f"{'ok  ' if unique else 'FAIL'} подписи не повторяются")

    usb = [item for item in items if item["name"] == "Микрофон (USB Audio Device)"]
    preferred = len(usb) == 1 and usb[0]["api"] == "Windows WASAPI"
    results.append(preferred)
    print(f"{'ok  ' if preferred else 'FAIL'} USB-микрофон один и через WASAPI: {usb}")

    wdm = [item for item in items if item["api"] == "Windows WDM-KS"]
    results.append(not wdm)
    print(f"{'ok  ' if not wdm else 'FAIL'} WDM-KS скрыт в обычном списке")

    everything = recorder.input_device_items(True)
    results.append(len(everything) == 8)      # 9 устройств минус выходной
    print(f"{'ok  ' if len(everything) == 8 else 'FAIL'} all_variants показывает всё: {len(everything)}")

    # восстановление устройства по имени и host API
    index, found = recorder.device_index(
        {"name": "Микрофон (USB Audio Device)", "hostapi": 0})
    ok = found and index == 0
    results.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'} MME-вариант находится по hostapi: index={index}")

    index, found = recorder.device_index({"name": "Микрофон (USB Audio Device)"})
    ok = found and index == 2
    results.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'} без hostapi берётся лучший вариант: index={index}")

    index, found = recorder.device_index({"name": "Нет такого микрофона"})
    ok = (not found) and index is None
    results.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'} неизвестное устройство -> по умолчанию")

    ok = all(results)
    print("результат:", "ok" if ok else "ОШИБКА")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

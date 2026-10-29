"""Диалог настроек."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import hotkey_capture
from hotkeys import parse_hotkey

MODELS = ["v3_e2e_rnnt", "v3_e2e_ctc", "v3_rnnt", "v3_ctc"]

MODIFIER_KEYS = {"Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
                 "Win_L", "Win_R", "Super_L", "Super_R", "Caps_Lock"}
KEY_NAMES = {"space": "space", "Return": "enter", "Escape": "esc", "Tab": "tab",
             "BackSpace": "backspace", "Delete": "delete", "Insert": "insert",
             "Home": "home", "End": "end", "Prior": "pageup", "Next": "pagedown",
             "Left": "left", "Right": "right", "Up": "up", "Down": "down"}


def _input_devices() -> list[str]:
    try:
        from recorder import list_input_devices
        return ["По умолчанию"] + list_input_devices()
    except Exception:
        return ["По умолчанию"]


def _replacements_to_text(replacements: dict) -> str:
    return "\n".join(f"{src} = {dst}" for src, dst in replacements.items())


def _text_to_replacements(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        source, target = line.split("=", 1)
        source, target = source.strip(), target.strip()
        if source and target:
            result[source] = target
    return result


def open_settings(parent: tk.Tk, config) -> dict | None:
    """Показывает модальное окно. Возвращает новые значения или None."""
    window = tk.Toplevel(parent)
    window.title("Настройки")
    # transient не ставим: если главное окно свёрнуто, дочернее остаётся невидимым
    window.grab_set()
    window.resizable(False, False)

    body = ttk.Frame(window, padding=14)
    body.pack(fill="both", expand=True)

    row = 0

    def label(text: str):
        nonlocal row
        ttk.Label(body, text=text).grid(row=row, column=0, sticky="w", pady=(0, 4))

    ttk.Label(body, text="Модель").grid(row=row, column=0, sticky="w", pady=(0, 2))
    model_var = tk.StringVar(value=config["model"])
    ttk.Combobox(body, textvariable=model_var, values=MODELS, state="readonly",
                 width=32).grid(row=row, column=1, sticky="ew", pady=(0, 8))
    row += 1

    devices = _input_devices()
    current_device = "По умолчанию"
    if config.get("device") is not None:
        for item in devices:
            if item.startswith(f"{config['device']}:"):
                current_device = item
                break
    ttk.Label(body, text="Микрофон").grid(row=row, column=0, sticky="w", pady=(0, 2))
    device_var = tk.StringVar(value=current_device)
    ttk.Combobox(body, textvariable=device_var, values=devices, state="readonly",
                 width=32).grid(row=row, column=1, sticky="ew", pady=(0, 8))
    row += 1

    capture = {"var": None, "label": None, "after": None}

    def _stop_capture():
        if capture["after"] is not None:
            try:
                window.after_cancel(capture["after"])
            except Exception:
                pass
            capture["after"] = None
        hotkey_capture.cancel_capture()

    def _accept_combo(combo: str) -> str | None:
        """Проверяет записанную комбинацию; текст ошибки или None."""
        parts = [part for part in combo.lower().split("+") if part]
        if not parts:
            return "ничего не нажато"
        if "win" in parts:
            return "Win не поддерживается"
        if len(parts) < 2:
            return "нужна комбинация: модификатор + клавиша"
        if not any(part in ("ctrl", "shift", "alt") for part in parts[:-1]):
            return "добавьте Ctrl, Shift или Alt"
        try:
            parse_hotkey(combo)
        except ValueError as exc:
            return str(exc)
        return None

    def _apply_combo(combo: str) -> None:
        error = _accept_combo(combo)
        if error:
            capture["label"].configure(text="×")
            capture_note.configure(text=f"{error}. Старое значение оставлено.",
                                   foreground="#b03030")
            return
        capture["var"].set(combo)
        capture["label"].configure(text="✓")
        capture_note.configure(text=f"Записано: {combo}", foreground="#2f7d32")

    def _hook_poll():
        combo = hotkey_capture.poll_capture()
        if combo is None:
            try:
                capture["after"] = window.after(60, _hook_poll)
            except Exception:
                _stop_capture()
            return
        capture["after"] = None
        if combo:
            _apply_combo(combo)
        else:
            capture["label"].configure(text="")
            capture_note.configure(text="Запись отменена", foreground="#666")

    def _hook_timeout():
        if capture["after"] is None:
            return
        _stop_capture()
        capture["label"].configure(text="")

    def start_capture(var, label):
        capture["var"], capture["label"] = var, label
        label.configure(text="…")
        window.focus_force()
        if hotkey_capture.HOOK_READY["ok"]:
            hotkey_capture.start_capture()
            _hook_poll()
            window.after(8000, _hook_timeout)   # не держать клавиши перехваченными
        else:
            window.bind("<KeyPress>", on_capture)

    def on_capture(event):
        if event.keysym in MODIFIER_KEYS:
            return "break"
        if event.keysym == "Escape":
            window.unbind("<KeyPress>")
            capture["label"].configure(text="")
            return "break"
        mods = []
        if event.state & 0x0004:
            mods.append("ctrl")
        if event.state & 0x0001:
            mods.append("shift")
        if event.state & 0x20000:
            mods.append("alt")
        if event.keysym in KEY_NAMES:
            name = KEY_NAMES[event.keysym]
        elif event.char and len(event.char) == 1 and event.char.isprintable():
            name = event.char.lower()          # кириллица и прочие символы
        else:
            name = event.keysym.lower()
        capture["var"].set("+".join(mods + [name]))
        _apply_combo("+".join(mods + [name]))
        window.unbind("<KeyPress>")
        return "break"

    def hotkey_row(title: str, var: tk.StringVar) -> None:
        nonlocal row
        ttk.Label(body, text=title).grid(row=row, column=0, sticky="w", pady=(0, 2))
        holder = ttk.Frame(body)
        holder.grid(row=row, column=1, sticky="ew", pady=(0, 8))
        ttk.Entry(holder, textvariable=var, width=22).pack(side="left", fill="x", expand=True)
        hint = ttk.Label(holder, text="", width=3)
        hint.pack(side="left", padx=(4, 0))
        ttk.Button(holder, text="Записать", width=9,
                   command=lambda: start_capture(var, hint)).pack(side="left", padx=(4, 0))
        row += 1

    dictate_var = tk.StringVar(value=config["hotkey"])
    hotkey_row("Хоткей диктовки", dictate_var)
    show_var = tk.StringVar(value=config["show_hotkey"])
    hotkey_row("Хоткей окна", show_var)

    capture_note = ttk.Label(
        body, text="Комбинация: Ctrl/Shift/Alt + клавиша. Win и одиночные клавиши не поддерживаются.",
        foreground="#666")
    capture_note.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
    row += 1

    ttk.Label(body, text="Режим хоткея").grid(row=row, column=0, sticky="w", pady=(0, 2))
    mode_var = tk.StringVar(value="удерживать" if config.get("hotkey_mode") == "hold"
                             else "переключатель")
    ttk.Combobox(body, textvariable=mode_var, state="readonly", width=32,
                 values=["переключатель", "удерживать"]).grid(row=row, column=1, sticky="ew", pady=(0, 8))
    row += 1

    ttk.Label(body, text="Пауза «конец фразы», мс").grid(row=row, column=0, sticky="w", pady=(0, 2))
    silence_var = tk.IntVar(value=int(config["silence_ms"]))
    ttk.Spinbox(body, from_=200, to=3000, increment=50, textvariable=silence_var,
                width=10).grid(row=row, column=1, sticky="w", pady=(0, 8))
    row += 1

    ttk.Label(body, text="Максимум записи, с").grid(row=row, column=0, sticky="w", pady=(0, 2))
    max_var = tk.IntVar(value=int(config["max_seconds"]))
    ttk.Spinbox(body, from_=10, to=600, increment=10, textvariable=max_var,
                width=10).grid(row=row, column=1, sticky="w", pady=(0, 8))
    row += 1

    autopaste_var = tk.BooleanVar(value=bool(config["autopaste"]))
    ttk.Checkbutton(body, text="Вставлять текст в активное окно (Ctrl+V)",
                    variable=autopaste_var).grid(row=row, column=0, columnspan=2,
                                                 sticky="w", pady=(0, 4))
    row += 1

    minimized_var = tk.BooleanVar(value=bool(config["start_minimized"]))
    ttk.Checkbutton(body, text="Запускать свёрнутым", variable=minimized_var).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
    row += 1

    ttk.Label(body, text="Замены (по строке: как слышится = Как надо)").grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(0, 2))
    row += 1
    replacements = tk.Text(body, height=6, width=54, wrap="word")
    replacements.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 10))
    replacements.insert("1.0", _replacements_to_text(config["replacements"]))

    body.columnconfigure(1, weight=1)
    body.rowconfigure(row, weight=1)

    result: dict | None = None

    def on_save() -> None:
        nonlocal result
        try:
            for var in (dictate_var, show_var):
                parse_hotkey(var.get())
        except ValueError as exc:
            messagebox.showerror("Настройки", f"Хоткей: {exc}", parent=window)
            return
        if silence_var.get() >= max_var.get() * 1000:
            messagebox.showerror("Настройки", "Пауза больше максимальной длительности",
                                 parent=window)
            return

        device_text = device_var.get()
        device = None if device_text == "По умолчанию" else int(device_text.split(":", 1)[0])
        result = {
            "model": model_var.get(),
            "device": device,
            "hotkey": dictate_var.get().strip().lower(),
            "show_hotkey": show_var.get().strip().lower(),
            "silence_ms": int(silence_var.get()),
            "max_seconds": int(max_var.get()),
            "hotkey_mode": "hold" if mode_var.get() == "удерживать" else "toggle",
            "autopaste": bool(autopaste_var.get()),
            "start_minimized": bool(minimized_var.get()),
            "replacements": _text_to_replacements(replacements.get("1.0", "end-1c")),
        }
        window.destroy()

    buttons = ttk.Frame(body)
    buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e", pady=(6, 0))
    ttk.Button(buttons, text="Отмена", command=window.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(buttons, text="Сохранить", command=on_save).pack(side="right")

    window.update_idletasks()
    width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    x = (window.winfo_screenwidth() - width) // 2
    y = max(40, (window.winfo_screenheight() - height) // 3)
    window.geometry(f"+{x}+{y}")
    window.lift()
    window.focus_force()
    window.attributes("-topmost", True)
    window.after(400, lambda: window.attributes("-topmost", False))
    window.bind("<Destroy>", lambda _e: _stop_capture())

    window.wait_window()
    return result

"""Диалог настроек в тёмной теме CustomTkinter."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

import hotkey_capture
from hotkeys import parse_hotkey

MODELS = ["v3_e2e_rnnt", "v3_e2e_ctc", "v3_rnnt", "v3_ctc"]

MODIFIER_KEYS = {"Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
                 "Win_L", "Win_R", "Super_L", "Super_R", "Caps_Lock"}
KEY_NAMES = {"space": "space", "Return": "enter", "Escape": "esc", "Tab": "tab",
             "BackSpace": "backspace", "Delete": "delete", "Insert": "insert",
             "Home": "home", "End": "end", "Prior": "pageup", "Next": "pagedown",
             "Left": "left", "Right": "right", "Up": "up", "Down": "down"}

BG = "#121316"
CARD = "#1b1d21"
BORDER = "#2e3238"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#3d78e6"
TEXT = "#f2f3f5"
MUTED = "#8b9199"
ERROR = "#e0554f"
OK = "#4caf68"


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


def open_settings(parent: tk.Misc, config) -> dict | None:
    """Показывает модальное окно. Возвращает новые значения или None."""
    window = ctk.CTkToplevel(parent)
    window.title("Настройки")
    window.configure(fg_color=BG)
    window.resizable(False, False)
    window.grab_set()

    footer = ctk.CTkFrame(window, fg_color="transparent")
    footer.pack(side="bottom", fill="x", padx=14, pady=(0, 12))

    body = ctk.CTkScrollableFrame(window, fg_color="transparent", width=560, height=600)
    body.pack(fill="both", expand=True, padx=6, pady=(6, 0))

    def label(text: str) -> None:
        ctk.CTkLabel(body, text=text, text_color=TEXT, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 13)).pack(fill="x", padx=8, pady=(10, 2))

    def hint(text: str, color: str = MUTED) -> ctk.CTkLabel:
        widget = ctk.CTkLabel(body, text=text, text_color=color, anchor="w", justify="left",
                              font=ctk.CTkFont("Segoe UI", 11))
        widget.pack(fill="x", padx=8, pady=(0, 4))
        return widget

    label("Модель")
    model_var = tk.StringVar(value=config["model"])
    ctk.CTkComboBox(body, values=MODELS, variable=model_var, state="readonly",
                    corner_radius=10, fg_color=CARD, border_color=BORDER,
                    button_color=BORDER, button_hover_color=CARD,
                    font=ctk.CTkFont("Segoe UI", 13)).pack(fill="x", padx=8, pady=(0, 4))

    devices = _input_devices()
    current_device = "По умолчанию"
    if config.get("device") is not None:
        for item in devices:
            if item.startswith(f"{config['device']}:"):
                current_device = item
                break
    label("Микрофон")
    device_var = tk.StringVar(value=current_device)
    ctk.CTkComboBox(body, values=devices, variable=device_var, state="readonly",
                    corner_radius=10, fg_color=CARD, border_color=BORDER,
                    button_color=BORDER, button_hover_color=CARD,
                    font=ctk.CTkFont("Segoe UI", 13)).pack(fill="x", padx=8, pady=(0, 4))

    capture = {"var": None, "label": None, "after": None}

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

    def _stop_capture():
        if capture["after"] is not None:
            try:
                window.after_cancel(capture["after"])
            except Exception:
                pass
            capture["after"] = None
        hotkey_capture.cancel_capture()

    def _apply_combo(combo: str) -> None:
        error = _accept_combo(combo)
        if error:
            capture["label"].configure(text="×", text_color=ERROR)
            capture_note.configure(text=f"{error}. Старое значение оставлено.", text_color=ERROR)
            return
        capture["var"].set(combo)
        capture["label"].configure(text="✓", text_color=OK)
        capture_note.configure(text=f"Записано: {combo}", text_color=OK)

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
            capture_note.configure(text="Запись отменена", text_color=MUTED)

    def _hook_timeout():
        if capture["after"] is None:
            return
        _stop_capture()
        capture["label"].configure(text="")

    def start_capture(var, label_widget):
        capture["var"], capture["label"] = var, label_widget
        label_widget.configure(text="…", text_color=MUTED)
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
        _apply_combo("+".join(mods + [name]))
        window.unbind("<KeyPress>")
        return "break"

    def hotkey_row(title: str, var: tk.StringVar) -> None:
        label(title)
        holder = ctk.CTkFrame(body, fg_color="transparent")
        holder.pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkEntry(holder, textvariable=var, corner_radius=10, fg_color=CARD,
                     border_color=BORDER, font=ctk.CTkFont("Segoe UI", 13)).pack(
            side="left", fill="x", expand=True)
        mark = ctk.CTkLabel(holder, text="", width=18, font=ctk.CTkFont("Segoe UI", 13))
        mark.pack(side="left", padx=(6, 0))
        ctk.CTkButton(holder, text="Записать", width=96, height=30, corner_radius=9,
                      fg_color=CARD, hover_color=CARD, border_width=1, border_color=BORDER,
                      text_color=TEXT, font=ctk.CTkFont("Segoe UI", 12),
                      command=lambda: start_capture(var, mark)).pack(side="left", padx=(6, 0))

    dictate_var = tk.StringVar(value=config["hotkey"])
    hotkey_row("Хоткей диктовки", dictate_var)
    show_var = tk.StringVar(value=config["show_hotkey"])
    hotkey_row("Хоткей окна", show_var)

    capture_note = hint("Комбинация: Ctrl/Shift/Alt + клавиша. Win и одиночные клавиши не поддерживаются.")

    label("Режим хоткея")
    mode_var = tk.StringVar(value="удерживать" if config.get("hotkey_mode") == "hold"
                             else "переключатель")
    ctk.CTkComboBox(body, values=["переключатель", "удерживать"], variable=mode_var,
                    state="readonly", corner_radius=10, fg_color=CARD, border_color=BORDER,
                    button_color=BORDER, button_hover_color=CARD,
                    font=ctk.CTkFont("Segoe UI", 13)).pack(fill="x", padx=8, pady=(0, 4))

    auto_stop_var = tk.BooleanVar(value=bool(config.get("auto_stop", True)))
    silence_var = tk.IntVar(value=int(config["silence_ms"]))
    silence_row = ctk.CTkFrame(body, fg_color="transparent")
    silence_row.pack(fill="x", padx=8, pady=(6, 4))
    ctk.CTkCheckBox(silence_row, text="Останавливать по паузе, мс", variable=auto_stop_var,
                    font=ctk.CTkFont("Segoe UI", 13), checkbox_width=20, checkbox_height=20,
                    corner_radius=6, fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(side="left")
    silence_entry = ctk.CTkEntry(silence_row, textvariable=silence_var, width=80,
                                 corner_radius=10, fg_color=CARD, border_color=BORDER)
    silence_entry.pack(side="left", padx=(10, 0))

    label("Максимум записи, с")
    max_var = tk.IntVar(value=int(config["max_seconds"]))
    ctk.CTkEntry(body, textvariable=max_var, width=100, corner_radius=10,
                 fg_color=CARD, border_color=BORDER).pack(anchor="w", padx=8, pady=(0, 4))
    hint("Без автостопа запись идёт до «Стоп» или этого лимита.")

    autopaste_var = tk.BooleanVar(value=bool(config["autopaste"]))
    ctk.CTkCheckBox(body, text="Вставлять текст в активное окно (Ctrl+V)",
                    variable=autopaste_var, font=ctk.CTkFont("Segoe UI", 13),
                    checkbox_width=20, checkbox_height=20, corner_radius=6,
                    fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(anchor="w", padx=8, pady=(10, 2))

    minimized_var = tk.BooleanVar(value=bool(config["start_minimized"]))
    ctk.CTkCheckBox(body, text="Запускать свёрнутым", variable=minimized_var,
                    font=ctk.CTkFont("Segoe UI", 13), checkbox_width=20, checkbox_height=20,
                    corner_radius=6, fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(anchor="w", padx=8, pady=(2, 10))

    label("Замены (по строке: как слышится = Как надо)")
    replacements = ctk.CTkTextbox(body, height=110, corner_radius=10, fg_color=CARD,
                                  border_width=1, border_color=BORDER,
                                  font=ctk.CTkFont("Segoe UI", 12))
    replacements.pack(fill="x", padx=8, pady=(0, 10))
    replacements.insert("1.0", _replacements_to_text(config["replacements"]))

    def _sync_silence_state() -> None:
        silence_entry.configure(state="normal" if auto_stop_var.get() else "disabled")

    auto_stop_var_trace = auto_stop_var.trace_add("write", lambda *_: _sync_silence_state())
    _sync_silence_state()

    result: dict | None = None

    def on_save() -> None:
        nonlocal result
        try:
            for var in (dictate_var, show_var):
                parse_hotkey(var.get())
        except ValueError as exc:
            messagebox.showerror("Настройки", f"Хоткей: {exc}", parent=window)
            return
        if auto_stop_var.get() and int(silence_var.get()) >= int(max_var.get()) * 1000:
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
            "silence_ms": max(200, int(silence_var.get())),
            "auto_stop": bool(auto_stop_var.get()),
            "max_seconds": int(max_var.get()),
            "hotkey_mode": "hold" if mode_var.get() == "удерживать" else "toggle",
            "autopaste": bool(autopaste_var.get()),
            "start_minimized": bool(minimized_var.get()),
            "replacements": _text_to_replacements(replacements.get("1.0", "end-1c")),
        }
        window.destroy()

    ctk.CTkButton(footer, text="Сохранить", width=140, height=36, corner_radius=10,
                  fg_color=ACCENT, hover_color=ACCENT_HOVER,
                  font=ctk.CTkFont("Segoe UI", 13, "bold"),
                  command=on_save).pack(side="right")
    ctk.CTkButton(footer, text="Отмена", width=110, height=36, corner_radius=10,
                  fg_color="transparent", border_width=1, border_color=BORDER,
                  text_color=TEXT, hover_color=CARD,
                  font=ctk.CTkFont("Segoe UI", 13),
                  command=window.destroy).pack(side="right", padx=(0, 8))

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

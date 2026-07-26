from __future__ import annotations

import ctypes
import threading
import time
import tkinter as tk
from tkinter import ttk

from trainer_features import FEATURES, FeatureKind
from trainer_runtime import LiveTrainerSession


GROUP_NAMES = {
    "player_combat": "玩家与战斗",
    "world": "捕获、掉落与世界",
    "inventory": "背包与物品",
    "building": "制作与建造",
    "time": "时间",
    "pal": "帕鲁",
    "weapons": "武器",
    "progression": "经验与点数",
    "movement": "速度与移动",
    "player_attributes": "玩家属性上限",
}


VK = {
    **{f"NUM {number}": 0x60 + number for number in range(10)},
    "NUM *": 0x6A,
    "NUM +": 0x6B,
    "NUM -": 0x6D,
    "NUM .": 0x6E,
    "NUM /": 0x6F,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    **{f"F{number}": 0x6F + number for number in range(1, 13)},
}
MODIFIERS = {"CTRL": 0x11, "ALT": 0x12, "SHIFT": 0x10}


class AsyncHotkeyPoller:
    def __init__(self, owner, callback):
        self.owner = owner
        self.callback = callback
        self.enabled = True
        self._pressed: set[str] = set()
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

    def start(self):
        self.owner.after(60, self._poll)

    def _is_down(self, vk: int) -> bool:
        return bool(self._user32.GetAsyncKeyState(vk) & 0x8000)

    def _matches(self, expression: str) -> bool:
        remainder = expression.strip().upper()
        required = set()
        changed = True
        while changed:
            changed = False
            for modifier in MODIFIERS:
                prefix = f"{modifier}+"
                if remainder.startswith(prefix):
                    required.add(modifier)
                    remainder = remainder[len(prefix) :].strip()
                    changed = True
                    break
        key_name = remainder
        if key_name not in VK or not self._is_down(VK[key_name]):
            return False
        return all(self._is_down(MODIFIERS[name]) for name in required)

    def _poll(self):
        try:
            disable_combo = (
                self._is_down(MODIFIERS["CTRL"])
                and self._is_down(MODIFIERS["SHIFT"])
                and self._is_down(0x24)
            )
            if disable_combo and "__master__" not in self._pressed:
                self.enabled = not self.enabled
                self.callback(None)
                self._pressed.add("__master__")
            elif not disable_combo:
                self._pressed.discard("__master__")
            if self.enabled:
                for feature in FEATURES:
                    down = self._matches(feature.hotkey)
                    if down and feature.feature_id not in self._pressed:
                        self._pressed.add(feature.feature_id)
                        self.callback(feature.feature_id)
                    elif not down:
                        self._pressed.discard(feature.feature_id)
        finally:
            if self.owner.winfo_exists():
                self.owner.after(60, self._poll)


class TrainerPanel(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.session = LiveTrainerSession()
        self.status_var = tk.StringVar(value="未连接：请先进入本地单人存档")
        self.hotkey_var = tk.StringVar(value="全局快捷键：已启用")
        self.feature_vars = {
            feature.feature_id: tk.BooleanVar(value=False)
            for feature in FEATURES
        }
        self.value_vars: dict[str, tk.Variable] = {}
        self.controls: list[tk.Widget] = []
        self.feature_controls: dict[str, list[tk.Widget]] = {}
        self.value_controls: dict[str, ttk.Spinbox] = {}
        self.connecting = False
        self.was_connected = False
        self._closed = False
        self._build()
        self.hotkeys = AsyncHotkeyPoller(self, self._hotkey_triggered)
        self.hotkeys.start()
        self.after(400, self._refresh_status)

    def _build(self):
        header = ttk.Frame(self, padding=(18, 13, 18, 9))
        header.pack(fill="x")
        ttk.Label(
            header,
            text="实时修改器",
            font=("Microsoft YaHei UI", 19, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Steam 1.0 · 本地单人实时功能",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.connect_button = ttk.Button(
            header,
            text="连接当前单人世界",
            command=self._connect,
        )
        self.connect_button.grid(row=0, column=1, rowspan=2, padx=(12, 0))
        self.disconnect_button = ttk.Button(
            header,
            text="断开并恢复",
            command=self._disconnect,
            state="disabled",
        )
        self.disconnect_button.grid(row=0, column=2, rowspan=2, padx=(8, 0))
        header.columnconfigure(0, weight=1)

        notice = ttk.Frame(self, padding=(18, 0, 18, 8))
        notice.pack(fill="x")
        ttk.Label(
            notice,
            text="修改数值前请先关闭对应开关；退出游戏前请先断开并恢复。",
            foreground="#8A4B08",
        ).pack(side="left")
        ttk.Label(notice, textvariable=self.hotkey_var).pack(side="right")

        host = ttk.Frame(self)
        host.pack(fill="both", expand=True, padx=18)
        self.canvas = tk.Canvas(host, highlightthickness=0, borderwidth=0)
        vertical = ttk.Scrollbar(host, orient="vertical", command=self.canvas.yview)
        horizontal = ttk.Scrollbar(host, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(self.canvas, padding=(0, 0, 8, 12))
        window = self.canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            ),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(
                window,
                width=max(event.width, 1120),
            ),
        )
        self.canvas.bind_all("<MouseWheel>", self._mousewheel, add="+")

        left = ttk.Frame(content)
        right = ttk.Frame(content)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        content.columnconfigure(0, weight=1, uniform="trainer")
        content.columnconfigure(1, weight=1, uniform="trainer")

        first_groups = {
            "player_combat",
            "world",
            "inventory",
            "building",
            "time",
        }
        groups = []
        for feature in FEATURES:
            if feature.group not in groups:
                groups.append(feature.group)
        for group in groups:
            parent = left if group in first_groups else right
            box = ttk.Labelframe(
                parent,
                text=GROUP_NAMES.get(group, group),
                padding=(9, 7),
            )
            box.pack(fill="x", pady=(0, 9))
            row = 0
            for feature in (item for item in FEATURES if item.group == group):
                self._feature_row(box, row, feature)
                row += 1
            box.columnconfigure(1, weight=1)

        footer = ttk.Frame(self, padding=(18, 8, 18, 12))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var).pack(
            side="left",
            fill="x",
            expand=True,
        )
        ttk.Button(
            footer,
            text="全部关闭并恢复",
            command=self._disable_all,
        ).pack(side="right")

    def _feature_row(self, parent, row, feature):
        ttk.Label(parent, text=feature.hotkey, width=14).grid(
            row=row,
            column=0,
            sticky="w",
            padx=(0, 6),
            pady=3,
        )
        control = ttk.Checkbutton(
            parent,
            text=feature.label,
            variable=self.feature_vars[feature.feature_id],
            command=lambda item=feature: self._apply_feature(item),
            state="disabled",
        )
        control.grid(row=row, column=1, sticky="w", pady=3)
        self.controls.append(control)
        self.feature_controls.setdefault(feature.feature_id, []).append(control)
        if feature.has_value:
            variable_type = tk.IntVar if feature.kind is FeatureKind.INTEGER else tk.DoubleVar
            variable = variable_type(value=feature.default)
            self.value_vars[feature.feature_id] = variable
            spin = ttk.Spinbox(
                parent,
                from_=feature.minimum,
                to=feature.maximum,
                increment=feature.increment,
                textvariable=variable,
                width=12,
                command=lambda item=feature: self._value_changed(item),
                state="disabled",
            )
            spin.grid(row=row, column=2, sticky="e", padx=(8, 0), pady=3)
            spin.bind(
                "<Return>",
                lambda _event, item=feature: self._value_changed(item),
            )
            spin.bind(
                "<FocusOut>",
                lambda _event, item=feature: self._value_changed(item),
            )
            self.controls.append(spin)
            self.feature_controls[feature.feature_id].append(spin)
            self.value_controls[feature.feature_id] = spin

    def _mousewheel(self, event):
        if self.canvas.winfo_containing(
            self.winfo_pointerx(),
            self.winfo_pointery(),
        ):
            self.canvas.yview_scroll((-1 if event.delta > 0 else 1) * 3, "units")

    def _set_controls(self, enabled: bool):
        available = self.session.available_feature_ids() if enabled else set()
        for feature_id, controls in self.feature_controls.items():
            state = "normal" if feature_id in available else "disabled"
            for control in controls:
                try:
                    control.configure(state=state)
                except tk.TclError:
                    pass

    def _connect(self):
        if self.connecting or self.session.connected:
            return
        self.connecting = True
        self.connect_button.configure(state="disabled")
        self.status_var.set("正在连接并动态解析当前 1.0 游戏对象，请稍候…")

        def worker():
            error = None
            try:
                self.session.connect()
            except Exception as exc:
                error = exc
            if not self._closed:
                self.after(0, lambda: self._connected(error))

        threading.Thread(target=worker, daemon=True).start()

    def _connected(self, error):
        self.connecting = False
        if error is not None:
            self.connect_button.configure(state="normal")
            self.status_var.set(f"连接失败：{error}")
            return
        self.was_connected = True
        self.disconnect_button.configure(state="normal")
        self._set_controls(True)
        self._refresh_status()

    def _disconnect(self):
        self._set_controls(False)
        self.disconnect_button.configure(state="disabled")
        self.status_var.set("正在关闭全部功能并恢复原始代码/数值…")
        self.update_idletasks()
        self.session.disconnect()
        self.was_connected = False
        for variable in self.feature_vars.values():
            variable.set(False)
        self.connect_button.configure(state="normal")
        self.status_var.set("已断开；本次实时补丁和临时世界参数已恢复")

    def _apply_feature(self, feature):
        if not self.session.connected:
            self.feature_vars[feature.feature_id].set(False)
            return
        enabled = self.feature_vars[feature.feature_id].get()
        value = (
            self.value_vars[feature.feature_id].get()
            if feature.has_value
            else None
        )
        try:
            self.session.set_feature(feature.feature_id, enabled, value)
            spin = self.value_controls.get(feature.feature_id)
            if spin is not None:
                spin.configure(state="disabled" if enabled else "normal")
        except Exception as exc:
            self.feature_vars[feature.feature_id].set(False)
            spin = self.value_controls.get(feature.feature_id)
            if spin is not None:
                spin.configure(state="normal")
            self.status_var.set(f"未启用：{exc}")

    def _value_changed(self, feature):
        if self.feature_vars[feature.feature_id].get():
            self._apply_feature(feature)

    def _hotkey_triggered(self, feature_id):
        if feature_id is None:
            self.hotkey_var.set(
                f"全局快捷键：{'已启用' if self.hotkeys.enabled else '已暂停'}"
                "（Ctrl+Shift+Home 切换）"
            )
            return
        if not self.session.connected:
            return
        variable = self.feature_vars[feature_id]
        variable.set(not variable.get())
        feature = next(item for item in FEATURES if item.feature_id == feature_id)
        self._apply_feature(feature)

    def _disable_all(self):
        if self.session.connected:
            self.session.disable_all()
        for variable in self.feature_vars.values():
            variable.set(False)
        for feature_id, spin in self.value_controls.items():
            if feature_id in self.session.available_feature_ids():
                spin.configure(state="normal")

    def _refresh_status(self):
        if self._closed:
            return
        if self.session.connected:
            status = self.session.status()
            age = (
                time.time() - float(status["last_tick"])
                if status["last_tick"]
                else 999.0
            )
            detail = (
                f"已连接 · 全部伙伴 {status['pals']} · "
                f"可用功能 {status['available']}/48"
            )
            if status["last_error"]:
                detail += f" · 最近错误：{status['last_error']}"
            elif age > 0.5:
                detail += " · 写入循环响应偏慢"
            self.status_var.set(detail)
        elif self.was_connected:
            self.was_connected = False
            self._set_controls(False)
            for variable in self.feature_vars.values():
                variable.set(False)
            self.disconnect_button.configure(state="disabled")
            self.connect_button.configure(state="normal")
            self.status_var.set(
                self.session.last_error
                or "连接已结束；进入本地世界后可重新连接"
            )
        self.after(400, self._refresh_status)

    def shutdown(self):
        self._closed = True
        self.hotkeys.enabled = False
        try:
            self.session.disconnect()
        except Exception:
            pass


RuntimePanel = TrainerPanel

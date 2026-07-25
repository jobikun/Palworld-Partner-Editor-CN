from __future__ import annotations

import logging
import queue
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from backend import (
    PASSIVE_CHOICES,
    PASSIVE_DATABASE,
    PASSIVE_EMPTY_DISPLAY,
    TOP_PASSIVE_PRESETS,
    EditorError,
    SUITS,
    SaveSession,
    find_world_saves,
    palworld_running,
    passive_display,
)
from trainer_window import RuntimeWindow


APP_NAME = "帕鲁伙伴编辑器"
APP_VERSION = "1.6.1"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1280x980")
        self.minsize(1120, 860)
        try:
            self.iconbitmap(self.resource_path("app.ico"))
        except tk.TclError:
            pass

        self.session: SaveSession | None = None
        self.visible_pals = []
        self.current_pal = None
        self.worker_messages: queue.Queue = queue.Queue()
        self.search_var = tk.StringVar()
        self.player_var = tk.StringVar()
        self.path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="正在查找 Steam 存档…")
        self.info_var = tk.StringVar(value="尚未加载伙伴")
        self.stat_var = tk.StringVar(value="面板预览：—")
        self.advanced_var = tk.BooleanVar(value=False)
        self.vars = {
            "hp_iv": tk.IntVar(value=0),
            "melee_iv": tk.IntVar(value=0),
            "ranged_iv": tk.IntVar(value=0),
            "defense_iv": tk.IntVar(value=0),
            "hp_soul": tk.IntVar(value=0),
            "attack_soul": tk.IntVar(value=0),
            "defense_soul": tk.IntVar(value=0),
            "craft_soul": tk.IntVar(value=0),
            "stars": tk.IntVar(value=0),
            "condenser": tk.IntVar(value=0),
        }
        self.stat_labels: dict[str, ttk.Label] = {}
        self.stat_spinboxes: dict[str, ttk.Spinbox] = {}
        self.suit_vars = {key: tk.IntVar(value=0) for key, _ in SUITS}
        self.suit_base_labels: dict[str, ttk.Label] = {}
        self.suit_frames: dict[str, ttk.Frame] = {}
        self.suit_spinboxes: dict[str, ttk.Spinbox] = {}
        self.passive_vars = [tk.StringVar(value=PASSIVE_EMPTY_DISPLAY) for _ in range(4)]
        self.passive_combos: list[ttk.Combobox] = []
        self.passive_description_var = tk.StringVar(value="选择词条后在这里显示效果说明。")
        self.passive_preset_var = tk.StringVar(value="神仙全能")
        self.runtime_window = None

        self._configure_style()
        self._build_menu()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._poll_worker)
        self.after(250, self.open_latest)

    @staticmethod
    def resource_path(name: str) -> str:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        return str(base / name)

    def _configure_style(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 19, "bold"))
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 6))
        style.configure("Treeview", font=("Microsoft YaHei UI", 10), rowheight=27)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 11, "bold"))

    def _build_menu(self):
        menu_bar = tk.Menu(self)
        tools = tk.Menu(menu_bar, tearoff=False)
        tools.add_command(label="独立实时修改器（48 项）…", command=self.open_runtime_window)
        tools.add_separator()
        tools.add_command(label="所有帕鲁应用所选神仙词条", command=self.apply_preset_to_all)
        tools.add_command(label="所有帕鲁应用当前四词条", command=self.apply_current_passives_to_all)
        tools.add_separator()
        tools.add_command(label="实验生成一只枯星龙…", command=self.add_experimental_world_tree_dragon)
        menu_bar.add_cascade(label="工具", menu=tools)
        self.configure(menu=menu_bar)

    def open_runtime_window(self):
        if self.runtime_window and self.runtime_window.winfo_exists():
            self.runtime_window.lift()
            self.runtime_window.focus_force()
            return
        self.runtime_window = RuntimeWindow(self)

    def _build_ui(self):
        header = ttk.Frame(self, padding=(18, 14, 18, 8))
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Steam 1.0 · 编辑伙伴属性、工作与被动词条 · 保存前自动备份",
            style="Sub.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        header.columnconfigure(0, weight=1)
        self.running_label = ttk.Label(header, text="")
        self.running_label.grid(row=0, column=1, rowspan=2, sticky="e")
        self._update_running_label()

        filebar = ttk.Frame(self, padding=(18, 4, 18, 10))
        filebar.pack(fill="x")
        ttk.Entry(filebar, textvariable=self.path_var, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(filebar, text="最近存档", command=self.open_latest).pack(side="left", padx=(8, 0))
        ttk.Button(filebar, text="选择 Level.sav", command=self.choose_save).pack(side="left", padx=(8, 0))
        ttk.Button(filebar, text="重新加载", command=self.reload).pack(side="left", padx=(8, 0))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 10))

        left = ttk.Frame(body, padding=(0, 0, 10, 0))
        right_shell = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(left, weight=4)
        body.add(right_shell, weight=7)

        player_row = ttk.Frame(left)
        player_row.pack(fill="x", pady=(0, 8))
        ttk.Label(player_row, text="玩家：").pack(side="left")
        self.player_combo = ttk.Combobox(player_row, textvariable=self.player_var, state="readonly", width=24)
        self.player_combo.pack(side="left", fill="x", expand=True)
        self.player_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_pal_list())

        search = ttk.Entry(left, textvariable=self.search_var)
        search.pack(fill="x", pady=(0, 8))
        self.search_var.trace_add("write", lambda *_: self.refresh_pal_list())

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=("level", "stars"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="伙伴")
        self.tree.heading("level", text="等级")
        self.tree.heading("stars", text="星级")
        self.tree.column("#0", width=220, minwidth=150, stretch=True)
        self.tree.column("level", width=52, minwidth=52, anchor="center", stretch=False)
        self.tree.column("stars", width=90, minwidth=90, anchor="center", stretch=False)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_pal_select)

        self.editor_canvas = tk.Canvas(
            right_shell,
            background=self.cget("background"),
            highlightthickness=0,
            borderwidth=0,
        )
        editor_scrollbar = ttk.Scrollbar(right_shell, orient="vertical", command=self.editor_canvas.yview)
        self.editor_canvas.configure(yscrollcommand=editor_scrollbar.set)
        editor_scrollbar.pack(side="right", fill="y")
        self.editor_canvas.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(self.editor_canvas, padding=(0, 0, 8, 8))
        editor_window = self.editor_canvas.create_window((0, 0), window=right, anchor="nw")
        right.bind(
            "<Configure>",
            lambda _event: self.editor_canvas.configure(scrollregion=self.editor_canvas.bbox("all")),
        )
        self.editor_canvas.bind(
            "<Configure>",
            lambda event: self.editor_canvas.itemconfigure(editor_window, width=event.width),
        )
        self.bind_all("<MouseWheel>", self._on_editor_mousewheel, add="+")

        ttk.Label(right, textvariable=self.info_var, font=("Microsoft YaHei UI", 14, "bold")).pack(fill="x")
        ttk.Label(right, textvariable=self.stat_var).pack(fill="x", pady=(3, 10))

        stats = ttk.Labelframe(right, text="生命、攻击、防御与工作速度", padding=10)
        stats.pack(fill="x", pady=(0, 10))
        advanced_row = ttk.Frame(stats)
        advanced_row.grid(row=0, column=0, columnspan=3, sticky="ew", padx=6, pady=(0, 5))
        ttk.Checkbutton(
            advanced_row,
            text="启用超限模式（IV/灵魂强化最高 255，浓缩等级最高 254）",
            variable=self.advanced_var,
            command=self._toggle_advanced,
        ).pack(side="left")
        ttk.Label(advanced_row, text="超限值可能破坏平衡，请保留自动备份", foreground="#a33a14").pack(side="right")
        stat_fields = [
            ("生命 IV", "hp_iv", 0, 100),
            ("近战攻击 IV", "melee_iv", 0, 100),
            ("远程攻击 IV", "ranged_iv", 0, 100),
            ("防御 IV", "defense_iv", 0, 100),
            ("生命灵魂强化", "hp_soul", 0, 10),
            ("攻击灵魂强化", "attack_soul", 0, 10),
            ("防御灵魂强化", "defense_soul", 0, 10),
            ("工作速度灵魂强化", "craft_soul", 0, 10),
            ("星级", "stars", 0, 4),
            ("超限浓缩等级", "condenser", 0, 254),
        ]
        for index, (label, key, low, high) in enumerate(stat_fields):
            row, col = divmod(index, 3)
            box = ttk.Frame(stats)
            box.grid(row=row + 1, column=col, sticky="ew", padx=6, pady=5)
            field_label = ttk.Label(box, text=f"{label}（{low}～{high}）")
            field_label.pack(anchor="w")
            spinbox = ttk.Spinbox(box, from_=low, to=high, textvariable=self.vars[key], width=10)
            spinbox.pack(fill="x")
            self.stat_labels[key] = field_label
            self.stat_spinboxes[key] = spinbox
        for col in range(3):
            stats.columnconfigure(col, weight=1)

        passives = ttk.Labelframe(right, text="当前伙伴的被动词条（独立编辑，最多 4 个）", padding=10)
        passives.pack(fill="x", pady=(0, 10))
        ttk.Label(
            passives,
            text="可输入中文名或内部 ID 搜索；同一只伙伴不能设置重复词条。",
            foreground="#555555",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=4, pady=(0, 6))
        for index, variable in enumerate(self.passive_vars):
            row, col = divmod(index, 2)
            box = ttk.Frame(passives)
            box.grid(row=row + 1, column=col * 2, columnspan=2, sticky="ew", padx=5, pady=3)
            ttk.Label(box, text=f"词条 {index + 1}", width=7).pack(side="left")
            combo = ttk.Combobox(box, textvariable=variable, values=PASSIVE_CHOICES, state="normal")
            combo.pack(side="left", fill="x", expand=True)
            combo.bind("<KeyRelease>", lambda event, item=combo: self._filter_passive_choices(event, item))
            combo.bind("<FocusIn>", lambda _event, item=combo: item.configure(values=PASSIVE_CHOICES))
            combo.bind("<<ComboboxSelected>>", lambda _event: self._update_passive_description())
            self.passive_combos.append(combo)
        preset_row = ttk.Frame(passives)
        preset_row.grid(row=3, column=0, columnspan=4, sticky="ew", padx=5, pady=(6, 2))
        ttk.Label(preset_row, text="顶级预设：").pack(side="left")
        self.passive_preset_combo = ttk.Combobox(
            preset_row,
            textvariable=self.passive_preset_var,
            values=list(TOP_PASSIVE_PRESETS),
            state="readonly",
            width=16,
        )
        self.passive_preset_combo.pack(side="left")
        ttk.Button(preset_row, text="一键应用到当前词条栏", command=self.apply_passive_preset).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(preset_row, text="清空当前词条栏", command=self.clear_passives).pack(side="left", padx=(8, 0))
        ttk.Label(
            passives,
            textvariable=self.passive_description_var,
            foreground="#444444",
            wraplength=700,
        ).grid(row=4, column=0, columnspan=4, sticky="w", padx=5, pady=(4, 0))
        for col in range(4):
            passives.columnconfigure(col, weight=1)

        suits = ttk.Labelframe(right, text="该物种拥有的工作适应性（目标等级 1～10）", padding=10)
        self.suits_frame = suits
        suits.pack(fill="both", expand=True, pady=(0, 10))
        ttk.Label(
            suits,
            text="只显示该物种实际拥有的工作类型；填写最终目标等级，程序会换算存档中的个体加成。",
            foreground="#555555",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=4, pady=(0, 7))
        self.no_suits_label = ttk.Label(suits, text="该伙伴没有可编辑的工作适应性。", foreground="#666666")
        for index, (key, label) in enumerate(SUITS):
            row, col = divmod(index, 4)
            box = ttk.Frame(suits)
            box.grid(row=row + 1, column=col, sticky="ew", padx=5, pady=4)
            self.suit_frames[key] = box
            title = ttk.Frame(box)
            title.pack(fill="x")
            ttk.Label(title, text=label).pack(side="left")
            base = ttk.Label(title, text="基础 0", foreground="#666666")
            base.pack(side="right")
            self.suit_base_labels[key] = base
            spinbox = ttk.Spinbox(box, from_=1, to=10, textvariable=self.suit_vars[key], width=8)
            spinbox.pack(fill="x")
            self.suit_spinboxes[key] = spinbox
        for col in range(4):
            suits.columnconfigure(col, weight=1)

        presets = ttk.Frame(right)
        presets.pack(fill="x", pady=(0, 7))
        ttk.Button(presets, text="当前伙伴满级（Lv.80）", command=self.max_current_level).pack(side="left")
        ttk.Button(presets, text="当前伙伴正常上限", command=self.max_combat).pack(side="left")
        ttk.Button(presets, text="当前伙伴超限最大（255）", command=self.max_overcap).pack(side="left", padx=(8, 0))
        ttk.Button(presets, text="当前伙伴已有工作设为 10", command=self.max_work).pack(side="left", padx=(8, 0))

        collection_actions = ttk.Frame(right)
        collection_actions.pack(fill="x", pady=(0, 7))
        ttk.Button(
            collection_actions,
            text="一键补齐尚未拥有的全部可获得帕鲁（每种一只）",
            command=self.add_all_missing,
        ).pack(side="left")

        actions = ttk.Frame(right)
        actions.pack(fill="x")
        ttk.Button(actions, text="一键：当前玩家全部帕鲁战斗/工作拉满", command=self.apply_all_max).pack(side="left")
        ttk.Button(actions, text="所有帕鲁满级", command=self.max_all_levels).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="应用到当前伙伴", command=self.apply_current).pack(side="right")
        self.save_button = ttk.Button(actions, text="保存到存档", command=self.save_to_disk)
        self.save_button.pack(side="right", padx=(0, 8))
        self._toggle_advanced(sync_values=False)

        footer = ttk.Frame(self, padding=(18, 8, 18, 12))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        ttk.Label(footer, text="请先退出游戏再保存").pack(side="right")

    def _on_editor_mousewheel(self, event):
        canvas = getattr(self, "editor_canvas", None)
        if not canvas or not canvas.winfo_exists():
            return
        pointer_x, pointer_y = self.winfo_pointerxy()
        left = canvas.winfo_rootx()
        top = canvas.winfo_rooty()
        if left <= pointer_x <= left + canvas.winfo_width() and top <= pointer_y <= top + canvas.winfo_height():
            canvas.yview_scroll((-1 if event.delta > 0 else 1) * 3, "units")
            return "break"

    def _update_running_label(self):
        running = palworld_running()
        self.running_label.configure(
            text="● 游戏正在运行：禁止保存" if running else "● 游戏未运行：可以保存",
            foreground="#b42318" if running else "#17803d",
        )
        self.after(3000, self._update_running_label)

    def _run_worker(self, label, action, callback):
        self.status_var.set(label)
        self.configure(cursor="wait")

        def work():
            try:
                self.worker_messages.put((callback, action(), None))
            except Exception as exc:
                self.worker_messages.put((callback, None, (exc, traceback.format_exc())))

        threading.Thread(target=work, daemon=True).start()

    def _poll_worker(self):
        try:
            while True:
                callback, result, error = self.worker_messages.get_nowait()
                self.configure(cursor="")
                if error:
                    exc, details = error
                    logging.getLogger("pal_partner_editor").error(details)
                    self.status_var.set(f"失败：{exc}")
                else:
                    callback(result)
        except queue.Empty:
            pass
        self.after(100, self._poll_worker)

    def open_latest(self):
        saves = find_world_saves()
        if not saves:
            self.status_var.set("没有找到 Steam 的 Level.sav")
            return
        self.open_path(saves[0])

    def choose_save(self):
        saves = find_world_saves()
        initial = str(saves[0].parent if saves else Path.home())
        selected = filedialog.askopenfilename(
            title="选择 Palworld 的 Level.sav",
            initialdir=initial,
            filetypes=[("Palworld 世界存档", "Level.sav"), ("SAV 文件", "*.sav")],
        )
        if selected:
            self.open_path(Path(selected))

    def reload(self):
        if self.session:
            if self.session.dirty and not messagebox.askyesno(APP_NAME, "重新加载会丢弃尚未保存的修改，继续吗？"):
                return
            self.open_path(self.session.level_path)
        else:
            self.open_latest()

    def open_path(self, path: Path):
        if self.session and self.session.dirty:
            if not messagebox.askyesno(APP_NAME, "打开其他存档会丢弃尚未保存的修改，继续吗？"):
                return
        self.path_var.set(str(path))
        self._run_worker("正在读取并解析存档…", lambda: SaveSession(path), self._loaded)

    def _loaded(self, session: SaveSession):
        self.session = session
        self.current_pal = None
        self.player_combo["values"] = [p.name for p in session.players]
        self.player_combo.current(0)
        self.refresh_pal_list()
        self.status_var.set(
            f"已加载 {len(session.pals_for_player(0))} 只伙伴；跳过 {session.unknown_count} 个未知条目。"
        )

    def refresh_pal_list(self):
        if not self.session:
            return
        index = max(self.player_combo.current(), 0)
        query = self.search_var.get().strip().casefold()
        pals = self.session.pals_for_player(index)
        self.visible_pals = [p for p in pals if not query or query in p.display_name.casefold() or query in p.code_name.casefold()]
        self.tree.delete(*self.tree.get_children())
        for i, pal in enumerate(self.visible_pals):
            snap = self.session.snapshot(pal)
            stars = self._star_text(snap)
            self.tree.insert("", "end", iid=str(i), text=snap["name"], values=(snap["level"], stars))
        if self.visible_pals:
            self.tree.selection_set("0")
            self.tree.focus("0")
            self.on_pal_select()
        else:
            self.current_pal = None
            self.info_var.set("没有匹配的伙伴")

    def on_pal_select(self, _event=None):
        selection = self.tree.selection()
        if not selection or not self.session:
            return
        self.current_pal = self.visible_pals[int(selection[0])]
        self._display_snapshot(self.session.snapshot(self.current_pal))

    def _display_snapshot(self, snap):
        warning_parts = []
        if snap["rank_warning"] is not None:
            warning_parts.append(f"Rank={snap['rank_warning']}")
        warning_parts.extend(f"{key}={value}" for key, value in snap.get("field_warnings", {}).items())
        rank_text = self._star_text(snap)
        if snap["overcap"]:
            details = [f"浓缩={snap['condenser']}"]
            details.extend(f"{key}={value}" for key, value in snap["overcap_fields"].items())
            rank_text += f"  ·  超限属性（{', '.join(details)}）"
        if warning_parts:
            rank_text += f"  ·  检测到异常原始值（{', '.join(warning_parts)}），应用后会按界面范围修正"
        self.info_var.set(f"{snap['name']}  ·  Lv.{snap['level']}  ·  {rank_text}")
        stats = snap["stats"]
        self.stat_var.set(
            f"基础预览（不含被动/伙伴/信赖）：生命 {stats['HP']}　近战 {stats['PHY']}　远程 {stats['MAG']}　"
            f"防御 {stats['DEF']}　工作速度 {stats['WORK']}"
        )
        for key in self.vars:
            self.vars[key].set(snap[key])
        passives = list(snap.get("passives", []))
        for index, variable in enumerate(self.passive_vars):
            variable.set(passive_display(passives[index]) if index < len(passives) else PASSIVE_EMPTY_DISPLAY)
        self._update_passive_description()
        self.advanced_var.set(bool(snap["overcap"]))
        self._toggle_advanced(sync_values=False)
        supported = [(key, values) for key, values in snap["suits"].items() if values["supported"]]
        for frame in self.suit_frames.values():
            frame.grid_remove()
        self.no_suits_label.grid_remove()
        if not supported:
            self.no_suits_label.grid(row=1, column=0, columnspan=4, sticky="w", padx=5, pady=8)
        for index, (key, values) in enumerate(supported):
            row, col = divmod(index, 4)
            self.suit_frames[key].grid(row=row + 1, column=col, sticky="ew", padx=5, pady=4)
            self.suit_vars[key].set(values["total"])
            self.suit_base_labels[key].configure(text=f"基础 {values['base']}")
            self.suit_spinboxes[key].configure(from_=values["base"], to=10)

    def _collect_values(self):
        return {
            **{key: var.get() for key, var in self.vars.items()},
            "advanced": self.advanced_var.get(),
            "passives": self._collect_passives(),
            "suits": {key: var.get() for key, var in self.suit_vars.items()},
        }

    def _collect_passives(self):
        result = []
        name_matches: dict[str, list[str]] = {}
        for code, data in PASSIVE_DATABASE.items():
            name_matches.setdefault(data["name"].casefold(), []).append(code)
        for variable in self.passive_vars:
            text = variable.get().strip()
            if not text or text == PASSIVE_EMPTY_DISPLAY:
                continue
            code = ""
            if text.endswith("]") and "[" in text:
                code = text.rsplit("[", 1)[1][:-1].strip()
            elif text in PASSIVE_DATABASE:
                code = text
            else:
                matches = name_matches.get(text.casefold(), [])
                if len(matches) == 1:
                    code = matches[0]
            if not code:
                raise EditorError(f"无法识别词条“{text}”，请从下拉搜索结果中选择")
            result.append(code)
        if len(result) != len(set(result)):
            raise EditorError("同一只帕鲁不能设置重复词条")
        return result

    def _filter_passive_choices(self, event, combo):
        if event.keysym in {"Up", "Down", "Left", "Right", "Return", "Escape", "Tab"}:
            return
        query = combo.get().strip().casefold()
        if not query or query == PASSIVE_EMPTY_DISPLAY.casefold():
            choices = PASSIVE_CHOICES
        else:
            choices = [
                display
                for display in PASSIVE_CHOICES
                if query in display.casefold()
                or (
                    display.endswith("]")
                    and display != PASSIVE_EMPTY_DISPLAY
                    and query
                    in PASSIVE_DATABASE.get(display.rsplit("[", 1)[1][:-1], {}).get("description", "").casefold()
                )
            ]
        combo.configure(values=choices)
        if choices:
            combo.event_generate("<Down>")

    def _update_passive_description(self):
        details = []
        for index, variable in enumerate(self.passive_vars, start=1):
            text = variable.get().strip()
            if not text or text == PASSIVE_EMPTY_DISPLAY or "[" not in text:
                continue
            code = text.rsplit("[", 1)[1][:-1]
            data = PASSIVE_DATABASE.get(code)
            if data:
                details.append(f"{index}. {data['name']}：{data['description'] or '游戏未提供说明'}")
        self.passive_description_var.set("　".join(details) if details else "选择词条后在这里显示效果说明。")

    def apply_passive_preset(self):
        codes = TOP_PASSIVE_PRESETS[self.passive_preset_var.get()]
        for variable, code in zip(self.passive_vars, codes):
            variable.set(passive_display(code))
        self._update_passive_description()
        self.status_var.set("已填入当前伙伴的顶级词条预设；点击“应用到当前伙伴”后再保存。")

    def clear_passives(self):
        for variable in self.passive_vars:
            variable.set(PASSIVE_EMPTY_DISPLAY)
        self._update_passive_description()

    def apply_preset_to_all(self):
        preset_name = self.passive_preset_var.get()
        self._confirm_passives_for_all(list(TOP_PASSIVE_PRESETS[preset_name]), preset_name)

    def apply_current_passives_to_all(self):
        try:
            passives = self._collect_passives()
        except EditorError as exc:
            self.status_var.set(str(exc))
            return
        names = "、".join(PASSIVE_DATABASE.get(code, {}).get("name", code) for code in passives) or "清空全部词条"
        self._confirm_passives_for_all(passives, names)

    def _confirm_passives_for_all(self, passives, label):
        if not self.session:
            self.status_var.set("请先加载存档")
            return
        index = max(self.player_combo.current(), 0)
        pals = self.session.pals_for_player(index)
        if not pals:
            self.status_var.set("当前玩家没有可编辑的伙伴")
            return
        self._run_worker(
            "正在批量写入所有伙伴的神仙词条…",
            lambda: (self.session.apply_passives_all(index, passives), label),
            self._all_passives_applied,
        )

    def _all_passives_applied(self, result):
        count, label = result
        self.refresh_pal_list()
        self.status_var.set(f"已将 {count} 只伙伴套用“{label}”；还需要保存到存档。")

    @staticmethod
    def _star_text(snap):
        if snap["rank_warning"] is not None:
            return "异常"
        return f"超限 {snap['condenser']}" if snap["condenser"] > 4 else str(snap["stars"])

    def _toggle_advanced(self, sync_values=True):
        advanced = self.advanced_var.get()
        iv_keys = ("hp_iv", "melee_iv", "ranged_iv", "defense_iv")
        soul_keys = ("hp_soul", "attack_soul", "defense_soul", "craft_soul")
        for key in iv_keys:
            maximum = 255 if advanced else 100
            self.stat_spinboxes[key].configure(from_=0, to=maximum)
            self.stat_labels[key].configure(text=f"{self.stat_labels[key].cget('text').split('（')[0]}（0～{maximum}）")
        for key in soul_keys:
            maximum = 255 if advanced else 10
            self.stat_spinboxes[key].configure(from_=0, to=maximum)
            self.stat_labels[key].configure(text=f"{self.stat_labels[key].cget('text').split('（')[0]}（0～{maximum}）")
        self.stat_spinboxes["stars"].configure(state="disabled" if advanced else "normal")
        self.stat_spinboxes["condenser"].configure(state="normal" if advanced else "disabled")
        if not sync_values:
            return
        if advanced:
            self.vars["condenser"].set(max(self.vars["condenser"].get(), self.vars["stars"].get()))
        else:
            for key in iv_keys:
                self.vars[key].set(min(self.vars[key].get(), 100))
            for key in soul_keys:
                self.vars[key].set(min(self.vars[key].get(), 10))
            self.vars["stars"].set(min(self.vars["condenser"].get(), 4))
            self.vars["condenser"].set(self.vars["stars"].get())

    def max_combat(self):
        self.advanced_var.set(False)
        self._toggle_advanced(sync_values=False)
        self.vars["hp_iv"].set(100)
        self.vars["melee_iv"].set(100)
        self.vars["ranged_iv"].set(100)
        self.vars["defense_iv"].set(100)
        self.vars["hp_soul"].set(10)
        self.vars["attack_soul"].set(10)
        self.vars["defense_soul"].set(10)
        self.vars["craft_soul"].set(10)
        self.vars["stars"].set(4)
        self.vars["condenser"].set(4)

    def max_overcap(self):
        self.advanced_var.set(True)
        self._toggle_advanced(sync_values=False)
        for key in ("hp_iv", "melee_iv", "ranged_iv", "defense_iv"):
            self.vars[key].set(255)
        for key in ("hp_soul", "attack_soul", "defense_soul", "craft_soul"):
            self.vars[key].set(255)
        self.vars["stars"].set(4)
        self.vars["condenser"].set(254)

    def max_work(self):
        if not self.session or not self.current_pal:
            return
        snap = self.session.snapshot(self.current_pal)
        for key, values in snap["suits"].items():
            if values["supported"]:
                self.suit_vars[key].set(10)

    def max_current_level(self):
        if not self.session or not self.current_pal:
            self.status_var.set("请先选择伙伴")
            return
        snap = self.session.max_level(self.current_pal)
        self._display_snapshot(snap)
        current = self.tree.selection()
        if current:
            self.tree.set(current[0], "level", snap["level"])
        self.status_var.set(f"已将 {snap['name']} 设为 Lv.80；还需要点击“保存到存档”。")

    def max_all_levels(self):
        if not self.session:
            return
        index = max(self.player_combo.current(), 0)
        pals = self.session.pals_for_player(index)
        if not pals:
            self.status_var.set("当前玩家没有可编辑的伙伴")
            return
        self._run_worker(
            "正在将所有伙伴设为满级…",
            lambda: self.session.max_level_all(index),
            self._all_levels_maxed,
        )

    def _all_levels_maxed(self, count):
        self.refresh_pal_list()
        self.status_var.set(f"已在内存中将 {count} 只伙伴设为 Lv.80；还需要保存到存档。")

    def add_all_missing(self):
        if not self.session:
            return
        index = max(self.player_combo.current(), 0)
        missing = self.session.missing_obtainable_species(index)
        if not missing:
            self.status_var.set("当前玩家已经拥有全部可获得帕鲁种类")
            return
        self._run_worker(
            "正在补齐尚未拥有的帕鲁…",
            lambda: self.session.add_all_missing_obtainable(index),
            self._all_missing_added,
        )

    def _all_missing_added(self, names):
        self.refresh_pal_list()
        self.status_var.set(f"已在内存中新增 {len(names)} 种帕鲁；还需要保存到存档。")

    def add_experimental_world_tree_dragon(self):
        if not self.session:
            self.status_var.set("请先加载存档")
            return
        index = max(self.player_combo.current(), 0)
        self._run_worker(
            "正在生成实验枯星龙…",
            lambda: self.session.add_experimental_world_tree_dragon(index),
            self._experimental_pal_added,
        )

    def _experimental_pal_added(self, name):
        self.refresh_pal_list()
        self.status_var.set(f"已在内存中生成实验伙伴{name}；还需要保存到存档。")

    def apply_all_max(self):
        if not self.session:
            return
        index = max(self.player_combo.current(), 0)
        pals = self.session.pals_for_player(index)
        if not pals:
            self.status_var.set("当前玩家没有可编辑的伙伴")
            return
        self._run_worker(
            "正在批量应用全部伙伴的超限属性…",
            lambda: self.session.apply_all_max(index),
            self._all_max_applied,
        )

    def _all_max_applied(self, count):
        self.refresh_pal_list()
        self.status_var.set(f"已在内存中将 {count} 只伙伴的战斗和工作属性拉满；还需要保存到存档。")

    def apply_current(self):
        if not self.session or not self.current_pal:
            self.status_var.set("请先选择伙伴")
            return
        try:
            snap = self.session.apply(self.current_pal, self._collect_values())
            self._display_snapshot(snap)
            self.status_var.set(f"已在内存中应用到 {snap['name']}；还需要点击“保存到存档”。")
            current = self.tree.selection()
            if current:
                self.tree.set(current[0], "stars", self._star_text(snap))
        except EditorError as exc:
            self.status_var.set(str(exc))

    def save_to_disk(self):
        if not self.session:
            return
        if self.current_pal:
            try:
                self.session.apply(self.current_pal, self._collect_values())
            except EditorError as exc:
                self.status_var.set(str(exc))
                return
        if not self.session.dirty:
            self.status_var.set("没有已应用的修改")
            return
        self._run_worker("正在备份、写入并回读验证…", self.session.save, self._saved)

    def _saved(self, backup_dir: Path):
        self.status_var.set(f"保存成功；备份位于 {backup_dir}")

    def on_close(self):
        if self.session and self.session.dirty:
            if not messagebox.askyesno(APP_NAME, "还有未保存的修改，确定退出吗？"):
                return
        self.destroy()


def configure_logging():
    log_dir = Path.home() / "AppData" / "Local" / "PalPartnerEditor"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "editor.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )


if __name__ == "__main__":
    configure_logging()
    App().mainloop()

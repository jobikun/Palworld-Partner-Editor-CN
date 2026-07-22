from __future__ import annotations

import logging
import queue
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from backend import EditorError, SUITS, SaveSession, find_world_saves, palworld_running


APP_NAME = "帕鲁伙伴编辑器"
APP_VERSION = "1.3.0"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1220x900")
        self.minsize(1080, 800)
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

        self._configure_style()
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

    def _build_ui(self):
        header = ttk.Frame(self, padding=(18, 14, 18, 8))
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Steam 1.0 · 编辑伙伴个体数据 · 保存前自动备份",
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
        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(left, weight=4)
        body.add(right, weight=7)

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
        self.tree.column("#0", width=230, stretch=True)
        self.tree.column("level", width=55, anchor="center", stretch=False)
        self.tree.column("stars", width=55, anchor="center", stretch=False)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_pal_select)

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
        ttk.Button(actions, text="应用到当前伙伴", command=self.apply_current).pack(side="right")
        self.save_button = ttk.Button(actions, text="保存到存档", command=self.save_to_disk)
        self.save_button.pack(side="right", padx=(0, 8))
        self._toggle_advanced(sync_values=False)

        footer = ttk.Frame(self, padding=(18, 8, 18, 12))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        ttk.Label(footer, text="请先退出游戏再保存").pack(side="right")

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
                    messagebox.showerror(APP_NAME, str(exc))
                else:
                    callback(result)
        except queue.Empty:
            pass
        self.after(100, self._poll_worker)

    def open_latest(self):
        saves = find_world_saves()
        if not saves:
            messagebox.showerror(APP_NAME, "没有找到 Steam 的 Level.sav")
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
            "suits": {key: var.get() for key, var in self.suit_vars.items()},
        }

    @staticmethod
    def _star_text(snap):
        if snap["rank_warning"] is not None:
            return "异常"
        return "4+（超限）" if snap["condenser"] > 4 else str(snap["stars"])

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

    def add_all_missing(self):
        if not self.session:
            return
        index = max(self.player_combo.current(), 0)
        missing = self.session.missing_obtainable_species(index)
        if not missing:
            messagebox.showinfo(APP_NAME, "当前玩家已经拥有全部可获得帕鲁种类。")
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"将向当前玩家的帕鲁终端补入 {len(missing)} 种尚未拥有的帕鲁，每种 1 只。\n\n"
            "只补正常可获得的物种/亚种，不加入塔主、NPC、测试体或随行剧情别名。\n"
            "新增伙伴为 1 级、无被动和主动技能；修改仍需点击“保存到存档”才会写入。\n\n"
            "是否继续？",
        ):
            return
        self._run_worker(
            "正在补齐尚未拥有的帕鲁…",
            lambda: self.session.add_all_missing_obtainable(index),
            self._all_missing_added,
        )

    def _all_missing_added(self, names):
        self.refresh_pal_list()
        self.status_var.set(f"已在内存中新增 {len(names)} 种帕鲁；还需要保存到存档。")
        messagebox.showinfo(
            APP_NAME,
            f"已补入 {len(names)} 种尚未拥有的帕鲁。\n\n"
            "它们已放入当前玩家的帕鲁终端；请退出游戏后点击“保存到存档”。",
        )

    def apply_all_max(self):
        if not self.session:
            return
        index = max(self.player_combo.current(), 0)
        pals = self.session.pals_for_player(index)
        if not pals:
            messagebox.showinfo(APP_NAME, "当前玩家没有可编辑的伙伴")
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"将当前玩家的 {len(pals)} 只伙伴全部设为超限最大？\n\n"
            "IV、生命/攻防/工作速度强化设为 255，浓缩等级设为 254；"
            "每只伙伴已有的工作适应性设为 10。\n\n修改先保存在内存中，仍需点击“保存到存档”。",
        ):
            return
        self._run_worker(
            "正在批量应用全部伙伴的超限属性…",
            lambda: self.session.apply_all_max(index),
            self._all_max_applied,
        )

    def _all_max_applied(self, count):
        self.refresh_pal_list()
        self.status_var.set(f"已在内存中将 {count} 只伙伴的战斗和工作属性拉满；还需要保存到存档。")
        messagebox.showinfo(APP_NAME, f"已批量应用 {count} 只伙伴。\n\n请退出游戏后点击“保存到存档”。")

    def apply_current(self):
        if not self.session or not self.current_pal:
            messagebox.showwarning(APP_NAME, "请先选择伙伴")
            return
        try:
            snap = self.session.apply(self.current_pal, self._collect_values())
            self._display_snapshot(snap)
            self.status_var.set(f"已在内存中应用到 {snap['name']}；还需要点击“保存到存档”。")
            current = self.tree.selection()
            if current:
                self.tree.set(current[0], "stars", self._star_text(snap))
        except EditorError as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def save_to_disk(self):
        if not self.session:
            return
        if self.current_pal and messagebox.askyesno(APP_NAME, "是否先把当前界面的数值应用到所选伙伴，再保存？"):
            try:
                self.session.apply(self.current_pal, self._collect_values())
            except EditorError as exc:
                messagebox.showerror(APP_NAME, str(exc))
                return
        if not self.session.dirty:
            messagebox.showinfo(APP_NAME, "没有已应用的修改")
            return
        if not messagebox.askyesno(
            APP_NAME,
            "确认写入 Level.sav？\n\n程序会先在世界目录创建带时间戳的备份，并回读验证临时存档。",
        ):
            return
        self._run_worker("正在备份、写入并回读验证…", self.session.save, self._saved)

    def _saved(self, backup_dir: Path):
        self.status_var.set(f"保存成功；备份位于 {backup_dir}")
        messagebox.showinfo(APP_NAME, f"保存成功。\n\n备份目录：\n{backup_dir}")

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

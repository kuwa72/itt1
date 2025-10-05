import tkinter as tk
from tkinter import ttk, messagebox
from typing import List
from itunes_controller import iTunesController
from config import ConfigManager


class PlaylistPicker(tk.Toplevel):
    def __init__(self, master, all_names: List[str], preselected: List[str] | None = None):
        super().__init__(master)
        self.title("クイックスロット選択 (最大5件)")
        self.geometry("480x420")
        self.resizable(False, False)
        self.result = None

        label = ttk.Label(self, text="クイックスロット(1-5)に割り当てるプレイリストを選択してください (最大5件)")
        label.pack(padx=10, pady=(10, 6), anchor="w")

        # List container with vertical scrollbar
        list_container = ttk.Frame(self)
        list_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        self.listbox = tk.Listbox(list_container, selectmode=tk.MULTIPLE, exportselection=False)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        yscroll = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.listbox.yview)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=yscroll.set)

        first_selected_idx = None
        pre = set(preselected or [])
        for i, name in enumerate(all_names):
            self.listbox.insert(tk.END, name)
            if name in pre:
                self.listbox.selection_set(i)
                if first_selected_idx is None:
                    first_selected_idx = i

        # Ensure the first selected item is visible
        if first_selected_idx is not None:
            self.listbox.see(first_selected_idx)

        # Mouse wheel scrolling (Windows)
        def _on_mousewheel(event):
            delta = -int(event.delta / 120)
            self.listbox.yview_scroll(delta, "units")
            return "break"
        self.listbox.bind("<MouseWheel>", _on_mousewheel)

        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, padx=10, pady=(6, 10))
        ttk.Button(btns, text="OK", command=self.on_ok).pack(side=tk.RIGHT)
        ttk.Button(btns, text="キャンセル", command=self.on_cancel).pack(side=tk.RIGHT, padx=(0, 6))

        self.bind("<Escape>", lambda e: self.on_cancel())
        self.transient(master)
        self.grab_set()
        self.focus_set()

    def on_ok(self):
        sel = list(self.listbox.curselection())[:5]
        self.result = [self.listbox.get(i) for i in sel]
        self.destroy()

    def on_cancel(self):
        self.result = None
        self.destroy()


class ITunesTkApp:
    def __init__(self, root: tk.Tk, config: ConfigManager):
        self.root = root
        self.root.title("iTunes Controller (GUI)")
        self.root.geometry("720x420")
        self.config = config
        self.ctrl = iTunesController()
        self.quick_slots = self.config.get_quick_slots()
        self.last_action = "起動"
        self.tap_times: List[float] = []
        self.bpm_value: float | None = None

        # UI
        self.build_ui()

        # Key binds (window focused)
        self.bind_keys()

        # Start update loop
        self.update_ui_loop()

    def build_ui(self):
        container = ttk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Track panel
        track_frame = ttk.LabelFrame(container, text="現在のトラック")
        track_frame.pack(fill=tk.X)
        self.track_status = ttk.Label(track_frame, text="⏸ 一時停止")
        self.track_status.pack(anchor="w", padx=8, pady=(6, 2))
        self.track_title = ttk.Label(track_frame, text="曲名: -", font=("", 10, "bold"))
        self.track_title.pack(anchor="w", padx=8)
        self.track_artist = ttk.Label(track_frame, text="アーティスト: -")
        self.track_artist.pack(anchor="w", padx=8)
        self.track_album = ttk.Label(track_frame, text="アルバム: -")
        self.track_album.pack(anchor="w", padx=8, pady=(0, 6))
        self.track_time = ttk.Label(track_frame, text="時間: 00:00 / 00:00")
        self.track_time.pack(anchor="w", padx=8, pady=(0, 8))
        self.last_action_label = ttk.Label(track_frame, text="最終アクション: 起動", foreground="#008b8b")
        self.last_action_label.pack(anchor="w", padx=8, pady=(0, 8))

        # BPM panel
        bpm_frame = ttk.LabelFrame(container, text="BPM (タップで計測)")
        bpm_frame.pack(fill=tk.X, pady=(6, 0))
        self.bpm_label = ttk.Label(bpm_frame, text="BPM: -")
        self.bpm_label.pack(side=tk.LEFT, padx=8, pady=6)
        ttk.Button(bpm_frame, text="Tap (t)", command=self.tap_bpm).pack(side=tk.LEFT, padx=4)
        ttk.Button(bpm_frame, text="Reset (x)", command=self.reset_bpm).pack(side=tk.LEFT, padx=4)

        # Quick slots
        slots_frame = ttk.LabelFrame(container, text="クイックスロット (1-5)")
        slots_frame.pack(fill=tk.X, pady=(10, 0))
        self.slot_labels: List[ttk.Label] = []
        for i in range(5):
            name = self.quick_slots[i] if i < len(self.quick_slots) else "(未設定)"
            lbl = ttk.Label(slots_frame, text=f"{i+1}. {name}")
            lbl.pack(anchor="w", padx=8)
            self.slot_labels.append(lbl)

        # Help
        help_frame = ttk.LabelFrame(container, text="操作")
        help_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        help_text = (
            "スペース: 再生/一時停止\n"
            f"→: +{self.config.config.skip_seconds}秒 / ←: -{self.config.config.skip_seconds}秒\n"
            "↑: 前の曲 / ↓: 次の曲\n"
            "1-5: クイックスロットに追加\n"
            "p: クイックスロット割当 / r: プレイリスト更新 / c: スロットのプレイリスト作成 / q: 終了"
        )
        ttk.Label(help_frame, text=help_text, justify=tk.LEFT).pack(anchor="w", padx=8, pady=6)

    def bind_keys(self):
        self.root.bind("<KeyPress>", self.on_key)

    def on_key(self, event: tk.Event):
        k = event.keysym
        # Normalize numbers from keyboard or numpad
        if k.isdigit():
            idx = int(k) - 1
            self.add_to_slot(idx)
            return "break"
        if k == "space":
            self.toggle_play_pause(); return "break"
        if k == "Right":
            self.skip_forward(); return "break"
        if k == "Left":
            self.skip_backward(); return "break"
        if k == "Up":
            self.prev_track(); return "break"
        if k == "Down":
            self.next_track(); return "break"
        if k in ("p", "P"):
            self.pick_slots(); return "break"
        if k in ("r", "R"):
            self.refresh_playlists(); return "break"
        if k in ("c", "C"):
            self.create_slots(); return "break"
        if k in ("t", "T"):
            self.tap_bpm(); return "break"
        if k in ("x", "X"):
            self.reset_bpm(); return "break"
        if k in ("q", "Q"):
            self.root.destroy(); return "break"

    # Actions
    def toggle_play_pause(self):
        self.ctrl.play_pause()
        self.last_action = "再生/一時停止"

    def skip_forward(self):
        self.ctrl.skip_forward(self.config.config.skip_seconds)
        self.last_action = f"+{self.config.config.skip_seconds}秒"

    def skip_backward(self):
        self.ctrl.skip_backward(self.config.config.skip_seconds)
        self.last_action = f"-{self.config.config.skip_seconds}秒"

    def next_track(self):
        self.ctrl.play_next_track(); self.last_action = "次の曲"

    def prev_track(self):
        self.ctrl.play_previous_track(); self.last_action = "前の曲"

    def add_to_slot(self, idx: int):
        if 0 <= idx < len(self.quick_slots):
            name = self.quick_slots[idx]
            ok = self.ctrl.add_to_playlist(name)
            if ok:
                self.last_action = f"追加: {name}"
            else:
                self.last_action = f"追加失敗: {name}"
        else:
            self.last_action = "未設定スロット"

    def pick_slots(self):
        names = self.ctrl.get_all_playlists()
        if not names:
            messagebox.showerror("エラー", "プレイリスト一覧を取得できませんでした")
            return
        dlg = PlaylistPicker(self.root, names, preselected=self.quick_slots)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        self.quick_slots = dlg.result[:5]
        self.config.set_quick_slots(self.quick_slots)
        for i in range(5):
            name = self.quick_slots[i] if i < len(self.quick_slots) else "(未設定)"
            self.slot_labels[i].configure(text=f"{i+1}. {name}")
        self.last_action = "クイックスロット更新"

    def create_slots(self):
        # スロットに表示されている名前でプレイリスト作成（存在しない場合に備え）
        for name in self.quick_slots:
            self.ctrl.create_playlist(name)
        self.refresh_playlists()
        self.last_action = "プレイリスト作成"

    def refresh_playlists(self):
        # 画面上はスロットの存在状態を色分けなどしない。必要なら後で拡張
        self.ctrl.get_playlists()
        self.last_action = "プレイリスト更新"

    def update_ui_loop(self):
        info = self.ctrl.get_current_track_info()
        if info:
            playing = info.get('is_playing', False)
            self.track_status.configure(text=("▶ 再生中" if playing else "⏸ 一時停止"))
            self.track_title.configure(text=f"曲名: {info.get('name', '-')}")
            self.track_artist.configure(text=f"アーティスト: {info.get('artist', '-')}")
            self.track_album.configure(text=f"アルバム: {info.get('album', '-')}")
            pos = int(info.get('position', 0) or 0)
            dur = int(info.get('duration', 0) or 0)
            pm, ps = divmod(pos, 60)
            dm, ds = divmod(dur, 60)
            self.track_time.configure(text=f"時間: {pm:02d}:{ps:02d} / {dm:02d}:{ds:02d}")
        # BPM表示更新
        if self.bpm_value:
            self.bpm_label.configure(text=f"BPM: {self.bpm_value:.1f}")
        else:
            self.bpm_label.configure(text="BPM: -")
        self.last_action_label.configure(text=f"最終アクション: {self.last_action}")
        self.root.after(int(self.config.config.refresh_interval * 1000), self.update_ui_loop)

    # BPM helpers
    def tap_bpm(self):
        import time
        now = time.time()
        # 古いタップを捨てる（8秒より前はクリア）
        self.tap_times = [t for t in self.tap_times if now - t <= 8.0]
        self.tap_times.append(now)
        if len(self.tap_times) >= 2:
            intervals = [self.tap_times[i] - self.tap_times[i-1] for i in range(1, len(self.tap_times))]
            # 明らかな外れ値を除外（0.25〜2.0秒の間のみ）→ BPM 30〜240相当
            intervals = [it for it in intervals if 0.25 <= it <= 2.0]
            if intervals:
                avg = sum(intervals[-8:]) / min(len(intervals), 8)
                bpm = 60.0 / avg
                self.bpm_value = bpm
                self.last_action = "Tap"
        else:
            self.bpm_value = None
            self.last_action = "Tap"

    def reset_bpm(self):
        self.tap_times = []
        self.bpm_value = None
        self.last_action = "BPMリセット"



def run_gui():
    cfg = ConfigManager("config.json")
    root = tk.Tk()
    app = ITunesTkApp(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    run_gui()

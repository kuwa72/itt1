import tkinter as tk
from tkinter import ttk, messagebox
from typing import List
from music_controller_base import create_music_controller
from config import ConfigManager


class PlaylistPicker(tk.Toplevel):
    def __init__(self, master, all_names: List[str], preselected: List[str] | None = None, max_select: int = 18):
        super().__init__(master)
        self.title("クイックスロット選択")
        self.geometry("600x600")
        self.resizable(False, False)
        self.result = None
        self.max_select = max_select

        label = ttk.Label(self, text=f"クイックスロットに割り当てるプレイリストを選択してください (最大{self.max_select}件)\nショートカット: F1–F12, 0–9")
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

        # Update selection count when selection changes
        self.listbox.bind('<<ListboxSelect>>', lambda e: self._update_selection_count())

        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, padx=10, pady=(6, 10))
        # Selection count label (left)
        self.count_label = ttk.Label(btns, text=f"選択: 0/{self.max_select}")
        self.count_label.pack(side=tk.LEFT)
        ttk.Button(btns, text="OK", command=self.on_ok).pack(side=tk.RIGHT)
        ttk.Button(btns, text="キャンセル", command=self.on_cancel).pack(side=tk.RIGHT, padx=(0, 6))

        self.bind("<Escape>", lambda e: self.on_cancel())
        self.transient(master)
        self.grab_set()
        self.focus_set()
        # Initialize count after preselection
        self._update_selection_count()

    def on_ok(self):
        sel = list(self.listbox.curselection())[: self.max_select]
        self.result = [self.listbox.get(i) for i in sel]
        self.destroy()

    def on_cancel(self):
        self.result = None
        self.destroy()

    def _update_selection_count(self):
        try:
            n = len(self.listbox.curselection())
        except Exception:
            n = 0
        self.count_label.configure(text=f"選択: {n}/{self.max_select}")


class ITunesTkApp:
    def __init__(self, root: tk.Tk, config: ConfigManager):
        self.root = root
        self.root.title("iTunes Controller (GUI)")
        self.root.geometry("1040x720")
        # Prevent excessive shrinking that may clip content
        self.root.minsize(860, 600)
        self.config = config
        self.ctrl = create_music_controller()
        self.quick_slots = self.config.get_quick_slots()
        self.last_action = "起動"
        self.tap_times: List[float] = []
        self.bpm_value: float | None = None
        # Key mapping for slots: F1..F12 (1..12), then digits 1..9,0 (13..22)
        self.slot_keys: List[str] = [*(f"F{i}" for i in range(1,13)), "1","2","3","4","5","6","7","8","9","0"]
        self.slot_count = len(self.slot_keys)  # 22
        # For current track playlist display
        self._last_track_sig: tuple | None = None
        self._last_playlists_of_track: List[str] = []
        self._fetching_track_playlists: bool = False
        self._loading_indicator_after_id: int | None = None
        self._warming_cache: bool = False

        # UI
        self.build_ui()

        # Key binds (window focused)
        self.bind_keys()

        # Start update loop
        self.update_ui_loop()

        # Warm up caches for quick slot playlists at startup
        self.start_warmup_caches()

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
        # Playlists containing current track
        self.track_in_playlists = ttk.Label(track_frame, text="この曲の登録先: -", foreground="#666")
        self.track_in_playlists.pack(anchor="w", padx=8, pady=(0, 8))
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
        slots_frame = ttk.LabelFrame(container, text="クイックスロット [F1–F12, 0–9]")
        slots_frame.pack(fill=tk.X, pady=(10, 0))
        self.slot_labels: List[ttk.Label] = []
        grid = ttk.Frame(slots_frame)
        grid.pack(fill=tk.X, padx=8, pady=6)
        # 4 columns grid for 22 slots (rows up to 6)
        columns = 4
        for c in range(columns):
            grid.columnconfigure(c, weight=1)
        for i in range(self.slot_count):
            name = self.quick_slots[i] if i < len(self.quick_slots) else "(未設定)"
            key_label = self.slot_keys[i]
            lbl = ttk.Label(grid, text=f"{key_label}: {name}")
            r, c = divmod(i, columns)
            lbl.grid(row=r, column=c, sticky="w", padx=10, pady=4)
            self.slot_labels.append(lbl)

        # Help
        help_frame = ttk.LabelFrame(container, text="操作")
        help_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        help_text = (
            "スペース: 再生/一時停止\n"
            f"→: +{self.config.config.skip_seconds}秒 / ←: -{self.config.config.skip_seconds}秒\n"
            "↑: 前の曲 / ↓: 次の曲\n"
            "F1–F12, 0–9: クイックスロットに追加（テンキー対応）\n"
            "p: クイックスロット割当 / r: プレイリスト更新 / c: プレイリスト新規作成 / q: 終了"
        )
        ttk.Label(help_frame, text=help_text, justify=tk.LEFT).pack(anchor="w", padx=8, pady=6)

    def bind_keys(self):
        self.root.bind("<KeyPress>", self.on_key)

    def on_key(self, event: tk.Event):
        k = event.keysym
        # Slots by function keys F1..F12 => 0..11, digits 1..9,0 => 12..21 (supports numpad)
        if k.startswith("F") and k[1:].isdigit():
            fn = int(k[1:])
            if 1 <= fn <= 12:
                self.add_to_slot(fn - 1)
                return "break"
        # digits top row or numpad
        if (k in tuple("1234567890")) or (k.startswith("KP_") and k[-1] in "1234567890"):
            d = k[-1]
            order = "1234567890"
            idx = 12 + order.index(d)
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
        if k == "c":
            self.create_single_playlist(); return "break"
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
            # Block while cache warming
            if self._warming_cache:
                try:
                    messagebox.showinfo("情報", "キャッシュ構築中のため、追加は一時停止します。完了後に再試行してください。")
                except Exception:
                    pass
                self.last_action = "追加保留: キャッシュ構築中"
                return
            # Ensure target playlist cache is fresh; if not, start warm-up and block
            try:
                fresh = self.ctrl.is_playlist_cache_fresh(name) if name and name != "(未設定)" else False
            except Exception:
                fresh = False
            if not fresh:
                self.start_warmup_caches()
                try:
                    messagebox.showinfo("情報", f"'{name}' のキャッシュを構築中です。完了後に再試行してください。")
                except Exception:
                    pass
                self.last_action = f"追加保留: キャッシュ構築中 ({name})"
                return
            # Cache-based duplicate decision
            try:
                pls = self.ctrl.get_playlists_of_current_track_from_cache() or []
            except Exception:
                pls = []
            if name in pls:
                # Already present -> treat as success, no AddTrack
                self.last_action = f"既に登録済み: {name}"
                self._refresh_track_playlists_from_cache()
                return
            # Proceed to actual add
            ok = self.ctrl.add_to_playlist(name)
            if ok:
                self.last_action = f"追加: {name}"
                # Addition succeeded; update label immediately from cache
                self._refresh_track_playlists_from_cache()
            else:
                self.last_action = f"追加失敗: {name}"
        else:
            self.last_action = "未設定スロット"

    def pick_slots(self):
        names = self.ctrl.get_all_playlists()
        if not names:
            messagebox.showerror("エラー", "プレイリスト一覧を取得できませんでした")
            return
        dlg = PlaylistPicker(self.root, names, preselected=self.quick_slots, max_select=self.slot_count)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        self.quick_slots = dlg.result[: self.slot_count]
        self.config.set_quick_slots(self.quick_slots)
        for i in range(self.slot_count):
            name = self.quick_slots[i] if i < len(self.quick_slots) else "(未設定)"
            key_label = self.slot_keys[i]
            self.slot_labels[i].configure(text=f"{key_label}: {name}")
        self.last_action = "クイックスロット更新"

    def create_single_playlist(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("新規プレイリスト", "作成するプレイリスト名を入力:", parent=self.root)
        if not name:
            self.last_action = "プレイリスト作成キャンセル"
            return
        ok = self.ctrl.create_playlist(name)
        self.refresh_playlists()
        self.last_action = f"プレイリスト作成: {'成功' if ok else '失敗'} ({name})"

    def refresh_playlists(self):
        # 画面上はスロットの存在状態を色分けなどしない。必要なら後で拡張
        self.ctrl.get_playlists()
        # After clearing caches in controller, warm them up again
        self.start_warmup_caches()
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
            # Update playlists containing current track when track signature changes (cache-only)
            sig = (info.get('name'), info.get('artist'), info.get('album'), int(info.get('duration', 0) or 0))
            if sig != self._last_track_sig:
                self._last_track_sig = sig
                # Query only from controller's caches (fast, non-blocking)
                try:
                    pls = self.ctrl.get_playlists_of_current_track_from_cache() or []
                except Exception:
                    pls = []
                # If any quick slot playlist cache is stale/absent, start warm-up and indicate it
                try:
                    stale_found = False
                    for name in self.quick_slots:
                        if not name or name == "(未設定)":
                            continue
                        if not self.ctrl.is_playlist_cache_fresh(name):
                            stale_found = True
                            break
                    if stale_found:
                        self.start_warmup_caches()
                except Exception:
                    pass
                self._last_playlists_of_track = pls
                # If cache warm-up is running, indicate it; otherwise show current cached result
                if self._warming_cache:
                    self.track_in_playlists.configure(text="この曲の登録先: キャッシュ構築中…")
                else:
                    txt = ", ".join(pls) if pls else "-"
                    self.track_in_playlists.configure(text=f"この曲の登録先: {txt}")
        # BPM表示更新
        if self.bpm_value:
            self.bpm_label.configure(text=f"BPM: {self.bpm_value:.1f}")
        else:
            self.bpm_label.configure(text="BPM: -")
        self.last_action_label.configure(text=f"最終アクション: {self.last_action}")
        self.root.after(int(self.config.config.refresh_interval * 1000), self.update_ui_loop)

    def _refresh_track_playlists_from_cache(self):
        """Update 'この曲の登録先' label from controller caches immediately."""
        try:
            pls = self.ctrl.get_playlists_of_current_track_from_cache() or []
        except Exception:
            pls = []
        self._last_playlists_of_track = pls
        # If warming cache, still show constructing state; otherwise show updated list
        if self._warming_cache:
            try:
                self.track_in_playlists.configure(text="この曲の登録先: キャッシュ構築中…")
            except Exception:
                pass
        else:
            txt = ", ".join(pls) if pls else "-"
            try:
                self.track_in_playlists.configure(text=f"この曲の登録先: {txt}")
            except Exception:
                pass

    def start_warmup_caches(self):
        if self._warming_cache:
            return
        self._warming_cache = True
        # Show warming indicator immediately
        try:
            self.track_in_playlists.configure(text="この曲の登録先: キャッシュ構築中…")
        except Exception:
            pass
        import threading
        current_sig = self._last_track_sig
        slots = list(self.quick_slots)
        def _worker():
            try:
                # Build DBID caches for quick slot playlists
                for name in slots:
                    if not name or name == "(未設定)":
                        continue
                    try:
                        # Build full cache for this playlist
                        self.ctrl.get_playlist_dbids_threadsafe(name)
                    except Exception:
                        continue
            finally:
                def _apply_after():
                    self._warming_cache = False
                    # After warm-up, update current track's membership from cache and show it
                    try:
                        pls = self.ctrl.get_playlists_of_current_track_from_cache() or []
                    except Exception:
                        pls = []
                    # Only apply if the track hasn't changed drastically; otherwise still fine to display latest
                    self._last_playlists_of_track = pls
                    txt = ", ".join(pls) if pls else "-"
                    try:
                        self.track_in_playlists.configure(text=f"この曲の登録先: {txt}")
                    except Exception:
                        pass
                try:
                    self.root.after(0, _apply_after)
                except Exception:
                    self._warming_cache = False
        threading.Thread(target=_worker, daemon=True).start()

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

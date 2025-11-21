import tkinter as tk
from tkinter import ttk, messagebox
from typing import List
import os
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
        # First-run only: if config file does NOT exist, auto-assign quick slots from existing playlists (top-first)
        cfg_exists = False
        try:
            cfg_exists = os.path.exists(self.config.config_file)
        except Exception:
            cfg_exists = True  # be safe: treat as exists to avoid unintended overwrite
        if not cfg_exists:
            try:
                detected = self.ctrl.get_all_playlists() or []
            except Exception:
                detected = []
            self.quick_slots = detected[:22]
            try:
                self.config.set_quick_slots(self.quick_slots)
            except Exception:
                pass
        else:
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
        
        # 表示状態
        self.show_progress = tk.BooleanVar(value=True)
        self.show_playlists = tk.BooleanVar(value=True)
        self.show_tracks = tk.BooleanVar(value=True)
        self.show_bpm = tk.BooleanVar(value=True)
        self.show_slots = tk.BooleanVar(value=True)
        
        # プログレスバーのドラッグ中フラグ
        self._seeking = False

        # UI
        self.build_ui()

        # Key binds (window focused)
        self.bind_keys()

        # Start update loop
        self.update_ui_loop()

    def build_ui(self):
        # メニューバー
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="表示", menu=view_menu)
        view_menu.add_checkbutton(label="プログレスバー", variable=self.show_progress, command=self.toggle_progress)
        view_menu.add_checkbutton(label="プレイリスト一覧", variable=self.show_playlists, command=self.toggle_playlists)
        view_menu.add_checkbutton(label="トラック一覧", variable=self.show_tracks, command=self.toggle_tracks)
        view_menu.add_checkbutton(label="BPMパネル", variable=self.show_bpm, command=self.toggle_bpm)
        view_menu.add_checkbutton(label="クイックスロット", variable=self.show_slots, command=self.toggle_slots)
        
        nav_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="移動", menu=nav_menu)
        nav_menu.add_command(label="再生中の曲へ移動 (Ctrl+G)", command=self.goto_current_track)
        
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
        
        # プログレスバー
        self.progress_frame = ttk.Frame(track_frame)
        self.progress_frame.pack(fill=tk.X, padx=8, pady=(0, 6))
        self.progress_bar = ttk.Scale(self.progress_frame, from_=0, to=100, orient=tk.HORIZONTAL, command=self.on_progress_change)
        self.progress_bar.pack(fill=tk.X)
        self.progress_bar.bind("<ButtonPress-1>", lambda e: setattr(self, '_seeking', True))
        self.progress_bar.bind("<ButtonRelease-1>", lambda e: setattr(self, '_seeking', False))
        
        self.track_time = ttk.Label(track_frame, text="時間: 00:00 / 00:00")
        self.track_time.pack(anchor="w", padx=8, pady=(0, 8))
        # Playlists containing current track
        self.track_in_playlists = ttk.Label(track_frame, text="この曲の登録先: -", foreground="#666")
        self.track_in_playlists.pack(anchor="w", padx=8, pady=(0, 8))
        self.last_action_label = ttk.Label(track_frame, text="最終アクション: 起動", foreground="#008b8b")
        self.last_action_label.pack(anchor="w", padx=8, pady=(0, 8))
        
        # 中央パネル（プレイリストとトラック）
        self.middle_paned = ttk.PanedWindow(container, orient=tk.HORIZONTAL)
        self.middle_paned.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        
        # プレイリスト一覧
        self.playlist_frame = ttk.LabelFrame(self.middle_paned, text="プレイリスト")
        self.middle_paned.add(self.playlist_frame, weight=1)
        
        # プレイリストツールバー
        playlist_toolbar = ttk.Frame(self.playlist_frame)
        playlist_toolbar.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(playlist_toolbar, text="再生中へ", command=self.goto_current_track, width=10).pack(side=tk.LEFT)
        
        playlist_scroll = ttk.Scrollbar(self.playlist_frame)
        playlist_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.playlist_listbox = tk.Listbox(self.playlist_frame, yscrollcommand=playlist_scroll.set)
        self.playlist_listbox.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        playlist_scroll.config(command=self.playlist_listbox.yview)
        self.playlist_listbox.bind("<Double-Button-1>", self.on_playlist_select)
        
        # トラック一覧
        self.track_frame_list = ttk.LabelFrame(self.middle_paned, text="トラック一覧")
        self.middle_paned.add(self.track_frame_list, weight=2)
        
        track_scroll = ttk.Scrollbar(self.track_frame_list)
        track_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.track_tree = ttk.Treeview(self.track_frame_list, columns=("artist", "album", "time"), show="tree headings", yscrollcommand=track_scroll.set)
        self.track_tree.heading("#0", text="曲名")
        self.track_tree.heading("artist", text="アーティスト")
        self.track_tree.heading("album", text="アルバム")
        self.track_tree.heading("time", text="時間")
        self.track_tree.column("#0", width=200)
        self.track_tree.column("artist", width=150)
        self.track_tree.column("album", width=150)
        self.track_tree.column("time", width=60)
        self.track_tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        track_scroll.config(command=self.track_tree.yview)
        self.track_tree.bind("<Double-Button-1>", self.on_track_select)

        # BPM panel
        self.bpm_frame = ttk.LabelFrame(container, text="BPM (タップで計測)")
        self.bpm_frame.pack(fill=tk.X, pady=(6, 0))
        self.bpm_label = ttk.Label(self.bpm_frame, text="BPM: -")
        self.bpm_label.pack(side=tk.LEFT, padx=8, pady=6)
        ttk.Button(self.bpm_frame, text="Tap (t)", command=self.tap_bpm).pack(side=tk.LEFT, padx=4)
        ttk.Button(self.bpm_frame, text="Reset (x)", command=self.reset_bpm).pack(side=tk.LEFT, padx=4)

        # Quick slots
        self.slots_frame = ttk.LabelFrame(container, text="クイックスロット [F1–F12, 0–9]")
        self.slots_frame.pack(fill=tk.X, pady=(10, 0))
        self.slot_labels: List[ttk.Label] = []
        grid = ttk.Frame(self.slots_frame)
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
        
        # プレイリストを読み込む
        self.load_playlists()

        # Help
        help_frame = ttk.LabelFrame(container, text="操作")
        help_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        help_text = (
            "スペース: 再生/一時停止\n"
            f"→: +{self.config.config.skip_seconds}秒 / ←: -{self.config.config.skip_seconds}秒\n"
            "↑: 前の曲 / ↓: 次の曲\n"
            "F1–F12, 0–9: クイックスロットに追加（テンキー対応）\n"
            "Ctrl+G: 再生中の曲へ移動\n"
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
        if k == "g" and (event.state & 0x4):  # Ctrl+G
            self.goto_current_track(); return "break"

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
            # Search APIベースなのでキャッシュ不要、即座に追加
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
        self.load_playlists()
        self.last_action = "プレイリスト更新"
    
    def load_playlists(self):
        """プレイリスト一覧を読み込む"""
        try:
            playlists = self.ctrl.get_playlists()
            self.playlist_listbox.delete(0, tk.END)
            for pl in playlists:
                self.playlist_listbox.insert(tk.END, pl['name'])
        except Exception as e:
            print(f"プレイリスト読み込みエラー: {e}")
    
    def on_playlist_select(self, event):
        """プレイリスト選択時"""
        try:
            selection = self.playlist_listbox.curselection()
            if not selection:
                return
            playlist_name = self.playlist_listbox.get(selection[0])
            self.load_tracks(playlist_name)
            self.last_action = f"プレイリスト選択: {playlist_name}"
        except Exception as e:
            print(f"プレイリスト選択エラー: {e}")
    
    def load_tracks(self, playlist_name: str):
        """トラック一覧を読み込む"""
        try:
            tracks = self.ctrl.get_playlist_tracks(playlist_name)
            print(f"トラック取得: {playlist_name} - {len(tracks)}曲")
            self.track_tree.delete(*self.track_tree.get_children())
            
            # 現在のトラックのDBIDを取得
            info = self.ctrl.get_current_track_info()
            current_dbid = info.get('dbid') if info else None
            
            for track in tracks:
                duration = track.get('duration', 0)
                m, s = divmod(duration, 60)
                time_str = f"{m:02d}:{s:02d}"
                
                # 現在再生中の曲をハイライト
                tags = ('playing',) if track.get('dbid') == current_dbid else ()
                
                self.track_tree.insert('', 'end', 
                    text=track.get('name', ''),
                    values=(track.get('artist', ''), track.get('album', ''), time_str),
                    tags=tags)
                self.track_tree.tag_configure('playing', background='#e0f0ff')
                
                # DBIDを保存（再生用）
                item_id = self.track_tree.get_children()[-1]
                self.track_tree.set(item_id, '#0', track.get('name', ''))
                # DBIDをタグとして保存
                self.track_tree.item(item_id, tags=tags + (f"dbid:{track.get('dbid')}",))
        except Exception as e:
            print(f"トラック読み込みエラー: {e}")
    
    def on_track_select(self, event):
        """トラック選択時（ダブルクリック）"""
        try:
            selection = self.track_tree.selection()
            if not selection:
                return
            item = selection[0]
            tags = self.track_tree.item(item, 'tags')
            
            # DBIDを取得
            dbid = None
            for tag in tags:
                if tag.startswith('dbid:'):
                    try:
                        dbid = int(tag.split(':')[1])
                        break
                    except:
                        pass
            
            if dbid:
                ok = self.ctrl.play_track_by_dbid(dbid)
                if ok:
                    self.last_action = f"トラック再生: {self.track_tree.item(item, 'text')}"
                else:
                    self.last_action = "トラック再生失敗"
        except Exception as e:
            print(f"トラック選択エラー: {e}")
    
    def on_progress_change(self, value):
        """プログレスバー変更時"""
        if self._seeking:
            try:
                info = self.ctrl.get_current_track_info()
                if info:
                    duration = info.get('duration', 0)
                    if duration > 0:
                        position = float(value) * duration / 100
                        self.ctrl.set_player_position(position)
            except Exception:
                pass
    
    # トグルメソッド
    def toggle_progress(self):
        if self.show_progress.get():
            self.progress_frame.pack(fill=tk.X, padx=8, pady=(0, 6))
        else:
            self.progress_frame.pack_forget()
    
    def toggle_playlists(self):
        if self.show_playlists.get():
            if self.playlist_frame not in self.middle_paned.panes():
                self.middle_paned.add(self.playlist_frame, weight=1)
        else:
            if self.playlist_frame in self.middle_paned.panes():
                self.middle_paned.remove(self.playlist_frame)
    
    def toggle_tracks(self):
        if self.show_tracks.get():
            if self.track_frame_list not in self.middle_paned.panes():
                self.middle_paned.add(self.track_frame_list, weight=2)
        else:
            if self.track_frame_list in self.middle_paned.panes():
                self.middle_paned.remove(self.track_frame_list)
    
    def toggle_bpm(self):
        if self.show_bpm.get():
            self.bpm_frame.pack(fill=tk.X, pady=(6, 0))
        else:
            self.bpm_frame.pack_forget()
    
    def toggle_slots(self):
        if self.show_slots.get():
            self.slots_frame.pack(fill=tk.X, pady=(10, 0))
        else:
            self.slots_frame.pack_forget()
    
    def goto_current_track(self):
        """現在再生中のプレイリストとトラックに移動"""
        try:
            # 現在のプレイリストを取得
            if not self.ctrl.itunes:
                return
            
            current_playlist = getattr(self.ctrl.itunes, 'CurrentPlaylist', None)
            if not current_playlist:
                self.last_action = "再生中のプレイリストがありません"
                return
            
            playlist_name = getattr(current_playlist, 'Name', '')
            if not playlist_name:
                self.last_action = "プレイリスト名を取得できません"
                return
            
            # プレイリスト一覧から該当プレイリストを選択
            for i in range(self.playlist_listbox.size()):
                if self.playlist_listbox.get(i) == playlist_name:
                    self.playlist_listbox.selection_clear(0, tk.END)
                    self.playlist_listbox.selection_set(i)
                    self.playlist_listbox.see(i)
                    break
            
            # トラック一覧を読み込む
            self.load_tracks(playlist_name)
            
            # 現在のトラックを選択
            info = self.ctrl.get_current_track_info()
            current_dbid = info.get('dbid') if info else None
            
            if current_dbid:
                # トラック一覧から該当トラックを探して選択
                for item in self.track_tree.get_children():
                    tags = self.track_tree.item(item, 'tags')
                    for tag in tags:
                        if tag.startswith('dbid:'):
                            try:
                                dbid = int(tag.split(':')[1])
                                if dbid == current_dbid:
                                    self.track_tree.selection_set(item)
                                    self.track_tree.see(item)
                                    self.track_tree.focus(item)
                                    self.last_action = f"再生中の曲へ移動: {playlist_name}"
                                    return
                            except:
                                pass
            
            self.last_action = f"プレイリストへ移動: {playlist_name}"
        except Exception as e:
            print(f"再生中の曲へ移動エラー: {e}")

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
            
            # プログレスバー更新（ドラッグ中は更新しない）
            if not self._seeking and dur > 0:
                progress = (pos / dur) * 100
                self.progress_bar.set(progress)
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

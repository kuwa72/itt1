import tkinter as tk
from tkinter import ttk, messagebox
from typing import List
import logging
import os
import plistlib
import sys
import threading
import time
import queue
from music_controller_base import create_music_controller
from config import ConfigManager

logger = logging.getLogger(__name__)

# COMワーカーのタスクキューへ入れる停止トークン（get のタイムアウト待ちを即時解除するため）
_COM_STOP = object()

# トラック読み込みワーカーがエラーをUIへ伝えるためのバッチ内センチネルキー
XML_ERROR_KEY = "__xml_error__"


class PlaylistPicker(tk.Toplevel):
    def __init__(self, master, all_names: List[str], preselected: List[str] | None = None, max_select: int = 18):
        super().__init__(master)
        self.title("クイックスロット選択")
        self.geometry("600x600")
        self.resizable(False, False)
        self.result = None
        self.max_select = max_select

        label = ttk.Label(self, text=f"クイックスロットに割り当てるプレイリストを選択してください (最大{self.max_select}件)")
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
    # update_ui_loop 連続失敗時のバックオフ上限（ミリ秒）
    UPDATE_LOOP_MAX_INTERVAL_MS = 30_000
    # シークバードラッグのデバウンス間隔（ミリ秒）。モーションごとの同期COMを避け、
    # ドラッグが止まってから1回だけシークを実行する
    SEEK_DEBOUNCE_MS = 150
    # 終了処理中フラグ / モーダルダイアログ表示カウント。
    # クラス既定値を置き、__init__ 未到達のインスタンスでも on_key 等が安全に参照できるようにする
    _closing: bool = False
    _modal_open: int = 0

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
            self.quick_slots = list(detected)
            try:
                self.config.set_quick_slots(self.quick_slots)
            except Exception:
                pass
        else:
            self.quick_slots = self.config.get_quick_slots()
        xml_path, xml_exists = ("", False)
        try:
            xml_path, xml_exists = self.config.check_library_xml_exists()
        except Exception:
            xml_path, xml_exists = ("", False)
        if xml_path and xml_exists:
            self.last_action = f"XML検出: {xml_path}"
        elif xml_path and not xml_exists:
            self.last_action = f"XML未検出: {xml_path}"
        else:
            self.last_action = "XML未設定"
        self._library_xml_path = xml_path
        self._library_xml_exists = bool(xml_path and xml_exists)
        self._library_xml_mtime: float | None = None
        self._library_xml_tracks: dict[str, dict] | None = None
        self._library_xml_playlists: list[dict] | None = None
        self._library_xml_lock = threading.Lock()
        # ライブラリXMLのバックグラウンド読込スレッド（plistlib.load をUIスレッドで行わない）
        self._library_xml_loader_thread: threading.Thread | None = None
        # プレイリスト一覧の表示ラベルに対応する実プレイリスト名（リストボックスのインデックスと対応）
        self._playlist_raw_names: List[str] = []
        self.tap_times: List[float] = []
        self.bpm_value: float | None = None
        # ASCII keyboard top 3 rows: number row + QWERTY + ASDF (per-page)
        self.slot_keys: List[str] = list("1234567890-=") + list("qwertyuiop[]\\") + list("asdfghjkl;'")
        self.bank_size = len(self.slot_keys)
        self.slot_bank = 0
        # 表示状態
        self.show_progress = tk.BooleanVar(value=True)
        self.show_playlists = tk.BooleanVar(value=True)
        self.show_tracks = tk.BooleanVar(value=True)
        self.show_bpm = tk.BooleanVar(value=True)
        self.show_slots = tk.BooleanVar(value=True)
        
        # プログレスバーのドラッグ中フラグ
        self._seeking = False
        
        # トラック読み込みワーカー管理（COMはコントローラー側で扱う）
        self._track_loading_thread: threading.Thread | None = None
        self._track_loading_cancel_event: threading.Event | None = None
        self._track_loading_queue: "queue.Queue[list[dict]] | None" = None
        self._track_loading_playlist: str | None = None
        self._track_loading_dbid: int | None = None
        # 読み込み世代番号（load_tracks ごとにインクリメント。ワーカー/キュー/ポーラーを世代に紐付ける）
        self._track_load_generation: int = 0
        # 手動選択による表示中は再生中プレイリストへの自動追従を抑止するフラグ
        self._manual_playlist_view: bool = False
        # シングルクリック表示の遅延実行ID（ダブルクリックとの競合回避用）
        self._playlist_click_after_id = None
        # ダブルクリック/Enterで再生した時刻（直後のButtonRelease由来の遅延実行を捨てるため）
        self._last_playlist_play_at: float = 0.0
        # 終了処理中フラグ（Trueの間は after の再スケジュールとグローバルホットキーを停止）
        self._closing = False
        # モーダルダイアログ表示中のカウント（>0 の間はグローバルホットキーを抑制）
        self._modal_open = 0
        # _after 経由で登録した after ID（終了時に一括 after_cancel する）
        self._pending_after_ids: set = set()

        # 曲変更検知用
        self._synced_dbid: int | None = None
        self._synced_playlist: str | None = None

        # update_ui_loop の連続失敗カウンタ（バックオフ用）
        self._update_loop_failures = 0

        # COMワーカー: UIスレッドをブロックする同期COM呼出しを専用スレッドに集約する。
        # ワーカー → UI への結果反映は _ui_queue 経由で update_ui_loop が drain する。
        self._ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self._com_task_queue: "queue.Queue" = queue.Queue()
        self._com_worker_stop = threading.Event()
        self._com_worker_thread: threading.Thread | None = None
        # ポーリングで得た最新のトラック情報（UI側はCOMを直接呼ばずこれを参照）
        self._latest_track_info: dict | None = None
        # シークバーのデバウンス管理（ドラッグ中のモーションイベントを1回のシークにまとめる）
        self._seek_pending_value: float | None = None
        self._seek_after_id = None

        # COMワーカー起動（トラック情報ポーリングとCOMタスク実行を1スレッドに集約）
        self._start_com_worker()
        # ライブラリXMLをバックグラウンドで先読み（起動直後の操作を速くする）
        self._ensure_library_xml_load_started()

        # UI
        self.build_ui()

        # Key binds (window focused)
        self.bind_keys()

        # ウィンドウを閉じる操作を終了処理に接続
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Start update loop
        self.update_ui_loop()

        # 起動直後にiTunesの再生状態をUIに反映
        self._after(300, self._sync_to_itunes_state)

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
        track_frame.columnconfigure(0, weight=1)
        track_frame.columnconfigure(1, weight=0)

        top_row = ttk.Frame(track_frame)
        top_row.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        top_row.columnconfigure(0, weight=0)
        top_row.columnconfigure(1, weight=1)
        top_row.columnconfigure(2, weight=0)

        self.track_status = ttk.Label(top_row, text="⏸ 一時停止", font=("", 14, "bold"))
        self.track_status.grid(row=0, column=0, sticky="w")
        self.track_title = ttk.Label(top_row, text="曲名: -", font=("", 16, "bold"))
        self.track_title.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.last_action_label = ttk.Label(top_row, text="最終アクション: 起動", foreground="#ff9800", font=("", 14, "bold"))
        self.last_action_label.grid(row=0, column=2, sticky="e")

        # BPM panel (inside track panel)
        self.bpm_frame = ttk.Frame(track_frame)
        self.bpm_frame.grid(row=0, column=1, sticky="e", padx=8, pady=(6, 2))
        self.bpm_label = ttk.Label(self.bpm_frame, text="BPM: -", font=("", 12))
        self.bpm_label.pack(side=tk.LEFT)
        ttk.Button(self.bpm_frame, text="Tap (/)", command=self.tap_bpm, takefocus=False).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(self.bpm_frame, text="Reset (x)", command=self.reset_bpm, takefocus=False).pack(side=tk.LEFT, padx=(6, 0))

        meta_row = ttk.Frame(track_frame)
        meta_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8)
        meta_row.columnconfigure(0, weight=1)
        meta_row.columnconfigure(1, weight=1)
        meta_row.columnconfigure(2, weight=0)
        self.track_artist = ttk.Label(meta_row, text="アーティスト: -", font=("", 12))
        self.track_artist.grid(row=0, column=0, sticky="w")
        self.track_album = ttk.Label(meta_row, text="アルバム: -", font=("", 12))
        self.track_album.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.track_time = ttk.Label(meta_row, text="時間: 00:00 / 00:00", font=("", 12))
        self.track_time.grid(row=0, column=2, sticky="e")
        
        # プログレスバー
        self.progress_frame = ttk.Frame(track_frame)
        self.progress_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(2, 4))
        self.progress_bar = ttk.Scale(self.progress_frame, from_=0, to=100, orient=tk.HORIZONTAL, command=self.on_progress_change)
        self.progress_bar.pack(fill=tk.X)
        self.progress_bar.bind("<ButtonPress-1>", lambda e: setattr(self, '_seeking', True))
        self.progress_bar.bind("<ButtonRelease-1>", lambda e: setattr(self, '_seeking', False))

        # 中央パネル（プレイリストとトラック）
        self.middle_paned = ttk.PanedWindow(container, orient=tk.HORIZONTAL)
        self.middle_paned.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        
        # プレイリスト一覧
        self.playlist_frame = ttk.LabelFrame(self.middle_paned, text="プレイリスト")
        self.middle_paned.add(self.playlist_frame, weight=1)
        
        # プレイリストツールバー
        playlist_toolbar = ttk.Frame(self.playlist_frame)
        playlist_toolbar.pack(fill=tk.X, padx=4, pady=4)
        goto_btn = ttk.Button(playlist_toolbar, text="再生中へ", command=self.goto_current_track, width=10, takefocus=False)
        goto_btn.pack(side=tk.LEFT)

        playlist_scroll = ttk.Scrollbar(self.playlist_frame)
        playlist_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.playlist_listbox = tk.Listbox(self.playlist_frame, yscrollcommand=playlist_scroll.set)
        self.playlist_listbox.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        playlist_scroll.config(command=self.playlist_listbox.yview)
        self.playlist_listbox.bind("<Double-Button-1>", self.on_playlist_select)
        self.playlist_listbox.bind("<ButtonRelease-1>", self.on_playlist_click)

        # キーボードナビゲーション: ↑↓で選択、Enterで再生、Spaceで再生（グローバル優先）
        self.playlist_listbox.bind("<Up>", self.on_playlist_nav_up)
        self.playlist_listbox.bind("<Down>", self.on_playlist_nav_down)
        self.playlist_listbox.bind("<Return>", self.on_playlist_enter)
        self.playlist_listbox.bind("<space>", self.on_playlist_space)
        
        # トラック一覧
        self.track_frame_list = ttk.LabelFrame(self.middle_paned, text="トラック一覧")
        self.middle_paned.add(self.track_frame_list, weight=2)
        
        track_scroll = ttk.Scrollbar(self.track_frame_list)
        track_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.track_tree = ttk.Treeview(
            self.track_frame_list,
            columns=("artist", "album", "time", "date_added", "purchase_date"),
            show="tree headings",
            yscrollcommand=track_scroll.set,
        )
        self.track_tree.heading("#0", text="曲名")
        self.track_tree.heading("artist", text="アーティスト")
        self.track_tree.heading("album", text="アルバム")
        self.track_tree.heading("time", text="時間")
        self.track_tree.heading("date_added", text="追加日")
        self.track_tree.heading("purchase_date", text="購入日")
        self.track_tree.column("#0", width=200)
        self.track_tree.column("artist", width=150)
        self.track_tree.column("album", width=150)
        self.track_tree.column("time", width=60)
        self.track_tree.column("date_added", width=120)
        self.track_tree.column("purchase_date", width=120)
        self.track_tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        track_scroll.config(command=self.track_tree.yview)
        self.track_tree.bind("<Double-Button-1>", self.on_track_select)
        # トラック一覧の上下キーを無効化（グローバルキーバインドと競合するため）
        self.track_tree.bind("<Up>", self.on_key)
        self.track_tree.bind("<Down>", self.on_key)
        self.track_tree.bind("<Left>", self.on_key)
        self.track_tree.bind("<Right>", self.on_key)
        self.track_tree.bind("<space>", self.on_key)

        self._track_tree_sort_reverse: dict[str, bool] = {}
        self._track_tree_item_meta: dict[str, dict] = {}

        self.track_tree.heading("#0", command=lambda: self.sort_track_tree("name"))
        self.track_tree.heading("artist", command=lambda: self.sort_track_tree("artist"))
        self.track_tree.heading("album", command=lambda: self.sort_track_tree("album"))
        self.track_tree.heading("time", command=lambda: self.sort_track_tree("time"))
        self.track_tree.heading("date_added", command=lambda: self.sort_track_tree("date_added"))
        self.track_tree.heading("purchase_date", command=lambda: self.sort_track_tree("purchase_date"))

        # Quick slots
        self.slots_frame = ttk.LabelFrame(container, text="クイックスロット")
        self.slots_frame.pack(fill=tk.X, pady=(6, 0))
        header = ttk.Frame(self.slots_frame)
        header.pack(fill=tk.X, padx=6, pady=(4, 0))
        self.slots_page_label = ttk.Label(header, text="バンク: 1")
        self.slots_page_label.pack(side=tk.LEFT)
        self.slots_hint_label = ttk.Label(header, text="上3段キーで追加  Ctrlでバンク2  ,/.でバンク固定切替", foreground="#666")
        self.slots_hint_label.pack(side=tk.RIGHT)
        self.slot_labels: List[tk.Label] = []
        # 数字行12, QWERTY行13, ASDF行11 と実際のキーボード上3段に対応
        self.slot_key_rows = [12, 13, 11]
        self._slot_default_bg = "#3a3a3a"
        self._slot_default_fg = "#eeeeee"
        self._slot_empty_fg = "#888888"
        slots_container = tk.Frame(self.slots_frame, bg="#2b2b2b")
        slots_container.pack(fill=tk.X, padx=4, pady=(2, 4))
        for row_idx, row_len in enumerate(self.slot_key_rows):
            row = tk.Frame(slots_container, bg="#2b2b2b")
            row.pack(fill=tk.X, expand=True, pady=1)
            for col in range(row_len):
                slot_idx = sum(self.slot_key_rows[:row_idx]) + col
                key_label = self.slot_keys[slot_idx]
                lbl = tk.Label(
                    row,
                    text=f"{key_label}\n(未設定)",
                    bg=self._slot_default_bg,
                    fg=self._slot_empty_fg,
                    font=("Yu Gothic UI", 10, "bold"),
                    relief=tk.RIDGE,
                    bd=1,
                    padx=2,
                    pady=2,
                    width=8,
                    height=2,
                    anchor="center",
                    justify="center",
                )
                lbl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)
                self.slot_labels.append(lbl)
        self.update_slot_labels()
        
        # プレイリストを読み込む
        self.load_playlists()

        # Help
        help_frame = ttk.LabelFrame(container, text="操作")
        help_frame.pack(fill=tk.X, expand=False, pady=(10, 0))
        
        help_header = ttk.Frame(help_frame)
        help_header.pack(fill=tk.X, padx=6, pady=4)
        self.help_visible = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            help_header,
            text="ヘルプ表示",
            variable=self.help_visible,
            command=self.toggle_help,
            takefocus=False
        ).pack(side=tk.LEFT)
        
        help_text = (
            "Space再生/停止 ←→スキップ ↑↓曲送り  "
            "上3段キーでスロット追加  Ctrl+上3段:バンク2  ,/.:バンク固定切替  "
            "b:スロット割当  v:プレイリスト一覧更新  c:プレイリスト作成"
        )
        self.help_label = ttk.Label(help_frame, text=help_text, justify=tk.LEFT, font=("", 10))
        self.help_label.pack(anchor="w", padx=6, pady=(0, 4))
        
        # 初期状態で非表示にする場合は以下をコメントアウトし、self.help_visible = tk.BooleanVar(value=False) に変更
        # self.help_label.pack_forget()

    def bind_keys(self):
        self.root.bind_all("<KeyPress>", self.on_key)

    def _after(self, ms: int, func, *args):
        """root.after のラッパー。返された ID を _pending_after_ids に記録し、
        終了時に一括 after_cancel できるようにする。
        発火済みの ID は残るが、after_cancel 側の失敗は無視するため問題ない。"""
        try:
            after_id = self.root.after(ms, func, *args)
        except Exception:
            return None
        try:
            ids = getattr(self, "_pending_after_ids", None)
            if ids is None:
                ids = self._pending_after_ids = set()
            ids.add(after_id)
        except Exception:
            pass
        return after_id

    def _begin_modal(self):
        self._modal_open = getattr(self, "_modal_open", 0) + 1

    def _end_modal(self):
        self._modal_open = max(0, getattr(self, "_modal_open", 0) - 1)

    def _show_error(self, message: str):
        """モーダルなエラーダイアログ。表示中はグローバルホットキーを抑制する"""
        self._begin_modal()
        try:
            messagebox.showerror("エラー", message)
        except Exception:
            pass
        finally:
            self._end_modal()

    # --- COMワーカー / UIキュー ---
    # UIスレッドをブロックする同期COM呼出しは専用ワーカースレッドに集約する。
    # STA で生成した COM オブジェクトは他スレッドから呼べないため、ワーカーは
    # ctrl.create_worker_controller() で自分専用の接続を持つ。
    # ワーカー → UI への反映は _ui_queue に積み、update_ui_loop が drain する
    # （Tk にはUIスレッドからのみ触れる原則を維持）。

    def _start_com_worker(self):
        """COMワーカースレッドを起動（多重起動防止）"""
        if getattr(self, "_com_task_queue", None) is None:
            return
        t = getattr(self, "_com_worker_thread", None)
        if t is not None and t.is_alive():
            return
        try:
            t = threading.Thread(
                target=self._com_worker_loop, daemon=True, name="itunes-com-worker"
            )
            self._com_worker_thread = t
            t.start()
        except Exception as e:
            logger.error("COMワーカー起動失敗: %s", e)
            self._com_worker_thread = None

    def _com_worker_loop(self):
        """COM操作専用ループ。タスク実行とトラック情報ポーリングを行う。

        - タスクキューに (fn, args, kwargs, result_tag) が来れば実行。
          fn が文字列ならワーカー用コントローラーのメソッド名として解決する。
        - タイムアウト（refresh_interval 経過）時は get_current_track_info を
          ポーリングして ("track_info", info) を _ui_queue へ積む。
        - result_tag があれば ("task_result", tag, result) を _ui_queue へ積む。
        """
        ctrl = self.ctrl
        try:
            factory = getattr(self.ctrl, "create_worker_controller", None)
            if callable(factory):
                ctrl = factory()
        except Exception as e:
            logger.error("COMワーカー用コントローラー生成失敗: %s", e)
            ctrl = self.ctrl
        try:
            while not self._com_worker_stop.is_set() and not getattr(self, "_closing", False):
                try:
                    interval = float(self.config.config.refresh_interval)
                except Exception:
                    interval = 0.5
                interval = max(0.1, interval)
                try:
                    task = self._com_task_queue.get(timeout=interval)
                except queue.Empty:
                    task = None
                try:
                    if task is _COM_STOP:
                        break
                    if task is None:
                        self._poll_track_info(ctrl)
                    else:
                        self._execute_com_task(ctrl, task)
                except Exception as e:
                    logger.error("COMワーカー処理エラー: %s", e)
        finally:
            # ワーカー専用接続（別スレッドで CoInitialize した COM）はここで解放
            if ctrl is not self.ctrl:
                close = getattr(ctrl, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass

    def _poll_track_info(self, ctrl):
        """ワーカースレッド側: トラック情報を取得してUIキューへ積む"""
        try:
            info = ctrl.get_current_track_info()
        except Exception as e:
            logger.error("トラック情報ポーリングエラー: %s", e)
            info = None
        self._ui_put("track_info", info)

    def _execute_com_task(self, ctrl, task):
        """ワーカースレッド側: COMタスクを1件実行し、必要なら結果をUIキューへ返す"""
        fn, args, kwargs, result_tag = task
        try:
            target = getattr(ctrl, fn) if isinstance(fn, str) else fn
            result = target(*args, **kwargs)
        except Exception as e:
            logger.error("COMタスク実行エラー: %s", e)
            return
        if result_tag is not None:
            self._ui_put("task_result", result_tag, result)

    def _com_submit(self, fn, *args, _result_tag=None, **kwargs):
        """COMワーカーにタスクを投入する。UIスレッドはブロックしない。

        fn: コントローラーのメソッド名(str)、または呼び出し可能オブジェクト。
        _result_tag: 結果を _ui_queue へ返すときの識別タグ。
        """
        if getattr(self, "_closing", False):
            return
        q = getattr(self, "_com_task_queue", None)
        if q is None:
            return
        try:
            q.put((fn, args, kwargs, _result_tag))
        except Exception:
            pass

    def _ui_put(self, *msg):
        """ワーカー → UI へのメッセージ投入（UIキューが無ければ捨てる）"""
        q = getattr(self, "_ui_queue", None)
        if q is None:
            return
        try:
            q.put(msg)
        except Exception:
            pass

    def _drain_ui_queue(self):
        """UIスレッド側: ワーカーからのメッセージを処理する。
        1ティックで処理する件数を制限してUIスレッドを占有しすぎない。"""
        q = getattr(self, "_ui_queue", None)
        if q is None:
            return
        info_seen = False
        latest_info = None
        processed = 0
        while processed < 20:
            try:
                msg = q.get_nowait()
            except queue.Empty:
                break
            processed += 1
            try:
                kind = msg[0]
                if kind == "track_info":
                    info_seen = True
                    latest_info = msg[1]
                elif kind == "task_result":
                    self._handle_task_result(msg[1], msg[2])
                elif kind == "library_xml_loaded":
                    self._on_library_xml_loaded(msg[1])
            except Exception as e:
                logger.error("UIキュー処理エラー: %s", e)
        if info_seen:
            self._latest_track_info = latest_info or {}
            self._apply_track_info(self._latest_track_info)

    def _handle_task_result(self, tag, result):
        """COMタスクの結果をUIへ反映（UIスレッド）"""
        try:
            if tag == "playlists":
                self._apply_playlist_list(result or [])
                return
            if isinstance(tag, tuple) and tag:
                kind = tag[0]
                name = tag[1] if len(tag) > 1 else None
                if kind == "play_track":
                    self.last_action = (
                        f"トラック再生: {name or '-'}" if result else "トラック再生失敗"
                    )
                elif kind == "play_playlist" and not result:
                    self.last_action = f"プレイリスト再生失敗: {name or '-'}"
        except Exception as e:
            logger.error("タスク結果処理エラー: %s", e)

    def _apply_track_info(self, info):
        """ポーリング済みのトラック情報をウィジェットへ反映（UIスレッド。COM不使用）"""
        if not info:
            return
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

        # 曲変更検知: dbid またはプレイリストが変わったら同期
        cur_dbid = info.get('dbid')
        cur_playlist = info.get('playlist')
        if cur_dbid is not None and (
            cur_dbid != self._synced_dbid or cur_playlist != self._synced_playlist
        ):
            self._synced_dbid = cur_dbid
            self._synced_playlist = cur_playlist
            self._on_track_changed(cur_playlist, cur_dbid)

    def on_closing(self):
        """終了処理。WM_DELETE_WINDOW と 'm' キーの両方から呼ばれる。

        実行中ワーカーへのキャンセル通知 → 短い待機 → 登録済み after のキャンセル
        → COM 解放 → root.destroy の順でクリーンアップする。
        """
        if self._closing:
            return
        self._closing = True

        # 実行中のトラック読み込みワーカーにキャンセルを通知
        try:
            cancel_event = getattr(self, "_track_loading_cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()
        except Exception:
            pass

        # daemon ワーカーが cancel を検知して終了処理を回るまで短く待つ（UIを長く止めない）
        try:
            worker = getattr(self, "_track_loading_thread", None)
            if worker is not None and worker.is_alive():
                worker.join(timeout=0.3)
        except Exception:
            pass

        # COMワーカーを停止（get(timeout) 待ちを即時解除するため停止トークンも投入）
        try:
            stop = getattr(self, "_com_worker_stop", None)
            if stop is not None:
                stop.set()
        except Exception:
            pass
        try:
            task_q = getattr(self, "_com_task_queue", None)
            if task_q is not None:
                task_q.put(_COM_STOP)
        except Exception:
            pass
        try:
            com_worker = getattr(self, "_com_worker_thread", None)
            if com_worker is not None and com_worker.is_alive():
                com_worker.join(timeout=0.5)
        except Exception:
            pass

        # ライブラリXML読込スレッドも短く待つ（解析中は中断できないためデッドライン付き）
        try:
            loader = getattr(self, "_library_xml_loader_thread", None)
            if loader is not None and loader.is_alive():
                loader.join(timeout=0.2)
        except Exception:
            pass

        # 管理下の after コールバックをすべてキャンセル（destroy 後の発火/TclError を防ぐ）
        cancel_ids = list(getattr(self, "_pending_after_ids", None) or ())
        click_after_id = getattr(self, "_playlist_click_after_id", None)
        if click_after_id is not None:
            cancel_ids.append(click_after_id)
        for after_id in cancel_ids:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
        self._pending_after_ids = set()
        self._playlist_click_after_id = None

        # メインスレッドで CoInitialize 済みの COM を解放（コントローラーが対応していれば）
        try:
            close = getattr(self.ctrl, "close", None)
            if callable(close):
                close()
        except Exception:
            pass

        try:
            self.root.destroy()
        except Exception:
            pass

    def toggle_help(self):
        """ヘルプ表示のトグル"""
        if self.help_visible.get():
            self.help_label.pack(anchor="w", padx=6, pady=(0, 4))
        else:
            self.help_label.pack_forget()

    def normalize_key(self, keysym: str) -> str:
        k = (keysym or "").lower()
        alias = {
            'minus': '-',
            'equal': '=',
            'bracketleft': '[',
            'bracketright': ']',
            'backslash': '\\',
            'semicolon': ';',
            'apostrophe': "'",
            'comma': ',',
            'period': '.',
            'slash': '/',
        }
        return alias.get(k, keysym)

    def update_slot_labels(self):
        base = self.slot_bank * self.bank_size

        try:
            self.slots_page_label.configure(text=f"バンク: {self.slot_bank + 1}/2")
        except Exception:
            pass

        # リストとバンクサイズがずれている場合もクラッシュしないように、実際のラベル数でループ
        for i, lbl in enumerate(self.slot_labels):
            idx = base + i
            name = self.quick_slots[idx] if idx < len(self.quick_slots) else None
            key_label = self.slot_keys[i]
            if name:
                lbl.configure(text=f"{key_label}\n{name}", fg=self._slot_default_fg, bg=self._slot_default_bg)
            else:
                lbl.configure(text=f"{key_label}\n(未設定)", fg=self._slot_empty_fg, bg=self._slot_default_bg)

    def toggle_slot_bank(self):
        self.slot_bank = 0 if self.slot_bank else 1
        self.update_slot_labels()
        self.last_action = f"バンク: {self.slot_bank + 1}"

    def on_key(self, event: tk.Event):
        try:
            # 終了処理中・モーダルダイアログ表示中はグローバルホットキーを抑制
            if self._closing or getattr(self, "_modal_open", 0):
                return

            # 入力ボックス（Entry/Text）にフォーカスがある場合はホットキーを無効化
            focus = self.root.focus_get()
            if focus is not None and isinstance(focus, (tk.Entry, tk.Text, ttk.Entry, tk.Spinbox)):
                return

            k = self.normalize_key(event.keysym)

            # Ctrl+G is reserved for navigation
            if k == 'g' and (event.state & 0x4):
                self.goto_current_track(); return "break"

            is_ctrl = bool(event.state & 0x4)
            eff_bank = 1 if is_ctrl else self.slot_bank
            if k in self.slot_keys:
                idx = eff_bank * self.bank_size + self.slot_keys.index(k)
                self.add_to_slot(idx)
                return "break"
            if k == ',':
                self.toggle_slot_bank(); return "break"
            if k == '.':
                self.toggle_slot_bank(); return "break"
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
            if k == "b":
                self.pick_slots(); return "break"
            if k == "v":
                self.refresh_playlists(); return "break"
            if k == "c":
                self.create_single_playlist(); return "break"
            if k == "/":
                self.tap_bpm(); return "break"
            if k == "x":
                self.reset_bpm(); return "break"
            if k == "m":
                self.on_closing(); return "break"
            if k == "g" and (event.state & 0x4):  # Ctrl+G
                self.goto_current_track(); return "break"
        except Exception as e:
            # bind_all 経由のホットキー処理で例外が起きても Tk へ伝播させない
            logger.error("キー入力処理エラー: %s", e)
            self.last_action = f"キーエラー: {e}"

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
        widget_idx = idx % self.bank_size
        if 0 <= idx < len(self.quick_slots):
            name = self.quick_slots[idx]
            # Search APIベースなのでキャッシュ不要、即座に追加
            result = self.ctrl.add_to_playlist(name)
            if result == "already_exists":
                self.last_action = f"既に存在: {name}"
                self._flash_slot(widget_idx, "warning")
            elif result == "added":
                self.last_action = f"追加: {name}"
                self._flash_slot(widget_idx, "success")
            else:
                self.last_action = f"追加失敗: {name}"
                self._flash_slot(widget_idx, "error")
        else:
            self.last_action = "未設定スロット"
            self._flash_slot(widget_idx, "warning")

    def _flash_slot(self, widget_idx: int, status: str):
        """スロットを一瞬色でフィードバックする。status: success/error/warning"""
        if not (0 <= widget_idx < len(self.slot_labels)):
            return
        colors = {
            "success": ("#2e7d32", "#ffffff"),
            "error": ("#b71c1c", "#ffffff"),
            "warning": ("#7a5c00", "#ffffff"),
        }
        bg, fg = colors.get(status, (self._slot_default_bg, self._slot_default_fg))
        lbl = self.slot_labels[widget_idx]
        try:
            lbl.configure(bg=bg, fg=fg)
        except Exception:
            return
        self._after(300, self.update_slot_labels)

    def pick_slots(self):
        # UIスレッドで全件走査(get_all_playlists)せず、load_playlists が
        # バックグラウンド取得済みのキャッシュ(_playlist_raw_names)を使う
        names = list(getattr(self, "_playlist_raw_names", None) or [])
        if not names:
            # 未取得なら取得を要求しておき、次回以降はキャッシュが使える
            self.load_playlists()
            self._show_error("プレイリスト一覧を取得できませんでした")
            return
        folder_map = self._get_playlist_folder_map()
        labels = [self._playlist_display_label(n, folder_map) for n in names]
        label_to_raw = dict(zip(labels, names))
        preselected_labels = [self._playlist_display_label(n, folder_map) for n in self.quick_slots]
        # モーダル表示中はグローバルホットキーを抑制するためフラグを立てる
        self._begin_modal()
        try:
            dlg = PlaylistPicker(self.root, labels, preselected=preselected_labels, max_select=len(names))
            self.root.wait_window(dlg)
        finally:
            self._end_modal()
        if dlg.result is None:
            return
        self.quick_slots = [label_to_raw[label] for label in dlg.result]
        self.config.set_quick_slots(self.quick_slots)
        self.slot_bank = 0
        self.update_slot_labels()
        self.last_action = "クイックスロット更新"

    def create_single_playlist(self):
        from tkinter import simpledialog
        # モーダル表示中はグローバルホットキーを抑制するためフラグを立てる
        self._begin_modal()
        try:
            name = simpledialog.askstring("新規プレイリスト", "作成するプレイリスト名を入力:", parent=self.root)
        finally:
            self._end_modal()
        if not name:
            self.last_action = "プレイリスト作成キャンセル"
            return
        ok = self.ctrl.create_playlist(name)
        self.refresh_playlists()
        self.last_action = f"プレイリスト作成: {'成功' if ok else '失敗'} ({name})"

    def refresh_playlists(self):
        # 画面上はスロットの存在状態を色分けなどしない。必要なら後で拡張
        self.load_playlists()
        self.last_action = "プレイリスト更新"
    
    @staticmethod
    def _playlist_display_label(name: str, folder_map: dict) -> str:
        """一覧表示用ラベルを返す。フォルダ内なら 'フォルダ/名前' 形式"""
        folder = folder_map.get(name)
        return f"{folder}/{name}" if folder else name

    def _get_playlist_folder_map(self) -> dict:
        """ライブラリXMLから {プレイリスト名: フォルダパス} を構築して返す。XMLが使えない場合は空dict。

        UIスレッドでは plistlib.load を実行しない。キャッシュが未取得/更新ありの場合は
        バックグラウンド読込を要求したうえで今回は空dictを返す。
        """
        try:
            if not self._library_xml_cache_valid():
                self._ensure_library_xml_load_started()
                return {}
            with self._library_xml_lock:
                playlists = self._library_xml_playlists or []
        except Exception:
            return {}

        by_pid = {}
        for p in playlists:
            if isinstance(p, dict):
                by_pid[p.get('Playlist Persistent ID')] = p

        folder_map = {}
        for p in playlists:
            if not isinstance(p, dict):
                continue
            name = p.get('Name')
            parent_pid = p.get('Parent Persistent ID')
            if not name or not parent_pid:
                continue
            # 親フォルダのチェーンを辿って '上位/下位' のパスを組み立てる
            parts = []
            seen = set()
            cur = by_pid.get(parent_pid)
            while cur is not None and cur.get('Playlist Persistent ID') not in seen:
                seen.add(cur.get('Playlist Persistent ID'))
                parts.append(cur.get('Name'))
                cur = by_pid.get(cur.get('Parent Persistent ID'))
            if parts:
                folder_map[name] = '/'.join(reversed(parts))
        return folder_map

    def load_playlists(self):
        """プレイリスト一覧をCOMワーカー経由でバックグラウンド取得する。
        結果は ("task_result", "playlists", ...) として _ui_queue に届き、
        update_ui_loop の drain で _apply_playlist_list が呼ばれる。"""
        self._com_submit("get_playlists", _result_tag="playlists")

    def _apply_playlist_list(self, playlists):
        """get_playlists の結果をリストボックスへ反映（UIスレッド）"""
        try:
            folder_map = self._get_playlist_folder_map()
            self._playlist_raw_names = []
            self.playlist_listbox.delete(0, tk.END)
            for pl in playlists:
                name = pl['name']
                self._playlist_raw_names.append(name)
                self.playlist_listbox.insert(tk.END, self._playlist_display_label(name, folder_map))
        except Exception as e:
            logger.error("プレイリスト読み込みエラー: %s", e)

    def _refresh_playlist_labels(self):
        """表示中プレイリスト一覧のラベルをキャッシュ済みXML情報で更新する（COM不使用）。
        XML読込完了後にフォルダパス付き表示へ追従するために使う。"""
        try:
            folder_map = self._get_playlist_folder_map()
            selected = set(self.playlist_listbox.curselection() or ())
            self.playlist_listbox.delete(0, tk.END)
            for i, name in enumerate(self._playlist_raw_names):
                self.playlist_listbox.insert(tk.END, self._playlist_display_label(name, folder_map))
                if i in selected:
                    self.playlist_listbox.selection_set(i)
        except Exception as e:
            logger.error("プレイリストラベル更新エラー: %s", e)

    def on_playlist_click(self, event):
        """シングルクリック: 再生せずに選択プレイリストのトラック一覧だけ表示する。
        ダブルクリック時もButtonReleaseが先行するため、遅延実行してダブルクリック側に譲る。
        """
        try:
            if self._playlist_click_after_id is not None:
                self.root.after_cancel(self._playlist_click_after_id)
            self._playlist_click_after_id = self._after(250, self._show_selected_playlist_tracks)
        except Exception:
            pass

    def _show_selected_playlist_tracks(self):
        """選択中プレイリストのトラック一覧を表示（再生は行わない）"""
        self._playlist_click_after_id = None
        if self._closing:
            return
        # ダブルクリック/Enter直後のButtonRelease由来の遅延実行は捨てる
        if time.monotonic() - self._last_playlist_play_at < 0.5:
            return
        try:
            selection = self.playlist_listbox.curselection()
            if not selection:
                return
            playlist_name = self._playlist_raw_names[selection[0]]
            self._manual_playlist_view = True
            self.load_tracks(playlist_name)
            self.last_action = f"トラック一覧表示: {playlist_name}"
        except Exception as e:
            logger.error("プレイリスト表示エラー: %s", e)

    def on_playlist_select(self, event):
        """プレイリスト選択時（ダブルクリック/Enter: 再生してトラック一覧表示）"""
        try:
            # シングルクリック表示の遅延実行が残っていればキャンセル
            if self._playlist_click_after_id is not None:
                self.root.after_cancel(self._playlist_click_after_id)
                self._playlist_click_after_id = None
            self._manual_playlist_view = False
            self._last_playlist_play_at = time.monotonic()
            selection = self.playlist_listbox.curselection()
            if not selection:
                return
            playlist_name = self._playlist_raw_names[selection[0]]
            
            # 即座に再生を開始（COM呼出しはワーカースレッドへ委譲しUIをブロックしない）
            if hasattr(self.ctrl, 'play_playlist'):
                self._com_submit(
                    "play_playlist", playlist_name,
                    _result_tag=("play_playlist", playlist_name),
                )
            
            # トラック一覧の読み込み（バックグラウンド）
            self.load_tracks(playlist_name)
            self.last_action = f"プレイリスト再生・選択: {playlist_name}"
        except Exception as e:
            logger.error("プレイリスト選択エラー: %s", e)
    
    def on_playlist_nav_up(self, event):
        """プレイリスト一覧で上キー"""
        try:
            current = self.playlist_listbox.curselection()
            if current:
                idx = current[0]
                if idx > 0:
                    self.playlist_listbox.selection_clear(0, tk.END)
                    self.playlist_listbox.selection_set(idx - 1)
                    self.playlist_listbox.see(idx - 1)
                    self.playlist_listbox.activate(idx - 1)
        except Exception:
            pass
    
    def on_playlist_nav_down(self, event):
        """プレイリスト一覧で下キー"""
        try:
            current = self.playlist_listbox.curselection()
            if current:
                idx = current[0]
                if idx < self.playlist_listbox.size() - 1:
                    self.playlist_listbox.selection_clear(0, tk.END)
                    self.playlist_listbox.selection_set(idx + 1)
                    self.playlist_listbox.see(idx + 1)
                    self.playlist_listbox.activate(idx + 1)
        except Exception:
            pass
    
    def on_playlist_enter(self, event):
        """プレイリスト一覧でEnterキー（選択プレイリストを再生）"""
        try:
            selection = self.playlist_listbox.curselection()
            if selection:
                self.on_playlist_select(event)
        except Exception:
            pass
    
    def on_playlist_space(self, event):
        """プレイリスト一覧でSpaceキー（グローバル再生/停止を優先）"""
        # グローバルキーを優先させるため、何もしない
        pass
    
    def load_tracks(self, playlist_name: str):
        """トラック一覧をバックグラウンドCOMワーカー経由でプログレッシブに読み込む"""
        # 既存ワーカーをキャンセル（joinはしない。旧ワーカーはcancel_eventを見て自然終了し、
        # 残りのバッチは旧世代のキューにのみ届くため新世代へ混入しない）
        if self._track_loading_cancel_event is not None:
            self._track_loading_cancel_event.set()

        # 新しい世代を開始
        self._track_load_generation += 1
        generation = self._track_load_generation

        # 状態を初期化
        load_queue: "queue.Queue[list[dict]]" = queue.Queue()
        cancel_event = threading.Event()
        self._track_loading_queue = load_queue
        self._track_loading_cancel_event = cancel_event
        self._track_loading_playlist = playlist_name

        # UIスレッドではCOMを呼ばず、COMワーカーのポーリング済みキャッシュを使う
        info = getattr(self, "_latest_track_info", None) or {}
        self._track_loading_dbid = info.get("dbid") if info else None

        # トラック一覧をクリア
        self.track_tree.delete(*self.track_tree.get_children())
        try:
            self._track_tree_item_meta.clear()
        except Exception:
            pass

        # ワーカー起動（Tkに触らない）。キューとキャンセルイベントはこの世代のものを
        # クロージャで捕捉し、新世代に上書きされるインスタンス属性は遅延参照しない
        def _on_batch(batch: list[dict]) -> None:
            if not cancel_event.is_set():
                load_queue.put(batch)

        if not self._library_xml_exists:
            self.last_action = "XML未設定/未検出のためトラック一覧を取得できません"
            self._show_error("ライブラリXMLが見つからないためトラック一覧を取得できません")
            return

        # UIスレッドでは plistlib.load を実行しない。キャッシュが有効なときだけ
        # インメモリの軽量チェックでプレイリスト存在を確認し、未取得/更新ありなら
        # ワーカースレッド側で読み込ませる（エラーはエラーバッチとしてUIへ届く）。
        if self._library_xml_cache_valid():
            with self._library_xml_lock:
                pls = self._library_xml_playlists or []
            if not any((p.get('Name') == playlist_name) for p in pls if isinstance(p, dict)):
                self.last_action = f"XMLにプレイリストがありません: {playlist_name}"
                self._show_error(f"XMLにプレイリストがありません: {playlist_name}")
                return
        else:
            self.last_action = "ライブラリXML読込中..."

        self._track_loading_thread = threading.Thread(
            target=self._stream_playlist_tracks_from_xml_worker,
            args=(playlist_name, 50, _on_batch, cancel_event),
            daemon=True,
        )
        self._track_loading_thread.start()

        # キューポーリング開始（世代ごとに1系統。旧世代のポーラーは次回発火時に終了する）
        self._poll_track_queue(generation)

    def _poll_track_queue(self, generation: int):
        """バックグラウンドワーカーから届いたトラックバッチをUIに反映"""
        # 終了処理中、または自分の世代ではなくなったポーラーは即終了（新キューを読まない・再スケジュールしない）
        if self._closing or generation != self._track_load_generation:
            return

        q = self._track_loading_queue
        cancel_event = self._track_loading_cancel_event
        if q is None:
            return

        # 1回の呼び出しで処理するバッチ数を制限して、UIスレッドを占有しすぎないようにする
        max_batches_per_tick = 3
        processed = 0
        try:
            while processed < max_batches_per_tick:
                batch = q.get_nowait()
                self._display_tracks_batch_sync(batch, self._track_loading_dbid, cancel_event)
                processed += 1
        except queue.Empty:
            pass

        # まだワーカーが動いているか、キューに残りがあれば再スケジュール
        worker_alive = (
            self._track_loading_thread is not None
            and self._track_loading_thread.is_alive()
            and cancel_event is not None
            and not cancel_event.is_set()
        )
        if worker_alive or (not q.empty()):
            self._after(30, self._poll_track_queue, generation)
        else:
            # ワーカー完了: 保存されたdbidでトラックをハイライト
            if self._track_loading_dbid is not None:
                self._highlight_track_by_dbid(self._track_loading_dbid)

    def _display_tracks_batch_sync(self, tracks, current_dbid, cancel_event=None):
        """トラックのバッチをUIに表示（同期実行）。cancel_eventは呼出し世代のものを渡す"""
        # ワーカーからのエラーバッチ: トラック行ではなくエラー表示へ回す
        if tracks and isinstance(tracks[0], dict) and XML_ERROR_KEY in tracks[0]:
            msg = tracks[0].get(XML_ERROR_KEY) or "XML読込エラー"
            self.last_action = msg
            try:
                self._show_error(msg)
            except Exception:
                pass
            return

        # PlayOrderIndex順にソート
        tracks.sort(key=lambda x: x.get('play_order', 0))

        for track in tracks:
            if cancel_event is not None and cancel_event.is_set():
                return
            
            duration = track.get('duration', 0)
            m, s = divmod(duration, 60)
            time_str = f"{m:02d}:{s:02d}"

            date_added = track.get('date_added')
            purchase_date = track.get('purchase_date')
            date_added_str = self._format_track_date(date_added)
            purchase_date_str = self._format_track_date(purchase_date)
            
            # 現在再生中の曲をハイライト
            tags = ('playing',) if track.get('dbid') == current_dbid else ()
            
            # DBIDとIITObject IDs、Persistent ID、PlayOrderをタグとして保存
            dbid_tag = f"dbid:{track.get('dbid')}"
            pid_tag = f"pid:{track.get('persistent_id')}"
            src_tag = f"src:{track.get('source_id')}"
            pl_tag = f"pl:{track.get('playlist_id')}"
            trk_tag = f"tid:{track.get('track_id')}"
            loc_tag = f"loc:{track.get('location')}"
            po_tag = f"po:{track.get('play_order')}"
            item_tags = tags + (dbid_tag, pid_tag, src_tag, pl_tag, trk_tag, loc_tag, po_tag)
            
            try:
                iid = self.track_tree.insert('', 'end',
                    text=track.get('name', ''),
                    values=(
                        track.get('artist', ''),
                        track.get('album', ''),
                        time_str,
                        date_added_str,
                        purchase_date_str,
                    ),
                    tags=item_tags)
                self._track_tree_item_meta[iid] = {
                    'name': track.get('name', '') or '',
                    'artist': track.get('artist', '') or '',
                    'album': track.get('album', '') or '',
                    'duration': int(duration or 0),
                    'date_added': date_added,
                    'purchase_date': purchase_date,
                    'play_order': int(track.get('play_order') or 0),
                }
                self.track_tree.tag_configure('playing', background='#e0f0ff')
            except Exception:
                pass

    def _format_track_date(self, dt) -> str:
        try:
            if dt is None:
                return ""
            # plistlibはdatetimeを返すことが多い
            if hasattr(dt, 'strftime'):
                return dt.strftime("%Y-%m-%d")
            return str(dt)
        except Exception:
            return ""

    def sort_track_tree(self, key: str):
        reverse = bool(self._track_tree_sort_reverse.get(key, False))
        self._track_tree_sort_reverse[key] = not reverse

        def _sort_value(iid: str):
            meta = self._track_tree_item_meta.get(iid) or {}
            if key == 'name':
                return (meta.get('name') or '').casefold()
            if key == 'artist':
                return (meta.get('artist') or '').casefold()
            if key == 'album':
                return (meta.get('album') or '').casefold()
            if key == 'time':
                return int(meta.get('duration') or 0)
            if key == 'date_added':
                v = meta.get('date_added')
                return v if v is not None else ''
            if key == 'purchase_date':
                v = meta.get('purchase_date')
                return v if v is not None else ''
            return (meta.get(key) or '')

        items = list(self.track_tree.get_children(''))
        try:
            items.sort(key=_sort_value, reverse=reverse)
        except Exception:
            # 日付/混在型などで比較に失敗した場合は文字列化で再ソート
            items.sort(key=lambda iid: str(_sort_value(iid)), reverse=reverse)

        for idx, iid in enumerate(items):
            try:
                self.track_tree.move(iid, '', idx)
            except Exception:
                pass
    
    def on_track_select(self, event):
        """トラック選択時（ダブルクリック）"""
        try:
            selection = self.track_tree.selection()
            if not selection:
                return
            item = selection[0]
            tags = self.track_tree.item(item, 'tags')
            
            # DBID, Persistent ID, IITObject IDsを取得
            dbid: int | None = None
            pid: str | None = None
            source_id: int | None = None
            playlist_id: int | None = None
            track_id: int | None = None
            play_order: int | None = None
            location: str | None = None

            for tag in tags:
                try:
                    if tag.startswith('dbid:'):
                        v = tag.split(':', 1)[1]
                        dbid = int(v) if v not in (None, '', 'None') else None
                    elif tag.startswith('pid:'):
                        v = tag.split(':', 1)[1]
                        pid = v if v not in (None, '', 'None') else None
                    elif tag.startswith('src:'):
                        v = tag.split(':', 1)[1]
                        source_id = int(v) if v not in (None, '', 'None') else None
                    elif tag.startswith('pl:'):
                        v = tag.split(':', 1)[1]
                        playlist_id = int(v) if v not in (None, '', 'None') else None
                    elif tag.startswith('tid:'):
                        v = tag.split(':', 1)[1]
                        track_id = int(v) if v not in (None, '', 'None') else None
                    elif tag.startswith('po:'):
                        v = tag.split(':', 1)[1]
                        play_order = int(v) if v not in (None, '', 'None') else None
                    elif tag.startswith('loc:'):
                        v = tag.split(':', 1)[1]
                        location = v if v not in (None, '', 'None') else None
                except Exception:
                    continue
            
            track_name = self.track_tree.item(item, 'text') or None
            current_playlist = self._track_loading_playlist
            submitted = False
            if pid or dbid is not None:
                # COM再生呼出し(PlayFirstTrack+sleepを含む)はワーカーへ委譲し、
                # 結果は result_tag 経由でUIへ反映する（UIスレッドをブロックしない）
                self._com_submit(
                    "play_track_by_ids",
                    source_id, playlist_id, track_id, dbid, pid, track_name,
                    current_playlist, play_order,
                    _result_tag=("play_track", track_name),
                )
                submitted = True
            elif location:
                fn = getattr(self.ctrl, 'play_track_by_location', None)
                if callable(fn):
                    self._com_submit(
                        "play_track_by_location", location,
                        _result_tag=("play_track", track_name),
                    )
                    submitted = True

            if submitted:
                self.last_action = f"トラック再生要求: {track_name or '-'}"
            else:
                self.last_action = "トラック再生失敗"
        except Exception as e:
            logger.error("トラック選択エラー: %s", e)

    def _library_xml_cache_valid(self) -> bool:
        """UIスレッド用の軽量チェック: キャッシュが現在のmtimeに一致していれば True。
        plistlib.load は実行しない（解析は必ずバックグラウンドスレッド側）。"""
        path = getattr(self, "_library_xml_path", "")
        if not path:
            return False
        try:
            mtime = float(os.stat(path).st_mtime)
        except Exception:
            return False
        try:
            with self._library_xml_lock:
                return (
                    self._library_xml_tracks is not None
                    and self._library_xml_playlists is not None
                    and self._library_xml_mtime == mtime
                )
        except Exception:
            return False

    def _ensure_library_xml_load_started(self):
        """ライブラリXMLのバックグラウンド読込スレッドを起動する（多重起動防止）。
        キャッシュが既に有効なら何もしない。"""
        if not getattr(self, "_library_xml_path", ""):
            return
        if not getattr(self, "_library_xml_exists", False):
            return
        if self._library_xml_cache_valid():
            return
        t = getattr(self, "_library_xml_loader_thread", None)
        if t is not None and t.is_alive():
            return
        try:
            t = threading.Thread(
                target=self._library_xml_loader_worker,
                daemon=True,
                name="library-xml-loader",
            )
            self._library_xml_loader_thread = t
            t.start()
        except Exception as e:
            logger.error("ライブラリXMLローダー起動失敗: %s", e)
            self._library_xml_loader_thread = None

    def _library_xml_loader_worker(self):
        """バックグラウンドで plistlib.load を実行し、完了をUIキューへ通知する"""
        try:
            ok = self._load_library_xml_if_needed()
        except Exception:
            ok = False
        self._ui_put("library_xml_loaded", ok)

    def _on_library_xml_loaded(self, ok):
        """XML読込完了通知（UIスレッド）。表示ラベルのフォルダパスを追従させる"""
        if self._closing or not ok:
            return
        self._refresh_playlist_labels()

    def _load_library_xml_if_needed(self) -> bool:
        """ライブラリXMLを必要時に読み込み、キャッシュする。成功でTrue。

        注意: plistlib.load は大規模ライブラリでは秒単位かかるため、
        UIスレッドからは呼ばないこと。UI側の確認は _library_xml_cache_valid を使い、
        解析は _stream_playlist_tracks_from_xml_worker / _library_xml_loader_worker
        などのバックグラウンドスレッドでのみ行う。
        """
        path = self._library_xml_path
        if not path:
            return False
        try:
            st = os.stat(path)
            mtime = float(st.st_mtime)
        except Exception:
            return False

        # キャッシュ有効チェックは短いロック区間で行う
        with self._library_xml_lock:
            if (
                self._library_xml_tracks is not None
                and self._library_xml_playlists is not None
                and self._library_xml_mtime == mtime
            ):
                return True

        # plistlib.load はロック外で実行する。
        # ロック保持中に解析すると、キャッシュ確認のための _library_xml_cache_valid
        # （UIスレッド）が解析完了までブロックされてしまうため。
        try:
            with open(path, 'rb') as f:
                data = plistlib.load(f)
        except Exception:
            return False
        tracks = data.get('Tracks') or {}
        playlists = data.get('Playlists') or []
        if not isinstance(tracks, dict) or not isinstance(playlists, list):
            return False

        with self._library_xml_lock:
            # 並行する別ローダーが先に同じ mtime をコミット済みならそれを使う
            if (
                self._library_xml_tracks is not None
                and self._library_xml_playlists is not None
                and self._library_xml_mtime == mtime
            ):
                return True
            # Tracksのキーは文字列のTrack IDが多い
            self._library_xml_tracks = tracks
            self._library_xml_playlists = playlists
            self._library_xml_mtime = mtime
            return True

    def _stream_playlist_tracks_from_xml_worker(
        self,
        playlist_name: str,
        batch_size: int,
        on_batch,
        cancel_event: threading.Event | None,
    ) -> None:
        """XML(plist)からプレイリストのトラック一覧を抽出してバッチで返す（バックグラウンド用）

        plistlib.load を含む重い解析はこのワーカースレッド側で行う。
        失敗時は {XML_ERROR_KEY: メッセージ} のエラーバッチを on_batch で送り、
        UI側の _display_tracks_batch_sync がエラー表示へ回す。
        """
        def _emit_error(message: str) -> None:
            try:
                on_batch([{XML_ERROR_KEY: message}])
            except Exception:
                pass

        try:
            ok = self._load_library_xml_if_needed()
            if not ok:
                _emit_error("ライブラリXMLの読み込みに失敗しました")
                return
            with self._library_xml_lock:
                tracks_dict = self._library_xml_tracks or {}
                playlists = self._library_xml_playlists or []
        except Exception:
            return

        target_pl = None
        for pl in playlists:
            try:
                if pl.get('Name') == playlist_name:
                    target_pl = pl
                    break
            except Exception:
                continue
        if not target_pl:
            _emit_error(f"XMLにプレイリストがありません: {playlist_name}")
            return

        items = target_pl.get('Playlist Items') or []
        if not isinstance(items, list):
            return

        all_tracks: list[dict] = []
        for idx, it in enumerate(items, 1):
            if cancel_event is not None and cancel_event.is_set():
                return
            try:
                tid = it.get('Track ID')
            except Exception:
                tid = None
            if tid is None:
                continue
            t = None
            try:
                t = tracks_dict.get(str(tid))
            except Exception:
                t = None
            if not isinstance(t, dict):
                continue

            total_ms = 0
            try:
                total_ms = int(t.get('Total Time') or 0)
            except Exception:
                total_ms = 0
            duration_sec = max(0, int(round(total_ms / 1000.0)))

            # XMLの Track ID は COMの TrackDatabaseID と一致する
            try:
                dbid = int(tid) if tid is not None else None
            except Exception:
                dbid = None

            track = {
                'name': t.get('Name', ''),
                'artist': t.get('Artist', ''),
                'album': t.get('Album', ''),
                'duration': duration_sec,
                'play_order': idx,
                'dbid': dbid,
                'persistent_id': t.get('Persistent ID'),
                'source_id': None,
                'playlist_id': None,
                'track_id': tid,
                'location': t.get('Location', ''),
                'date_added': t.get('Date Added'),
                'purchase_date': t.get('Purchase Date'),
            }
            all_tracks.append(track)

        if cancel_event is not None and cancel_event.is_set():
            return

        # 追加日降順（新しい順）でソート（iTunes側も追加日降順ソートの前提。低速なCOMの表示順取得は行わない）
        # 同着はXMLのプレイリスト順を維持（安定ソート）、追加日不明は末尾
        dated = [t for t in all_tracks if t.get('date_added') is not None]
        undated = [t for t in all_tracks if t.get('date_added') is None]
        dated.sort(key=lambda x: x.get('date_added'), reverse=True)
        all_tracks = dated + undated
        for pos, tr in enumerate(all_tracks, 1):
            tr['play_order'] = pos

        # バッチ分割して送信
        if batch_size <= 0:
            batch_size_local = len(all_tracks) or 1
        else:
            batch_size_local = batch_size

        for start in range(0, len(all_tracks), batch_size_local):
            if cancel_event is not None and cancel_event.is_set():
                return
            chunk = all_tracks[start:start + batch_size_local]
            try:
                on_batch(chunk)
            except Exception:
                pass
    
    def on_progress_change(self, value):
        """プログレスバー変更時。

        ドラッグ中のモーションイベントごとに同期COM(get_current_track_info /
        set_player_position)を呼ばないよう、after でデバウンスしてから
        COMワーカー経由でシークを実行する。
        """
        if not self._seeking:
            return
        self._seek_pending_value = value
        old_id = getattr(self, "_seek_after_id", None)
        if old_id is not None:
            try:
                self.root.after_cancel(old_id)
            except Exception:
                pass
        self._seek_after_id = self._after(self.SEEK_DEBOUNCE_MS, self._flush_pending_seek)

    def _flush_pending_seek(self):
        """デバウンス済みシークをCOMワーカーへ投入（発火済みコールバックの二重実行は無害）"""
        self._seek_after_id = None
        if self._closing:
            return
        value = getattr(self, "_seek_pending_value", None)
        self._seek_pending_value = None
        if value is None:
            return
        # 曲長はポーリング済みキャッシュから取得（UIスレッドでCOMを呼ばない）
        info = getattr(self, "_latest_track_info", None) or {}
        try:
            duration = float(info.get('duration') or 0)
        except Exception:
            duration = 0.0
        if duration <= 0:
            return
        try:
            position = float(value) * duration / 100.0
        except Exception:
            return
        self._com_submit("set_player_position", position)
    
    # トグルメソッド
    def toggle_progress(self):
        if self.show_progress.get():
            self.progress_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(2, 4))
        else:
            self.progress_frame.grid_remove()
    
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
            self.bpm_frame.grid(row=0, column=1, sticky="e", padx=8, pady=(6, 2))
        else:
            self.bpm_frame.grid_remove()
    
    def toggle_slots(self):
        if self.show_slots.get():
            self.slots_frame.pack(fill=tk.X, pady=(10, 0))
        else:
            self.slots_frame.pack_forget()
    
    def goto_current_track(self):
        """現在再生中のプレイリストとトラックに移動"""
        try:
            self._manual_playlist_view = False
            # まずポーリング済みキャッシュを使い、無ければプラットフォーム共通APIで取得
            info = getattr(self, "_latest_track_info", None) or {}
            playlist_name = info.get('playlist')
            if not playlist_name:
                playlist_name = self.ctrl.get_current_playlist_name()
            if not playlist_name:
                self.last_action = "再生中のプレイリストがありません"
                return

            # プレイリスト一覧から該当プレイリストを選択
            self._select_playlist_in_listbox(playlist_name)

            # トラック一覧を読み込む
            self.load_tracks(playlist_name)

            # 現在のトラックを選択（ポーリング済みキャッシュのみ使用しCOMは呼ばない。
            # 未取得でも読込完了時に _track_loading_dbid 経由でハイライトされる）
            current_dbid = info.get('dbid')
            
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
                            except Exception:
                                pass
            
            self.last_action = f"プレイリストへ移動: {playlist_name}"
        except Exception as e:
            logger.error("再生中の曲へ移動エラー: %s", e)

    def _sync_to_itunes_state(self):
        """iTunesの現在の再生状態をUIに反映する（起動直後用）。

        COMワーカーのポーリング済みキャッシュのみ参照する。未取得なら何もしない
        （次回以降のポーリング結果が _on_track_changed 経由で反映される）。
        """
        if self._closing:
            return
        try:
            info = getattr(self, "_latest_track_info", None)
            if not info:
                return
            cur_dbid = info.get('dbid')
            cur_playlist = info.get('playlist')
            if cur_dbid is None:
                return
            self._synced_dbid = cur_dbid
            self._synced_playlist = cur_playlist
            self._on_track_changed(cur_playlist, cur_dbid)
        except Exception as e:
            logger.error("起動時同期エラー: %s", e)

    def _on_track_changed(self, playlist_name: str | None, dbid: int | None):
        """曲が変わったときにUIのプレイリスト選択とトラックハイライトを更新する"""
        if not playlist_name:
            # プレイリスト不明の場合はトラックハイライトのみ試みる
            if dbid is not None:
                self._highlight_track_by_dbid(dbid)
            return

        # 現在表示中のプレイリストと同じなら、トラックハイライトのみ更新
        if self._track_loading_playlist == playlist_name:
            if dbid is not None:
                self._highlight_track_by_dbid(dbid)
            return

        # 手動選択で別プレイリストを表示中は、再生中プレイリストへの自動追従をしない
        if self._manual_playlist_view:
            return

        # 別のプレイリストに変わった場合: プレイリスト選択を更新してトラック一覧を読み込む
        self._select_playlist_in_listbox(playlist_name)
        self.load_tracks(playlist_name)
        # トラック一覧の読み込み完了後にハイライトするため、dbidを保存
        self._track_loading_dbid = dbid

    def _select_playlist_in_listbox(self, playlist_name: str):
        """プレイリストリストボックスで指定名のプレイリストを選択状態にする"""
        try:
            for i, raw in enumerate(self._playlist_raw_names):
                if raw == playlist_name:
                    self.playlist_listbox.selection_clear(0, tk.END)
                    self.playlist_listbox.selection_set(i)
                    self.playlist_listbox.see(i)
                    return
        except Exception:
            pass

    def _highlight_track_by_dbid(self, dbid: int):
        """トラック一覧で指定DBIDのトラックを選択・ハイライトする"""
        try:
            for item in self.track_tree.get_children():
                tags = self.track_tree.item(item, 'tags')
                for tag in tags:
                    if tag.startswith('dbid:'):
                        try:
                            item_dbid = int(tag.split(':', 1)[1])
                            if item_dbid == dbid:
                                self.track_tree.selection_set(item)
                                self.track_tree.see(item)
                                self.track_tree.focus(item)
                                return
                        except (ValueError, IndexError):
                            pass
        except Exception:
            pass

    def update_ui_loop(self):
        # 終了処理中は更新も再スケジュールも行わずループを終了する
        if self._closing:
            return
        try:
            base_interval = int(self.config.config.refresh_interval * 1000)
        except Exception:
            base_interval = 1000
        next_interval = base_interval
        try:
            # COMの同期呼出し(get_current_track_info)は行わない。
            # COMワーカーが _ui_queue へ積んだポーリング結果を drain して反映する。
            self._drain_ui_queue()

            # BPM表示更新
            if self.bpm_value:
                self.bpm_label.configure(text=f"BPM: {self.bpm_value:.1f}")
            else:
                self.bpm_label.configure(text="BPM: -")
            self.last_action_label.configure(text=f"最終アクション: {self.last_action}")
            self._update_loop_failures = 0
        except Exception as e:
            # 例外が起きてもループを停止させない。連続失敗時は指数バックオフ（上限付き）
            self._update_loop_failures = getattr(self, "_update_loop_failures", 0) + 1
            logger.error("UI更新ループエラー(%d回連続): %s", self._update_loop_failures, e)
            self.last_action = f"UI更新エラー: {e}"
            next_interval = min(
                base_interval * (2 ** (self._update_loop_failures - 1)),
                self.UPDATE_LOOP_MAX_INTERVAL_MS,
            )
        finally:
            # 終了処理中は再スケジュールしない（ループ終了）
            if not self._closing:
                self._after(next_interval, self.update_ui_loop)

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



def run_gui(config: ConfigManager | None = None) -> int:
    """GUIを起動する。終了コード(0=正常)を返す。

    config: main() で生成した ConfigManager。省略時は従来通り config.json を読む。
    コントローラー初期化（iTunes/Music への接続）に失敗した場合は、
    案内メッセージを表示して非0を返す（未処理例外でトレースバックを出さない）。
    """
    cfg = config if config is not None else ConfigManager("config.json")
    root = tk.Tk()
    try:
        ITunesTkApp(root, cfg)
    except Exception as e:
        logger.exception("コントローラーの初期化に失敗しました")
        message = (
            "iTunes / Music に接続できませんでした。\n"
            "iTunes / Music を起動してから、もう一度実行してください。\n\n"
            f"詳細: {e}"
        )
        try:
            messagebox.showerror("iTunes Controller", message, parent=root)
        except Exception:
            pass
        print(f"エラー: {message}", file=sys.stderr)
        try:
            root.destroy()
        except Exception:
            pass
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run_gui())

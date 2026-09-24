"""Issue #43: プレイリスト/トラック一覧の検索フィルタテスト。

- プレイリストペインの検索 Entry は _playlist_raw_names をフィルタし、
  リストボックスの行インデックスは _playlist_visible_names 経由で実名へ解決する
- トラックペインの検索 Entry は曲名/アーティスト/アルバムで絞り込み、
  非一致行は detach で隠す（get_children は表示中の行のみ返すため、
  フィルタ後の順が次/前・自然終了遷移の基準になる）
- Esc/クリアで全件表示へ戻り、プレイリスト切替でトラックフィルタはリセットされる
- 検索 Entry（ttk.Entry）フォーカス中は既存の on_key ガードでホットキー無効

tkinter は conftest.py のフェイクモジュール経由。
ITunesTkApp は __init__ を通さず __new__ + 必要属性の代入で生成する。
"""

import threading
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock

import tkinter.ttk as ttk

from gui_tk import ITunesTkApp


class _Var:
    """StringVar 相当の最小スタブ（trace は張らず手動で _on_*_changed を呼ぶ）"""

    def __init__(self, value: str = ""):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


def _loc(name: str) -> str:
    return f"file://localhost/C:/music/{name}.m4a"


def _make_listbox():
    """delete/insert/curselection/selection_set を実装する playlist_listbox フェイク"""
    lb = MagicMock(name="playlist_listbox")
    items: list = []
    sel: set = set()

    lb.delete.side_effect = lambda *a: (items.clear(), sel.clear())
    lb.insert.side_effect = lambda idx, label: items.append(label)
    lb.curselection.side_effect = lambda: tuple(sorted(sel))
    lb.selection_set.side_effect = lambda i: sel.add(i)
    lb.selection_clear.side_effect = lambda *a: sel.clear()
    lb.size.side_effect = lambda: len(items)
    lb.get = lambda i: items[i]
    return lb, items, sel


def _make_track_tree(rows):
    """detach/reattach/move/delete/insert を実装する track_tree フェイク。

    get_children('') は detach されていない行を現在順で返す。
    rows: [{'dbid':..,'name':..,'artist':..,'album':..,'loc':..,'po':..}, ...]
    """
    tree = MagicMock(name="track_tree")
    order = [f"I{i}" for i in range(len(rows))]
    detached: set = set()
    tagmap: dict = {}
    texts: dict = {}
    valmap: dict = {}
    counter = [len(rows)]

    for iid, r in zip(order, rows):
        tags = [
            f"{k}:{r[k]}"
            for k in ("dbid", "pid", "src", "pl", "tid", "po", "loc")
            if r.get(k) is not None
        ]
        tagmap[iid] = tuple(tags)
        texts[iid] = r.get("name", "")
        valmap[iid] = (r.get("artist", ""), r.get("album", ""), "", "", "")

    def _item(iid, opt=None):
        if opt == "tags":
            return tagmap[iid]
        if opt == "values":
            return valmap[iid]
        return texts[iid]

    def _get_children(parent=""):
        return tuple(i for i in order if i not in detached)

    def _detach(iid):
        if iid in order:
            detached.add(iid)

    def _reattach(iid, parent, pos):
        if iid in order:
            detached.discard(iid)
            order.remove(iid)
            order.insert(min(pos, len(order)), iid)

    def _move(iid, parent, pos):
        if iid in order and iid not in detached:
            order.remove(iid)
            order.insert(min(pos, len(order)), iid)

    def _delete(*iids):
        for i in iids:
            if i in order:
                order.remove(i)
            detached.discard(i)

    def _insert(parent, pos, text="", values=(), tags=()):
        iid = f"I{counter[0]}"
        counter[0] += 1
        order.append(iid)
        tagmap[iid] = tuple(tags or ())
        texts[iid] = text
        valmap[iid] = tuple(values or ())
        return iid

    tree.get_children.side_effect = _get_children
    tree.item.side_effect = _item
    tree.detach.side_effect = _detach
    tree.reattach.side_effect = _reattach
    tree.move.side_effect = _move
    tree.delete.side_effect = _delete
    tree.insert.side_effect = _insert
    return tree, order


def _make_app() -> ITunesTkApp:
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.root = MagicMock(name="root")
    app.root.focus_get.return_value = None
    app._closing = False
    app._modal_open = 0
    app.last_action = ""
    app._action_log = deque(maxlen=50)
    app.action_log_listbox = None
    app._ui_queue = None
    app.engine = MagicMock(name="engine")
    app._playback_playlist = "PL"
    app._track_loading_playlist = "PL"
    app._latest_track_info = {"dbid": 200}
    app._current_track_meta = None
    app._pending_autoplay = None
    app._manual_playlist_view = False
    app._last_playlist_play_at = 0.0
    app._playlist_click_after_id = None
    # 検索フィルタ関連
    app.playlist_search_var = _Var()
    app.track_search_var = _Var()
    app._playlist_filter_query = ""
    app._track_filter_query = ""
    app._track_tree_order = []
    app._track_tree_item_meta = {}
    # プレイリスト一覧
    app._playlist_raw_names = []
    app._playlist_visible_names = []
    app.playlist_listbox, app._lb_items, app._lb_sel = _make_listbox()
    # ライブラリXMLまわり（folder_map は空になる）
    app._library_xml_path = ""
    app._library_xml_exists = False
    app._library_xml_mtime = None
    app._library_xml_tracks = None
    app._library_xml_playlists = None
    app._library_xml_lock = threading.Lock()
    app._library_xml_loader_thread = None
    # トラック読み込み（load_tracks の早期リターン用）
    app._track_loading_thread = None
    app._track_loading_cancel_event = None
    app._track_loading_queue = None
    app._track_load_generation = 0
    app._track_loading_dbid = None
    app.track_tree = MagicMock(name="track_tree")
    app.track_tree.get_children.return_value = ()
    return app


def _track_rows():
    return [
        {"dbid": 100, "name": "Alpha", "artist": "X", "album": "One",
         "loc": _loc("A"), "po": 1},
        {"dbid": 200, "name": "Beta", "artist": "Y", "album": "Two",
         "loc": _loc("B"), "po": 2},
        {"dbid": 300, "name": "Gamma", "artist": "X", "album": "Three",
         "loc": _loc("C"), "po": 3},
    ]


def _set_tracks(app, rows):
    """行フェイクを差し込み、_track_tree_order/_track_tree_item_meta を同期する"""
    tree, order = _make_track_tree(rows)
    app.track_tree = tree
    app._track_tree_order = list(order)
    app._track_tree_item_meta = {
        iid: {
            "name": r.get("name", ""),
            "artist": r.get("artist", ""),
            "album": r.get("album", ""),
            "duration": 0,
        }
        for iid, r in zip(order, rows)
    }
    return list(order)


def _track_dict(name, play_order, artist="a", album="al", dbid=None, loc=""):
    return {
        "name": name,
        "artist": artist,
        "album": album,
        "duration": 0,
        "play_order": play_order,
        "dbid": dbid if dbid is not None else play_order,
        "persistent_id": None,
        "source_id": None,
        "playlist_id": None,
        "track_id": play_order,
        "location": loc,
        "date_added": None,
        "purchase_date": None,
    }


class TestPlaylistFilter:
    def test_filter_narrows_visible_names(self):
        app = _make_app()
        app._playlist_raw_names = ["Rock Mix", "Jazz", "Pop Rock"]
        app._playlist_visible_names = list(app._playlist_raw_names)

        app.playlist_search_var.set("rock")
        app._on_playlist_search_changed()

        assert app._playlist_visible_names == ["Rock Mix", "Pop Rock"]
        assert app._lb_items == ["Rock Mix", "Pop Rock"]

    def test_filter_is_case_insensitive(self):
        app = _make_app()
        app._playlist_raw_names = ["ROCK", "jazz"]

        app.playlist_search_var.set("Rock")
        app._on_playlist_search_changed()

        assert app._playlist_visible_names == ["ROCK"]

    def test_empty_query_shows_all(self):
        app = _make_app()
        app._playlist_raw_names = ["A", "B", "C"]

        app.playlist_search_var.set("b")
        app._on_playlist_search_changed()
        app.playlist_search_var.set("")
        app._on_playlist_search_changed()

        assert app._playlist_visible_names == ["A", "B", "C"]
        assert app._lb_items == ["A", "B", "C"]

    def test_escape_clears_filter(self):
        app = _make_app()
        app._playlist_raw_names = ["Rock", "Jazz"]

        app.playlist_search_var.set("rock")
        app._on_playlist_search_changed()
        assert app._lb_items == ["Rock"]

        assert app._on_playlist_search_escape() == "break"
        assert app.playlist_search_var.get() == ""
        assert app._playlist_visible_names == ["Rock", "Jazz"]
        assert app._lb_items == ["Rock", "Jazz"]

    def test_apply_playlist_list_populates_visible_names(self):
        app = _make_app()

        app._apply_playlist_list([{"name": "A"}, {"name": "B"}])

        assert app._playlist_raw_names == ["A", "B"]
        assert app._playlist_visible_names == ["A", "B"]
        assert app._lb_items == ["A", "B"]

    def test_apply_playlist_list_keeps_active_filter(self):
        app = _make_app()
        app._playlist_filter_query = "rock"

        app._apply_playlist_list([{"name": "Rock"}, {"name": "Jazz"}])

        assert app._playlist_raw_names == ["Rock", "Jazz"]
        assert app._playlist_visible_names == ["Rock"]
        assert app._lb_items == ["Rock"]

    def test_selection_index_maps_through_filter_on_double_click(self):
        """フィルタ適用中の行インデックスはフィルタ後の実名へ解決される"""
        app = _make_app()
        app._playlist_raw_names = ["Jazz", "Rock", "Pop Rock"]
        app.playlist_search_var.set("rock")
        app._on_playlist_search_changed()
        app._lb_sel.add(1)  # 表示上2行目 = "Pop Rock"
        app.load_tracks = MagicMock(name="load_tracks")

        app.on_playlist_select(None)

        app.load_tracks.assert_called_once_with("Pop Rock")

    def test_selection_index_maps_through_filter_on_single_click(self):
        app = _make_app()
        app._playlist_raw_names = ["Jazz", "Rock", "Pop Rock"]
        app.playlist_search_var.set("rock")
        app._on_playlist_search_changed()
        app._lb_sel.add(0)  # 表示上1行目 = "Rock"
        app.load_tracks = MagicMock(name="load_tracks")

        app._show_selected_playlist_tracks()

        app.load_tracks.assert_called_once_with("Rock")

    def test_select_playlist_in_listbox_uses_visible_names(self):
        """_select_playlist_in_listbox はフィルタ後の表示行インデックスで選択する"""
        app = _make_app()
        app._playlist_raw_names = ["Jazz", "Rock", "Pop Rock"]
        app.playlist_search_var.set("rock")
        app._on_playlist_search_changed()

        app._select_playlist_in_listbox("Pop Rock")

        assert app._lb_sel == {1}

    def test_filter_preserves_selection_by_name(self):
        """再構築時に同じ実名が残っていれば選択を維持する"""
        app = _make_app()
        app._playlist_raw_names = ["Rock", "Jazz"]
        app._on_playlist_search_changed()
        app._lb_sel.add(1)  # "Jazz" を選択

        app.playlist_search_var.set("j")  # "Jazz" のみ残る
        app._on_playlist_search_changed()

        assert app._lb_items == ["Jazz"]
        assert app._lb_sel == {0}


class TestTrackFilter:
    def test_filter_by_name(self):
        app = _make_app()
        _set_tracks(app, _track_rows())

        app.track_search_var.set("beta")
        app._on_track_search_changed()

        assert app.track_tree.get_children("") == ("I1",)

    def test_filter_matches_artist_and_album(self):
        app = _make_app()
        _set_tracks(app, _track_rows())

        app.track_search_var.set("x")  # artist X → Alpha/Gamma
        app._on_track_search_changed()
        assert app.track_tree.get_children("") == ("I0", "I2")

        app.track_search_var.set("three")  # album Three → Gamma
        app._on_track_search_changed()
        assert app.track_tree.get_children("") == ("I2",)

    def test_clear_restores_all_rows_in_order(self):
        app = _make_app()
        _set_tracks(app, _track_rows())

        app.track_search_var.set("gamma")
        app._on_track_search_changed()
        assert app.track_tree.get_children("") == ("I2",)

        app._clear_track_filter()
        assert app.track_search_var.get() == ""
        assert app.track_tree.get_children("") == ("I0", "I1", "I2")

    def test_escape_clears_filter(self):
        app = _make_app()
        _set_tracks(app, _track_rows())

        app.track_search_var.set("beta")
        app._on_track_search_changed()

        assert app._on_track_search_escape() == "break"
        assert app.track_tree.get_children("") == ("I0", "I1", "I2")

    def test_next_track_follows_filtered_order(self):
        """フィルタ後の表示順が「次の曲」の基準になる"""
        app = _make_app()
        _set_tracks(app, _track_rows())

        app.track_search_var.set("x")  # Alpha(dbid100), Gamma(dbid300) が残る
        app._on_track_search_changed()
        app._latest_track_info = {"dbid": 100}

        app.next_track()

        app.engine.play.assert_called_once()
        assert "C.m4a" in app.engine.play.call_args[0][0]
        assert app._current_track_meta["dbid"] == 300

    def test_next_track_stops_at_filtered_list_end(self):
        """フィルタ後の末尾で次 → 何もしない（隠れた行へは進まない）"""
        app = _make_app()
        _set_tracks(app, _track_rows())

        app.track_search_var.set("x")  # Alpha, Gamma が残る
        app._on_track_search_changed()
        app._latest_track_info = {"dbid": 300}

        app.next_track()

        app.engine.play.assert_not_called()

    def test_natural_end_follows_filtered_order(self):
        """自然終了時の次行遷移もフィルタ後の表示順に従う"""
        app = _make_app()
        _set_tracks(app, _track_rows())

        app.track_search_var.set("x")  # Alpha, Gamma が残る
        app._on_track_search_changed()

        assert app._play_next_row_after(100) is True
        assert "C.m4a" in app.engine.play.call_args[0][0]

    def test_current_track_filtered_out_is_noop(self):
        """再生中の行がフィルタで隠れている場合は遷移しない"""
        app = _make_app()
        _set_tracks(app, _track_rows())

        app.track_search_var.set("gamma")  # Gamma のみ残る（Beta=dbid200 は隠れる）
        app._on_track_search_changed()
        app._latest_track_info = {"dbid": 200}

        app.next_track()

        app.engine.play.assert_not_called()

    def test_new_batch_rows_respect_active_filter(self):
        """フィルタ適用中に届いた追加バッチも絞り込み対象になる"""
        app = _make_app()
        _set_tracks(app, _track_rows())

        app.track_search_var.set("alpha")
        app._on_track_search_changed()
        assert app.track_tree.get_children("") == ("I0",)

        app._display_tracks_batch_sync(
            [
                _track_dict("Alpha Two", 4, dbid=400, loc=_loc("D")),
                _track_dict("Zed", 5, dbid=500, loc=_loc("E")),
            ],
            None,
        )

        visible = app.track_tree.get_children("")
        assert len(visible) == 2
        assert "I0" in visible
        assert "I3" in visible  # "Alpha Two" は一致
        # "Zed" は非表示
        assert len(visible) == 2

    def test_load_tracks_resets_track_filter(self):
        """フィルタ適用中にプレイリストを切り替えたらフィルタをリセットする"""
        app = _make_app()
        _set_tracks(app, _track_rows())
        app.track_search_var.set("beta")
        app._on_track_search_changed()
        app._show_error = MagicMock(name="_show_error")

        app.load_tracks("OtherPL")

        assert app.track_search_var.get() == ""
        assert app._track_filter_query == ""
        assert app._track_tree_order == []
        assert app.track_tree.get_children("") == ()

    def test_load_tracks_without_search_var_still_works(self):
        """track_search_var 未注入の古いテスト用インスタンスでも load_tracks が動く"""
        app = _make_app()
        del app.track_search_var
        app._show_error = MagicMock(name="_show_error")

        app.load_tracks("PL")

        assert app._track_filter_query == ""


class TestSearchEntryKeyGuard:
    def test_hotkeys_disabled_while_search_entry_focused(self):
        """検索 Entry（ttk.Entry）フォーカス中は既存ガードでホットキー無効"""
        app = _make_app()
        app.root.focus_get.return_value = ttk.Entry()
        app.reset_bpm = MagicMock(name="reset_bpm")

        assert app.on_key(SimpleNamespace(keysym="x", state=0)) is None
        app.reset_bpm.assert_not_called()

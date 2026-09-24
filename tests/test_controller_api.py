"""コントローラー公開APIの存在確認と、削除した再生系APIの不在確認。

Issue #39 以降、コントローラーはプレイリスト管理（列挙・作成・トラック追加）のみを担う。
再生系メソッドは playback_engine（ローカル再生）に移譲され、コントローラーからは削除済み。
"""

import ast
import inspect

import pytest

import music_controller_windows
from music_controller_base import MacOSMusicController
from music_controller_windows import WindowsMusicController

# gui_tk が両プラットフォーム共通で呼ぶ公開メソッド
COMMON_REQUIRED_METHODS = [
    "get_playlists",
    "get_all_playlists",
    "add_to_playlist",
    "create_playlist",
    "create_worker_controller",
    "close",
]

# Issue #39 で削除された再生系メソッド（両プラットフォーム）
PLAYBACK_REMOVED_METHODS = [
    "get_current_track_info",
    "get_current_playlist_name",
    "play_pause",
    "play_next_track",
    "play_previous_track",
    "skip_forward",
    "skip_backward",
    "get_volume",
    "set_volume",
    "set_current_track_bpm",
]

# Issue #39 で Windows 側から削除された再生系メソッド
WINDOWS_REMOVED_PLAYBACK_METHODS = [
    "set_player_position",
    "play_playlist",
    "play_track_by_ids",
    "play_track_by_location",
    "_play_adjacent_track",
    "_play_playlist_neighbor",
    "_find_playlist_track_index",
]

# Issue #8 で削除対象となったデッドコード
WINDOWS_REMOVED_METHODS = [
    "stream_playlist_tracks_threadsafe",
    "get_current_playlist_tracks",
    "get_playlist_tracks",
    "get_playlists_of_current_track",
    "get_playlists_of_current_track_threadsafe",
    "get_play_order_mapping_threadsafe",
]

MACOS_REMOVED_METHODS = [
    "get_playlist_dbids_threadsafe",
    "get_all_playlists_threadsafe",
    "get_play_order_mapping_threadsafe",
    "get_playlists_of_current_track_from_cache",
    "is_playlist_cache_fresh",
]


@pytest.mark.parametrize("cls", [WindowsMusicController, MacOSMusicController])
@pytest.mark.parametrize("method", COMMON_REQUIRED_METHODS)
def test_controller_exposes_common_gui_api(cls, method):
    assert callable(getattr(cls, method, None)), (
        f"{cls.__name__}.{method} が存在しない（gui_tk が依存）"
    )


@pytest.mark.parametrize("cls", [WindowsMusicController, MacOSMusicController])
@pytest.mark.parametrize("method", PLAYBACK_REMOVED_METHODS)
def test_playback_methods_removed(cls, method):
    """再生は playback_engine に移譲済みのためコントローラーに存在しないこと"""
    assert not hasattr(cls, method), (
        f"{cls.__name__}.{method} は再生エンジン移譲のため削除されているはず"
    )


@pytest.mark.parametrize("method", WINDOWS_REMOVED_PLAYBACK_METHODS)
def test_windows_playback_methods_removed(method):
    assert not hasattr(WindowsMusicController, method), (
        f"WindowsMusicController.{method} は再生エンジン移譲のため削除されているはず"
    )


@pytest.mark.parametrize("method", WINDOWS_REMOVED_METHODS)
def test_windows_dead_methods_removed(method):
    assert not hasattr(WindowsMusicController, method), (
        f"WindowsMusicController.{method} は未使用のため削除されているはず"
    )


@pytest.mark.parametrize("method", MACOS_REMOVED_METHODS)
def test_macos_dead_methods_removed(method):
    assert not hasattr(MacOSMusicController, method), (
        f"MacOSMusicController.{method} は未使用のため削除されているはず"
    )


def test_windows_init_has_no_current_track_field():
    src = inspect.getsource(WindowsMusicController.__init__)
    assert "self.current_track" not in src


def test_windows_controller_has_no_print_calls():
    """DEBUG/エラー出力は print ではなく logging に集約されていること"""
    tree = ast.parse(inspect.getsource(music_controller_windows))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "print", (
                f"print() が残っている: line {node.lineno}"
            )


def test_windows_controller_uses_logging():
    src = inspect.getsource(music_controller_windows)
    assert "import logging" in src
    assert "logging.getLogger(__name__)" in src

"""コントローラー公開APIの存在確認と、削除したデッドコードの不在確認。

gui_tk が呼ぶメソッド群は削除後も維持される必要がある。
インスタンス化は __init__ が COM/AppleScript へ接続するため行わず、
クラス属性としての存在だけを検証する。
"""

import ast
import inspect

import pytest

import music_controller_windows
from music_controller_base import MacOSMusicController
from music_controller_windows import WindowsMusicController

# gui_tk が両プラットフォーム共通で呼ぶ公開メソッド
COMMON_REQUIRED_METHODS = [
    "get_current_track_info",
    "play_pause",
    "play_next_track",
    "play_previous_track",
    "skip_forward",
    "skip_backward",
    "get_playlists",
    "get_all_playlists",
    "add_to_playlist",
    "create_playlist",
    "set_current_track_bpm",
]

# gui_tk が呼ぶが現状 Windows 側にのみ実装があるメソッド
# (macOS 側は hasattr ガードや try/except で吸収されている)
WINDOWS_REQUIRED_METHODS = [
    "set_player_position",
    "play_playlist",
    "play_track_by_ids",
    "play_track_by_location",
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


@pytest.mark.parametrize("method", WINDOWS_REQUIRED_METHODS)
def test_windows_controller_exposes_windows_api(method):
    assert callable(getattr(WindowsMusicController, method, None)), (
        f"WindowsMusicController.{method} が存在しない（gui_tk が依存）"
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

"""Issue #39: LocalPlaybackEngine（libmpv ラッパー）のテスト。

mpv モジュールはフェイクを sys.modules に注入して検証する
（CI の macOS/Linux 環境には libmpv が無いため）。
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

import playback_engine
from playback_engine import LocalPlaybackEngine, PlaybackUnavailableError, itunes_location_to_path


class _FakePlayer:
    def __init__(self):
        self.calls = []
        self.pause = False
        self.time_pos = 12.5
        self.duration = 200.0
        self.idle_active = False
        self.volume = 50.0
        self._end_file_cb = None

    def play(self, path):
        self.calls.append(("play", path))

    def seek(self, amount, reference="relative", precision="default-precise"):
        self.calls.append(("seek", amount, reference, precision))

    def event_callback(self, *event_types):
        def deco(fn):
            self._end_file_cb = fn
            return fn
        return deco

    def terminate(self):
        self.calls.append(("terminate",))


def _make_engine():
    fake_mpv = types.ModuleType("mpv")
    player = _FakePlayer()
    fake_mpv.MPV = MagicMock(return_value=player)
    sys.modules["mpv"] = fake_mpv
    engine = LocalPlaybackEngine()
    return engine, player


@pytest.fixture(autouse=True)
def _cleanup_mpv_module():
    yield
    sys.modules.pop("mpv", None)


class TestInit:
    def test_missing_mpv_module_raises(self):
        sys.modules["mpv"] = None  # import error を起こさせる
        with pytest.raises(PlaybackUnavailableError):
            LocalPlaybackEngine()

    def test_mpv_init_failure_raises(self):
        fake_mpv = types.ModuleType("mpv")
        fake_mpv.MPV = MagicMock(side_effect=OSError("libmpv-2.dll not found"))
        sys.modules["mpv"] = fake_mpv
        with pytest.raises(PlaybackUnavailableError):
            LocalPlaybackEngine()


class TestControls:
    def test_play(self):
        engine, player = _make_engine()
        engine.play(r"C:\Music\a.m4a")
        assert ("play", r"C:\Music\a.m4a") in player.calls

    def test_toggle_pause(self):
        engine, player = _make_engine()
        engine.toggle_pause()
        assert player.pause is True
        engine.toggle_pause()
        assert player.pause is False

    def test_seek_abs(self):
        engine, player = _make_engine()
        engine.seek_abs(42.0)
        assert ("seek", 42.0, "absolute", "exact") in player.calls

    def test_seek_rel(self):
        engine, player = _make_engine()
        engine.seek_rel(-10)
        assert ("seek", -10.0, "relative", "exact") in player.calls

    def test_set_volume_clips(self):
        engine, player = _make_engine()
        engine.set_volume(150)
        assert player.volume == 100
        engine.set_volume(-5)
        assert player.volume == 0
        engine.set_volume(62)
        assert player.volume == 62

    def test_calls_after_close_raise(self):
        engine, player = _make_engine()
        engine.close()
        with pytest.raises(PlaybackUnavailableError):
            engine.play("x")


class TestState:
    def test_get_state_playing(self):
        engine, player = _make_engine()
        st = engine.get_state()
        assert st["position"] == 12.5
        assert st["duration"] == 200.0
        assert st["is_playing"] is True
        assert st["volume"] == 50.0

    def test_get_state_paused(self):
        engine, player = _make_engine()
        player.pause = True
        assert engine.get_state()["is_playing"] is False

    def test_get_state_idle(self):
        engine, player = _make_engine()
        player.idle_active = True
        assert engine.get_state()["is_playing"] is False

    def test_get_state_closed(self):
        engine, player = _make_engine()
        engine.close()
        assert engine.get_state()["is_playing"] is False


class TestEndFile:
    def _fire(self, player, reason, error=0):
        data = types.SimpleNamespace(reason=reason, error=error)
        event = types.SimpleNamespace(data=data)
        player._end_file_cb(event)

    def test_eof_forwarded(self):
        engine, player = _make_engine()
        got = []
        engine.on_end_file(lambda r, e: got.append((r, e)))
        self._fire(player, 0)
        assert got == [("eof", 0)]

    def test_error_forwarded(self):
        engine, player = _make_engine()
        got = []
        engine.on_end_file(lambda r, e: got.append((r, e)))
        self._fire(player, 4, error=-36)
        assert got == [("error", -36)]

    def test_aborted_forwarded_as_stop_family(self):
        engine, player = _make_engine()
        got = []
        engine.on_end_file(lambda r, e: got.append((r, e)))
        self._fire(player, 2)
        assert got == [("stop", 0)]

    def test_callback_exception_does_not_propagate(self):
        engine, player = _make_engine()
        engine.on_end_file(lambda r, e: 1 / 0)
        self._fire(player, 0)  # 例外は飲み込まれる

    def test_no_callback_after_close(self):
        engine, player = _make_engine()
        got = []
        engine.on_end_file(lambda r, e: got.append(r))
        engine.close()
        self._fire(player, 0)
        assert got == []


class TestLocationConversion:
    def test_file_url_to_windows_path(self, monkeypatch):
        monkeypatch.setattr(playback_engine.os, "name", "nt")
        url = "file://localhost/C:/Users/me/Music/a%20b.m4a"
        assert itunes_location_to_path(url) == "C:\\Users\\me\\Music\\a b.m4a"

    def test_file_url_to_posix_path(self, monkeypatch):
        monkeypatch.setattr(playback_engine.os, "name", "posix")
        url = "file://localhost/Users/me/Music/a%20b.m4a"
        assert itunes_location_to_path(url) == "/Users/me/Music/a b.m4a"

    def test_empty_and_nonfile(self):
        assert itunes_location_to_path(None) is None
        assert itunes_location_to_path("") is None
        assert itunes_location_to_path("https://example.com/x") is None

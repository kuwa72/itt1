"""ローカル再生エンジン（libmpv 経由）。

iTunes COM を介さず楽曲ファイルを直接デコードして再生する（Issue #39）。
mpv モジュールと libmpv-2.dll は実行時にのみ必要（import は __init__ で遅延実行
するため、テスト環境や macOS でもこのモジュール自体は import できる）。

スレッドモデル:
- 全メソッドは呼出しスレッド（GUI の UIスレッド）から呼ぶ。mpv の内部スレッドが
  デコード・出力を行うため呼出しはブロックしない。
- end-file イベントは mpv のイベントスレッドで発火する。on_end_file に渡す
  コールバック内では Tk 等の UI オブジェクトを直接触らず、キュー経由で
  UI スレッドへ転送すること。
"""

import logging
import os
import urllib.parse

logger = logging.getLogger(__name__)

# mpv end-file reason codes（mpv_end_file_reason）
_END_FILE_REASONS = {
    0: "eof",
    1: "restarted",
    2: "stop",
    3: "quit",
    4: "error",
    5: "redirect",
}


class PlaybackUnavailableError(RuntimeError):
    """mpv モジュールまたは libmpv が利用できない場合に送出される。"""


def itunes_location_to_path(location: str | None) -> str | None:
    """iTunes XML の Location (file://localhost/C:/...) をローカルパスに変換する。
    file:// URL でない・空・デコード不能な場合は None。"""
    if not location:
        return None
    try:
        parsed = urllib.parse.urlparse(location)
        if parsed.scheme != "file":
            return None
        path = urllib.parse.unquote(parsed.path)
        if os.name == "nt":
            # "/C:/..." → "C:/..." → "C:\..."
            if path.startswith("/") and len(path) > 2 and path[2] == ":":
                path = path[1:]
            path = path.replace("/", "\\")
        return path
    except Exception:
        return None


class LocalPlaybackEngine:
    """libmpv ラッパー。GUI の再生操作の全てを担う。"""

    def __init__(self):
        try:
            import mpv
        except Exception as e:
            raise PlaybackUnavailableError(f"mpv モジュールを読み込めません: {e}") from e
        try:
            self._player = mpv.MPV(video=False, terminal=False)
        except Exception as e:
            raise PlaybackUnavailableError(f"libmpv の初期化に失敗しました: {e}") from e
        self._closed = False

    # --- 再生制御 ---

    def play(self, path: str) -> None:
        self._check()
        self._player.play(path)

    def toggle_pause(self) -> None:
        self._check()
        self._player.pause = not self._player.pause

    def seek_abs(self, seconds: float) -> None:
        self._check()
        self._player.seek(float(seconds), reference="absolute", precision="exact")

    def seek_rel(self, seconds: float) -> None:
        self._check()
        self._player.seek(float(seconds), reference="relative", precision="exact")

    def set_volume(self, level: int) -> None:
        self._check()
        self._player.volume = max(0, min(100, int(round(level))))

    # --- 状態取得（ポーリング用。mpv プロパティ読みはプロセス内呼出しで軽い） ---

    def get_state(self) -> dict:
        if self._closed:
            return {"position": 0.0, "duration": 0.0, "is_playing": False, "volume": None}
        try:
            pos = self._player.time_pos
            dur = self._player.duration
            idle = self._player.idle_active
            paused = bool(self._player.pause)
            vol = self._player.volume
            return {
                "position": float(pos) if pos is not None else 0.0,
                "duration": float(dur) if dur is not None else 0.0,
                "is_playing": (not idle) and (not paused),
                "volume": float(vol) if vol is not None else None,
            }
        except Exception as e:
            logger.error("再生状態取得失敗: %s", e)
            return {"position": 0.0, "duration": 0.0, "is_playing": False, "volume": None}

    # --- イベント ---

    def on_end_file(self, callback) -> None:
        """end-file イベントを callback(reason: str, error: int | None) で通知する。

        reason は "eof" / "error" / "stop" / "quit" / "restarted" / "redirect" /
        "unknown" のいずれか。コールバックは mpv のイベントスレッドで呼ばれる。
        """
        self._check()

        @self._player.event_callback("end-file")
        def _handler(event):
            if self._closed:
                return
            reason, error = self._parse_end_file(event)
            try:
                callback(reason, error)
            except Exception as e:
                logger.error("end-file コールバックエラー: %s", e)

    @staticmethod
    def _parse_end_file(event) -> tuple[str, int | None]:
        """python-mpv の end-file イベントから (reason名, error) を取り出す。

        event.data は MpvEventEndFile ctypes.Structure（reason/error は c_int）。
        形状差異に備えて dict/属性/整数のいずれにも対応する。
        """
        reason = None
        error = None
        try:
            data = getattr(event, "data", None)
            if data is None and isinstance(event, dict):
                data = event.get("event") or event.get("data")
            if isinstance(data, dict):
                reason = data.get("reason")
                error = data.get("error")
            elif data is not None:
                reason = getattr(data, "reason", None)
                error = getattr(data, "error", None)
        except Exception:
            pass
        if isinstance(reason, str):
            rname = reason
        elif isinstance(reason, int):
            rname = _END_FILE_REASONS.get(reason, "unknown")
        else:
            rname = "unknown"
        return rname, error

    # --- 終了 ---

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._player.terminate()
        except Exception:
            pass
        self._player = None

    def _check(self) -> None:
        if self._closed or self._player is None:
            raise PlaybackUnavailableError("再生エンジンは停止しています")

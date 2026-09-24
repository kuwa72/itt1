"""
Music App Controller Abstraction Layer
Cross-platform abstraction for iTunes (Windows) and Music (macOS) control

再生はローカル再生エンジン（playback_engine）が担うため、
コントローラーはプレイリストの列挙・作成・トラック追加のみを扱う（Issue #39）。
"""

import platform
import subprocess
import json
from typing import Dict, List, Any


def detect_platform() -> str:
    """Detect the current platform"""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "darwin":
        return "macos"
    else:
        raise NotImplementedError(f"Unsupported platform: {system}")

def create_music_controller():
    """Factory function to create appropriate controller for current platform.
    Returns an object exposing the same API as the legacy iTunesController used by gui_tk.
    """
    platform_name = detect_platform()

    if platform_name == "windows":
        from music_controller_windows import WindowsMusicController
        return WindowsMusicController()
    elif platform_name == "macos":
        return MacOSMusicController()
    else:
        raise NotImplementedError(f"No controller available for platform: {platform_name}")


class MacOSMusicController:
    """macOS Music app controller using AppleScript"""

    def _run_applescript(self, script: str) -> str:
        """Execute AppleScript and return result"""
        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(f"AppleScript failed: {result.stderr}")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise RuntimeError("AppleScript timeout")
        except FileNotFoundError:
            raise RuntimeError("osascript not found")

    def _run_javascript(self, js_code: str) -> Dict[str, Any]:
        """Execute JXA (JavaScript for Automation) and return parsed JSON"""
        try:
            result = subprocess.run(
                ['osascript', '-l', 'JavaScript', '-e', js_code],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(f"JXA failed: {result.stderr}")
            return json.loads(result.stdout.strip())
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            raise RuntimeError(f"JXA execution failed: {e}")

    def get_playlists(self) -> List[Dict[str, str]]:
        """Get all playlists (name only for now)"""
        script = '''
        tell application "Music"
            set playlists_list to {}
            repeat with pl in (get playlists)
                set end of playlists_list to {name:(get name of pl)}
            end repeat
            return playlists_list
        end tell
        '''
        result = self._run_applescript(script)
        # Parse AppleScript list format: "name:ライブラリ, name:ミュージック"
        import re
        names = re.findall(r'name:([^,]+?)(?:,|$)', result)
        return [{'name': name.strip()} for name in names]

    def get_all_playlists(self) -> List[str]:
        return [p['name'] for p in self.get_playlists()]

    def add_to_playlist(
        self,
        playlist_name: str,
        database_id: int | None = None,
        track_name: str | None = None,
    ) -> str | bool:
        """Add a library track (by database ID) to playlist.

        再生はローカルエンジンが担うため「現在のトラック」は Music アプリ側ではなく
        コントローラー側の dbid で特定する（Issue #39）。
        Returns: "added", "already_exists", or False
        """
        if not isinstance(database_id, int):
            return False
        script = f'''
        tell application "Music"
            try
                set target_playlist to user playlist "{playlist_name}"
                set the_track to first track of library playlist 1 whose database ID is {database_id}

                set track_exists to false
                repeat with t in (get tracks of target_playlist)
                    if database ID of t = {database_id} then
                        set track_exists to true
                        exit repeat
                    end if
                end repeat

                if not track_exists then
                    duplicate the_track to target_playlist
                    return "added"
                else
                    return "already_exists"
                end if
            on error errMsg
                return "error"
            end try
        end tell
        '''
        try:
            result = self._run_applescript(script)
            if result in ("added", "already_exists"):
                return result
            return False
        except RuntimeError:
            return False

    def create_worker_controller(self) -> "MacOSMusicController":
        """ワーカースレッド用コントローラー。AppleScript(subprocess)ベースで
        スレッド間共有に問題がないため自身を返す（close 不要）。"""
        return self

    def close(self) -> None:
        """終了処理。AppleScript ベースで解放すべきリソースは無いため no-op。"""
        return None

    def create_playlist(self, name: str) -> bool:
        script = f'tell application "Music" to make new user playlist with properties {{name:\"{name}\"}}'
        try:
            self._run_applescript(script)
            return True
        except Exception:
            return False

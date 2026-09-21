"""
Music App Controller Abstraction Layer
Cross-platform abstraction for iTunes (Windows) and Music (macOS) control
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

    # --- Methods compatible with gui_tk's expectations ---
    def get_current_track_info(self) -> Dict[str, Any]:
        """Get current track info using JXA for better performance"""
        js = '''
        (function() {
            const Music = Application('Music');
            const props = { is_playing: false, name: '-', artist: '-', album: '-' };
            if (!Music.running()) return JSON.stringify(props);

            const state = Music.playerState();
            props.is_playing = state === 'playing';

            if (state === 'playing' || state === 'paused') {
                const track = Music.currentTrack();
                props.name = track.name() || '-';
                props.artist = track.artist() || '-';
                props.album = track.album() || '-';
                props.duration = track.duration() || 0;
                props.position = Music.playerPosition();
                props.database_id = track.databaseID();
            }
            return JSON.stringify(props);
        })()
        '''
        return self._run_javascript(js)

    def play_pause(self):
        self._run_applescript('tell application "Music" to playpause')

    def play_next_track(self):
        self._run_applescript('tell application "Music" to next track')

    def play_previous_track(self):
        self._run_applescript('tell application "Music" to previous track')

    def set_position(self, seconds: float) -> None:
        script = f'tell application "Music" to set player position to {seconds}'
        self._run_applescript(script)

    def skip_forward(self, seconds: int = 10):
        info = self.get_current_track_info() or {}
        pos = int(info.get('position') or 0) + int(seconds)
        self.set_position(max(0, pos))
    def skip_backward(self, seconds: int = 10):
        info = self.get_current_track_info() or {}
        pos = int(info.get('position') or 0) - int(seconds)
        self.set_position(max(0, pos))

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

    def add_to_playlist(self, playlist_name: str) -> str | bool:
        """Add current track to playlist. Returns: "added", "already_exists", or False"""
        script = f'''
        tell application "Music"
            try
                set target_playlist to user playlist "{playlist_name}"
                set current_track to current track

                -- Check if already in playlist by database ID
                set track_id to database ID of current_track
                set track_exists to false
                repeat with t in (get tracks of target_playlist)
                    if database ID of t = track_id then
                        set track_exists to true
                        exit repeat
                    end if
                end repeat

                if not track_exists then
                    duplicate current_track to target_playlist
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

    def get_current_playlist_name(self) -> str | None:
        """現在再生中のプレイリスト名を返す。未再生/取得不可の場合は None"""
        try:
            name = self._run_applescript(
                'tell application "Music" to get name of current playlist'
            )
            return name or None
        except RuntimeError:
            return None

    def create_worker_controller(self) -> "MacOSMusicController":
        """ワーカースレッド用コントローラー。AppleScript(subprocess)ベースで
        スレッド間共有に問題がないため自身を返す（close 不要）。"""
        return self

    def set_current_track_bpm(self, bpm: int) -> bool:
        """Set BPM for current track"""
        script = f'tell application "Music" to set bpm of current track to {bpm}'
        try:
            self._run_applescript(script)
            return True
        except RuntimeError:
            return False

    # The following APIs keep GUI compatibility
    def create_playlist(self, name: str) -> bool:
        script = f'tell application "Music" to make new user playlist with properties {{name:\"{name}\"}}'
        try:
            self._run_applescript(script)
            return True
        except Exception:
            return False

"""
Music App Controller Abstraction Layer
Cross-platform abstraction for iTunes (Windows) and Music (macOS) control
"""

import platform
import subprocess
import json
from typing import Dict, List, Set, Any
import threading
import time


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

    def __init__(self):
        self._cache_lock = threading.Lock()
        self._playlist_cache: Dict[str, tuple[float, Set[int]]] = {}
        self._cache_ttl_sec: float = 300.0
        self._recent_sig_cache: Dict[tuple, tuple[float, Set[str]]] = {}
        self._recent_sig_ttl_sec: float = 300.0

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

    def add_to_playlist(self, playlist_name: str) -> bool:
        """Add current track to playlist"""
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
            return result in ["added", "already_exists"]
        except RuntimeError:
            return False

    def set_current_track_bpm(self, bpm: int) -> bool:
        """Set BPM for current track"""
        script = f'tell application "Music" to set bpm of current track to {bpm}'
        try:
            self._run_applescript(script)
            return True
        except RuntimeError:
            return False

    # Cache management methods (simplified for macOS)
    def get_playlists_of_current_track_from_cache(self) -> List[str]:
        """Get playlists containing current track from cache"""
        # Basic cache-only check using database_id and recent signatures
        names: List[str] = []
        try:
            info = self.get_current_track_info() or {}
            dbid = info.get('database_id')
            sig = (info.get('name','-'), info.get('artist','-'), info.get('album','-'), int(info.get('duration') or 0))
            with self._cache_lock:
                ent = self._recent_sig_cache.get(sig)
                if ent and (time.time() - ent[0]) <= self._recent_sig_ttl_sec:
                    names.extend([n for n in ent[1] if n not in names])
                if isinstance(dbid, int):
                    for pl_name, (ts, dbids) in self._playlist_cache.items():
                        if (time.time() - ts) <= self._cache_ttl_sec and dbid in dbids:
                            if pl_name not in names:
                                names.append(pl_name)
        except Exception:
            pass
        return names

    def is_playlist_cache_fresh(self, playlist_name: str) -> bool:
        with self._cache_lock:
            ent = self._playlist_cache.get(playlist_name)
            if not ent:
                return False
            ts, dbids = ent
            now = time.time()
            if (now - ts) <= self._cache_ttl_sec:
                # sliding expiration
                self._playlist_cache[playlist_name] = (now, set(dbids))
                return True
            return False

    # The following APIs keep GUI compatibility
    def create_playlist(self, name: str) -> bool:
        script = f'tell application "Music" to make new user playlist with properties {{name:\"{name}\"}}'
        try:
            self._run_applescript(script)
            return True
        except Exception:
            return False

    def get_playlist_dbids_threadsafe(self, playlist_name: str, cap: int | None = None, force_refresh: bool = False) -> Set[int]:
        # Build DBID set for the given playlist and cache it
        script = f'''
        tell application "Music"
            set out_ids to {{}}
            try
                set the_pl to user playlist "{playlist_name}"
                repeat with t in (get tracks of the_pl)
                    set end of out_ids to (get database ID of t)
                end repeat
            end try
            return out_ids
        end tell
        '''
        try:
            out = self._run_applescript(script)
            # AppleScript returns like: {123, 456, 789}
            raw = out.strip().strip('{}')
            s: Set[int] = set()
            if raw:
                for part in raw.split(','):
                    p = part.strip()
                    if p.isdigit():
                        s.add(int(p))
            with self._cache_lock:
                self._playlist_cache[playlist_name] = (time.time(), s)
            return s
        except Exception:
            return set()

    def get_all_playlists_threadsafe(self) -> List[str]:
        return self.get_all_playlists()

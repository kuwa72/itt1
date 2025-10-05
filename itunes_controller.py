import win32com.client
import pythoncom
from typing import Optional, List, Dict, Any
import time


class iTunesController:
    """iTunes OLE操作を管理するクラス"""
    
    def __init__(self):
        self.itunes = None
        self.current_track = None
        self._connect()
    
    def _connect(self):
        """iTunes COMオブジェクトに接続"""
        try:
            pythoncom.CoInitialize()
            # 複数のProgIDを試す（環境により異なる場合がある）
            last_err = None
            for progid in ("iTunes.Application", "iTunes.Application.1"):
                try:
                    self.itunes = win32com.client.Dispatch(progid)
                    if self.itunes:
                        return
                except Exception as e:
                    last_err = e
                    continue
            raise RuntimeError(f"iTunes COMに接続できません: {last_err}")
        except Exception as e:
            raise RuntimeError(f"iTunesに接続できません: {e}")
    
    def get_current_track_info(self) -> Dict[str, Any]:
        """現在再生中のトラック情報を取得"""
        if not self.itunes:
            return {}
        
        try:
            current_track = self.itunes.CurrentTrack
            if not current_track:
                return {}
            
            return {
                'name': current_track.Name,
                'artist': current_track.Artist,
                'album': current_track.Album,
                'duration': current_track.Duration,
                'position': self.itunes.PlayerPosition,
                'is_playing': self.itunes.PlayerState == 1
            }
        except Exception as e:
            print(f"トラック情報取得エラー: {e}")
            return {}
    
    def play_pause(self):
        """再生/一時停止を切り替える"""
        if self.itunes:
            self.itunes.PlayPause()
    
    def play_next_track(self):
        """次のトラックへ"""
        if self.itunes:
            self.itunes.NextTrack()
    
    def play_previous_track(self):
        """前のトラックへ"""
        if self.itunes:
            self.itunes.PreviousTrack()
    
    def skip_forward(self, seconds: int = 10):
        """指定秒数分スキップ"""
        if self.itunes:
            current_pos = self.itunes.PlayerPosition
            new_pos = current_pos + seconds
            self.itunes.PlayerPosition = max(0, new_pos)
    
    def skip_backward(self, seconds: int = 10):
        """指定秒数分戻る"""
        if self.itunes:
            current_pos = self.itunes.PlayerPosition
            new_pos = current_pos - seconds
            self.itunes.PlayerPosition = max(0, new_pos)
    
    def get_playlists(self) -> List[Dict[str, str]]:
        """利用可能なプレイリストを取得（簡易、名前とID相当のインデックス）"""
        if not self.itunes:
            return []
        
        playlists = []
        try:
            sources = self.itunes.Sources
            # COMコレクションは1始まり
            for i in range(1, sources.Count + 1):
                source = sources.Item(i)
                if source.Kind == 1:  # Library
                    playlists_collection = source.Playlists
                    for j in range(1, playlists_collection.Count + 1):
                        playlist = playlists_collection.Item(j)
                        playlists.append({
                            'name': playlist.Name,
                            'id': str(j)
                        })
        except Exception as e:
            print(f"プレイリスト取得エラー: {e}")
        
        return playlists
    
    def add_to_playlist(self, playlist_name: str) -> bool:
        """現在のトラックを指定されたプレイリストに追加。
        可能なら playlist.AddTrack(track) を使用し、失敗時は Duplicate(target) にフォールバックする。
        """
        if not self.itunes:
            return False
        
        try:
            current_track = self.itunes.CurrentTrack
            if not current_track:
                return False
            
            target = self._find_playlist_by_name(playlist_name)
            if target is None:
                return False

            # 追加前に重複チェック
            try:
                tracks = target.Tracks
                cur_dbid = getattr(current_track, 'TrackDatabaseID', None)
                if tracks is not None:
                    # TrackDatabaseID で一致チェック
                    if cur_dbid is not None:
                        for i in range(1, tracks.Count + 1):
                            t = tracks.Item(i)
                            try:
                                if getattr(t, 'TrackDatabaseID', object()) == cur_dbid:
                                    # 既に存在
                                    return False
                            except Exception:
                                continue
                    # メタデータでの保険チェック
                    cname = (getattr(current_track, 'Name', '') or '').strip()
                    cart = (getattr(current_track, 'Artist', '') or '').strip()
                    calb = (getattr(current_track, 'Album', '') or '').strip()
                    cdur = int(getattr(current_track, 'Duration', 0) or 0)
                    if cname:
                        for i in range(1, tracks.Count + 1):
                            t = tracks.Item(i)
                            try:
                                name = (getattr(t, 'Name', '') or '').strip()
                                art = (getattr(t, 'Artist', '') or '').strip()
                                alb = (getattr(t, 'Album', '') or '').strip()
                                dur = int(getattr(t, 'Duration', 0) or 0)
                                if cname == name and cart == art and calb == alb and abs(cdur - dur) <= 1:
                                    return False
                            except Exception:
                                continue
            except Exception:
                # 重複チェックに失敗しても追加処理は継続
                pass
            
            # まずは playlist.AddTrack(track) を試す
            try:
                if hasattr(target, 'AddTrack'):
                    target.AddTrack(current_track)
                    return True
            except Exception:
                pass

            # フォールバック: Duplicate(track) で追加
            try:
                current_track.Duplicate(target)
                return True
            except Exception:
                # 一部のリストは追加不可（スマートプレイリストなど）
                return False
            
            return False
        except Exception as e:
            print(f"プレイリスト追加エラー: {e}")
            return False
    
    def create_playlist(self, name: str) -> bool:
        """新しいプレイリストを作成"""
        if not self.itunes:
            return False
        
        try:
            self.itunes.CreatePlaylist(name)
            return True
        except Exception as e:
            print(f"プレイリスト作成エラー: {e}")
            return False

    def _find_playlist_by_name(self, name: str):
        """名前でプレイリストを検索し返す（見つからなければNone）"""
        try:
            sources = self.itunes.Sources
            for i in range(1, sources.Count + 1):
                source = sources.Item(i)
                if source.Kind == 1:
                    playlists = source.Playlists
                    for j in range(1, playlists.Count + 1):
                        playlist = playlists.Item(j)
                        if playlist.Name == name:
                            return playlist
        except Exception:
            pass
        return None

    def get_all_playlists(self) -> List[str]:
        """プレイリスト名の一覧を返す（表示用）"""
        names: List[str] = []
        try:
            sources = self.itunes.Sources
            for i in range(1, sources.Count + 1):
                source = sources.Item(i)
                if source.Kind == 1:
                    playlists = source.Playlists
                    for j in range(1, playlists.Count + 1):
                        names.append(playlists.Item(j).Name)
        except Exception as e:
            print(f"プレイリスト一覧取得エラー: {e}")
        return names

    def set_current_track_bpm(self, bpm: int) -> bool:
        """現在のトラックにBPMを設定（プロパティ名が環境で異なるため複数候補を試す）"""
        if not self.itunes:
            return False
        try:
            track = self.itunes.CurrentTrack
            if not track:
                return False
            for prop in ("BPM", "BeatsPerMinute"):
                try:
                    setattr(track, prop, int(bpm))
                    return True
                except Exception:
                    continue
        except Exception as e:
            print(f"BPM設定エラー: {e}")
        return False

import win32com.client
import pythoncom
from typing import Optional, List, Dict, Any
import time
import threading


class WindowsMusicController:
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
        if not self.itunes:
            return
        try:
            current_pos = self.itunes.PlayerPosition
            new_pos = current_pos + seconds
            self.itunes.PlayerPosition = max(0, new_pos)
        except Exception:
            # Ignore COM errors (e.g., no current track, track deleted, transient state)
            pass
    
    def skip_backward(self, seconds: int = 10):
        """指定秒数分戻る"""
        if not self.itunes:
            return
        try:
            current_pos = self.itunes.PlayerPosition
            new_pos = current_pos - seconds
            self.itunes.PlayerPosition = max(0, new_pos)
        except Exception:
            # Ignore COM errors
            pass
    
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
        Search APIを使った高速な重複チェックを実行。
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
            
            # トラック情報を取得
            track_name = getattr(current_track, 'Name', None)
            track_dbid = getattr(current_track, 'TrackDatabaseID', None)
            
            if not track_name or not isinstance(track_dbid, int):
                # 情報が不足している場合は追加のみ試みる
                if hasattr(target, 'AddTrack'):
                    target.AddTrack(current_track)
                    return True
                return False
            
            # Search APIで重複チェック（超高速: 0.04秒）
            if hasattr(target, 'Search'):
                try:
                    search_result = target.Search(track_name, 5)  # 5 = SongNames
                    if search_result and hasattr(search_result, 'Count') and search_result.Count > 0:
                        # 検索結果からDBIDで一致確認
                        for i in range(1, search_result.Count + 1):
                            t = search_result.Item(i)
                            try:
                                if getattr(t, 'TrackDatabaseID', None) == track_dbid:
                                    # 既に存在
                                    return True
                            except Exception:
                                continue
                except Exception:
                    # Search失敗時はフォールバック（後述）
                    pass
            
            # 重複なし → 追加
            if hasattr(target, 'AddTrack'):
                target.AddTrack(current_track)
                return True
            
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

    def get_playlists_of_current_track(self) -> List[str]:
        """現在のトラックが含まれているプレイリスト名一覧を返す。
        TrackDatabaseID を優先し、取得不可の場合はメタデータ一致で判定。
        スマートプレイリストなど一部は含有チェックができない場合あり。
        """
        result: List[str] = []
        if not self.itunes:
            return result
        try:
            track = self.itunes.CurrentTrack
            if not track:
                return result
            cur_dbid = getattr(track, 'TrackDatabaseID', None)
            cname = (getattr(track, 'Name', '') or '').strip()
            cart = (getattr(track, 'Artist', '') or '').strip()
            calb = (getattr(track, 'Album', '') or '').strip()
            cdur = int(getattr(track, 'Duration', 0) or 0)

            sources = self.itunes.Sources
            for i in range(1, sources.Count + 1):
                source = sources.Item(i)
                if source.Kind != 1:
                    continue
                playlists = source.Playlists
                for j in range(1, playlists.Count + 1):
                    pl = playlists.Item(j)
                    try:
                        tracks = getattr(pl, 'Tracks', None)
                        if tracks is None:
                            continue
                        found = False
                        if cur_dbid is not None:
                            for k in range(1, tracks.Count + 1):
                                t = tracks.Item(k)
                                try:
                                    if getattr(t, 'TrackDatabaseID', object()) == cur_dbid:
                                        found = True
                                        break
                                except Exception:
                                    continue
                        if not found and cname:
                            for k in range(1, tracks.Count + 1):
                                t = tracks.Item(k)
                                try:
                                    name = (getattr(t, 'Name', '') or '').strip()
                                    art = (getattr(t, 'Artist', '') or '').strip()
                                    alb = (getattr(t, 'Album', '') or '').strip()
                                    dur = int(getattr(t, 'Duration', 0) or 0)
                                    if cname == name and cart == art and calb == alb and abs(cdur - dur) <= 1:
                                        found = True
                                        break
                                except Exception:
                                    continue
                        if found:
                            result.append(pl.Name)
                    except Exception:
                        continue
        except Exception as e:
            print(f"登録プレイリスト取得エラー: {e}")
        return result

    def get_playlists_of_current_track_threadsafe(self, time_budget_sec: float = 1.5) -> List[str]:
        """バックグラウンドスレッド用の安全な取得関数。
        Search APIを使って高速に検索。
        """
        result: List[str] = []
        try:
            # このスレッドをSTAとして初期化
            pythoncom.CoInitialize()
            try:
                it = None
                last_err = None
                for progid in ("iTunes.Application", "iTunes.Application.1"):
                    try:
                        it = win32com.client.Dispatch(progid)
                        if it:
                            break
                    except Exception as e:
                        last_err = e
                        continue
                if it is None:
                    raise RuntimeError(f"iTunes COMに接続できません: {last_err}")

                track = getattr(it, 'CurrentTrack', None)
                if not track:
                    return result
                
                track_name = getattr(track, 'Name', None)
                track_dbid = getattr(track, 'TrackDatabaseID', None)
                
                if not track_name or not isinstance(track_dbid, int):
                    return result

                # 全プレイリストをチェック（Search APIで高速化）
                sources = getattr(it, 'Sources', None)
                if sources is None:
                    return result
                
                for i in range(1, sources.Count + 1):
                    source = sources.Item(i)
                    if getattr(source, 'Kind', None) != 1:
                        continue
                    playlists = getattr(source, 'Playlists', None)
                    if playlists is None:
                        continue
                    
                    for j in range(1, playlists.Count + 1):
                        pl = playlists.Item(j)
                        pl_name = getattr(pl, 'Name', None)
                        if not pl_name:
                            continue
                        
                        try:
                            # Search APIで重複チェック
                            if hasattr(pl, 'Search'):
                                search_result = pl.Search(track_name, 5)  # 5 = SongNames
                                if search_result and hasattr(search_result, 'Count') and search_result.Count > 0:
                                    # 検索結果からDBIDで一致確認
                                    for k in range(1, search_result.Count + 1):
                                        t = search_result.Item(k)
                                        try:
                                            if getattr(t, 'TrackDatabaseID', None) == track_dbid:
                                                if pl_name not in result:
                                                    result.append(pl_name)
                                                break
                                        except Exception:
                                            continue
                        except Exception:
                            continue
                
            finally:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
        except Exception as e:
            print(f"登録プレイリスト取得エラー: {e}")
        return result

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

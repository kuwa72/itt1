import win32com.client
import pythoncom
from typing import Optional, List, Dict, Any, Callable
import os
import threading
from urllib.parse import urlparse, unquote


class WindowsMusicController:
    """iTunes OLE操作を管理するクラス"""
    
    def __init__(self):
        self.itunes = None
        self.current_track = None
        self._connect()

    @staticmethod
    def _create_itunes() -> Any:
        """iTunes COMオブジェクトを取得する共通ヘルパー。

        - 呼び出し側のスレッドで pythoncom.CoInitialize 済みであることを前提とする
        - 複数のProgIDを試し、失敗時は RuntimeError を送出
        """
        it = None
        last_err: Optional[Exception] = None
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
        return it

    def _connect(self):
        """iTunes COMオブジェクトに接続"""
        try:
            pythoncom.CoInitialize()
            # 複数のProgIDを試す（環境により異なる場合がある）
            self.itunes = self._create_itunes()
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
            
            playlist_name = None
            try:
                cp = getattr(self.itunes, 'CurrentPlaylist', None)
                if cp is not None:
                    playlist_name = getattr(cp, 'Name', None)
            except Exception:
                pass

            return {
                'name': current_track.Name,
                'artist': current_track.Artist,
                'album': current_track.Album,
                'duration': current_track.Duration,
                'position': self.itunes.PlayerPosition,
                'is_playing': self.itunes.PlayerState == 1,
                'dbid': getattr(current_track, 'TrackDatabaseID', None),
                'playlist': playlist_name,
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
    
    def set_player_position(self, position: float):
        """再生位置を設定（秒単位）"""
        if not self.itunes:
            return
        try:
            self.itunes.PlayerPosition = max(0, position)
        except Exception:
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
    
    def get_playlist_tracks(self, playlist_name: str) -> List[Dict[str, Any]]:
        """指定プレイリストのトラック一覧を取得"""
        if not self.itunes:
            return []
        
        tracks = []
        try:
            target = self._find_playlist_by_name(playlist_name)
            if target is None:
                return []
            
            tracks_collection = getattr(target, 'Tracks', None)
            if tracks_collection is None:
                return []
            
            try:
                count = tracks_collection.Count
            except Exception as e:
                print(f"トラック数取得エラー: {e}")
                count = 0
            
            print(f"プレイリスト '{playlist_name}' のトラック数: {count}")
            
            for i in range(1, count + 1):
                try:
                    track = tracks_collection.Item(i)
                    tracks.append(self._track_to_dict(track, i))
                except Exception as e:
                    print(f"トラック{i}取得エラー: {e}")
                    continue
            
            # PlayOrderIndex順にソート（iTunes UIの表示順と一致）
            tracks.sort(key=lambda x: x.get('play_order', 0))
        except Exception as e:
            print(f"トラック一覧取得エラー: {e}")
        
        return tracks

    @staticmethod
    def _track_to_dict(track: Any, index: int) -> Dict[str, Any]:
        """IITTrack COMオブジェクトを共通のdict形式に変換するヘルパー。"""

        # IITObject IDs を取得（source / playlist / track / database）
        try:
            src_id, pl_id, trk_id, db_id = track.GetITObjectIDs()
        except Exception:
            src_id = pl_id = trk_id = None
            db_id = getattr(track, 'TrackDatabaseID', None)

        return {
            "index": index,
            "name": getattr(track, "Name", ""),
            "artist": getattr(track, "Artist", ""),
            "album": getattr(track, "Album", ""),
            "duration": int(getattr(track, "Duration", 0) or 0),
            "dbid": db_id,
            "source_id": src_id,
            "playlist_id": pl_id,
            "track_id": trk_id,
            "play_order": getattr(track, "PlayOrderIndex", index),
        }

    def _play_track_in_playlist_by_dbid(self, playlist, database_id: int, track_name: str | None = None, play_order: int | None = None) -> bool:
        """プレイリスト内から TrackDatabaseID が一致するトラックを特定して再生する。
        iTunesの「ダブルクリック」相当の挙動を模倣し、コンテキストを確実に確立する。
        """
        pname = getattr(playlist, 'Name', 'Unknown')
        print(f"DEBUG: _play_track_in_playlist_by_dbid playlist={pname} dbid={database_id}")
        try:
            # 1. ブラウザウィンドウでプレイリストを選択し、シャッフルをオフにする
            try:
                if hasattr(self.itunes, 'BrowserWindow'):
                    self.itunes.BrowserWindow.SelectedPlaylist = playlist
                if hasattr(playlist, 'Shuffle'):
                    playlist.Shuffle = False
            except Exception as e:
                print(f"DEBUG: Failed to prepare playlist UI state: {e}")

            # 2. コンテキストを強制的にこのプレイリストに切り替える（ダブルクリック挙動の核心）
            # PlayFirstTrack() を呼ぶことで、iTunesの「次はこちら」キューがこのプレイリストで再構成される
            print(f"DEBUG: Switching context to '{pname}' via PlayFirstTrack()...")
            try:
                playlist.PlayFirstTrack()
                # 非常に短い待ち時間を入れることで iTunes の内部状態更新を待つ
                import time
                time.sleep(0.2)
            except Exception as e:
                print(f"DEBUG: PlayFirstTrack failed: {e}")

            tracks = getattr(playlist, 'Tracks', None)
            if tracks is None:
                return False

            # 3. 目的のトラックを特定して再生（既に再生が始まっている可能性もあるが、確実にターゲットへ飛ばす）
            # play_order (GUI/XMLのインデックス) 周辺を確認（最速パス）
            if play_order is not None:
                for offset in [0, -1, 1, -2, 2]:
                    check_idx = play_order + offset
                    if 1 <= check_idx <= tracks.Count:
                        try:
                            tr = tracks.Item(check_idx)
                            if getattr(tr, 'TrackDatabaseID', None) == database_id:
                                print(f"DEBUG: Jumping to target via index {check_idx}. Playing...")
                                tr.Play()
                                return True
                        except Exception:
                            pass

            # 4. Search API で POI (PlayOrderIndex) を取得し、そのインデックス周辺で再試行
            if track_name and hasattr(playlist, 'Search'):
                print(f"DEBUG: Searching '{track_name}' for jump target...")
                search_result = playlist.Search(track_name, 5) # 5 = SongNames
                if search_result and search_result.Count > 0:
                    for tr_cand in search_result:
                        if getattr(tr_cand, 'TrackDatabaseID', None) == database_id:
                            poi = getattr(tr_cand, 'PlayOrderIndex', None)
                            if poi is not None:
                                try:
                                    for check_idx in range(max(1, poi - 10), min(tracks.Count + 1, poi + 11)):
                                        tr = tracks.Item(check_idx)
                                        if getattr(tr, 'TrackDatabaseID', None) == database_id:
                                            print(f"DEBUG: Jumping to target via Search + Index {check_idx}. Playing...")
                                            tr.Play()
                                            return True
                                except Exception:
                                    pass
                            
                            print(f"DEBUG: Playing target from Search result directly.")
                            tr_cand.Play()
                            return True

            # 5. 最終手段: 全件走査
            print(f"DEBUG: Starting fast scan for jump target...")
            for tr in tracks:
                try:
                    if getattr(tr, 'TrackDatabaseID', None) == database_id:
                        print("DEBUG: Found target via scan. Playing...")
                        tr.Play()
                        return True
                except Exception:
                    pass

        except Exception as e:
            print(f"プレイリスト内再生エラー: {e}")
        return False

    def play_track_by_ids(
        self,
        source_id: int | None,
        playlist_id: int | None,
        track_id: int | None,
        database_id: int | None,
        persistent_id: str | None = None,
        track_name: str | None = None,
        playlist_name: str | None = None,
        play_order: int | None = None,
    ) -> bool:
        """トラックを特定して再生する。

        playlist_name が指定されている場合、そのプレイリスト内のトラックオブジェクトで
        Play() を呼び、iTunes 側でもそのプレイリストのコンテキストで再生する。
        """
        if not self.itunes:
            return False

        # 1. 指定プレイリスト内の特定 (play_order または走査でコンテキスト維持)
        if isinstance(database_id, int) and playlist_name:
            try:
                target_pl = self._find_playlist_by_name(playlist_name)
                if target_pl is not None:
                    if self._play_track_in_playlist_by_dbid(target_pl, database_id, track_name, play_order):
                        return True
            except Exception as e:
                print(f"プレイリスト内再生エラー: {e}")

        # 2. ライブラリ全体で Search API + TrackDatabaseID（高速）
        if isinstance(database_id, int) and track_name:
            try:
                lib_playlist = getattr(self.itunes, 'LibraryPlaylist', None)
                if lib_playlist is not None and hasattr(lib_playlist, 'Search'):
                    search_result = lib_playlist.Search(track_name, 5)  # 5 = SongNames
                    if search_result and hasattr(search_result, 'Count') and search_result.Count > 0:
                        for i in range(1, search_result.Count + 1):
                            tr = search_result.Item(i)
                            try:
                                if getattr(tr, 'TrackDatabaseID', None) == database_id:
                                    tr.Play()
                                    return True
                            except Exception:
                                pass
            except Exception as e:
                print(f"Search API再生エラー: {e}")

        return False

    @staticmethod
    def _normalize_file_location(location: str) -> str:
        """XMLのLocationやCOMのLocationを比較可能なWindowsパスへ正規化する。"""
        if not location:
            return ""
        loc = str(location)
        # XML: file://localhost/C:/... のようなURL
        if loc.lower().startswith("file:"):
            try:
                u = urlparse(loc)
                p = unquote(u.path or "")
                if p.startswith("/") and len(p) >= 3 and p[2] == ":":
                    p = p[1:]
                p = p.replace("/", "\\")
                return os.path.normcase(os.path.normpath(p))
            except Exception:
                return ""
        # COM: C:\... のようなパス
        try:
            return os.path.normcase(os.path.normpath(loc))
        except Exception:
            return ""

    def play_track_by_location(self, location: str) -> bool:
        """Location（ファイルパス/URL）でライブラリ内トラックを検索して再生する。"""
        if not self.itunes:
            return False
        want = WindowsMusicController._normalize_file_location(location)
        if not want:
            return False

        try:
            lib_playlist = getattr(self.itunes, 'LibraryPlaylist', None)
            if lib_playlist is None:
                return False
            tracks = getattr(lib_playlist, 'Tracks', None)
            if tracks is None:
                return False
            try:
                count = tracks.Count
            except Exception:
                return False
            for i in range(1, count + 1):
                try:
                    tr = tracks.Item(i)
                    got = getattr(tr, 'Location', None)
                    got_n = WindowsMusicController._normalize_file_location(str(got or ""))
                    if got_n and got_n == want:
                        tr.Play()
                        return True
                except Exception:
                    continue
        except Exception as e:
            print(f"Location一致による再生エラー: {e}")
        return False

    def stream_playlist_tracks_threadsafe(
        self,
        playlist_name: str,
        batch_size: int,
        on_batch: Callable[[List[Dict[str, Any]]], None],
        cancel_event: Optional[threading.Event] = None,
    ) -> threading.Thread:
        """指定プレイリストのトラックを別スレッドで列挙し、バッチごとにコールバックする。

        - COMはこのワーカースレッド内で初期化・破棄する
        - GUI側はon_batch内でTkに触らず、queue.putなどの軽い処理のみを行うこと
        """

        def _worker() -> None:
            try:
                pythoncom.CoInitialize()
                try:
                    try:
                        it = WindowsMusicController._create_itunes()
                    except Exception as e:
                        print(f"iTunes COMに接続できません (stream): {e}")
                        return

                    # 対象プレイリストを検索
                    target = None
                    try:
                        sources = getattr(it, "Sources", None)
                        if sources is None:
                            return
                        for i in range(1, sources.Count + 1):
                            src = sources.Item(i)
                            if getattr(src, "Kind", None) != 1:
                                continue
                            pls = getattr(src, "Playlists", None)
                            if pls is None:
                                continue
                            for j in range(1, pls.Count + 1):
                                pl = pls.Item(j)
                                if getattr(pl, "Name", None) == playlist_name:
                                    target = pl
                                    break
                            if target is not None:
                                break
                    except Exception as e:
                        print(f"プレイリスト検索エラー (stream): {e}")
                        return

                    if target is None:
                        return

                    tracks_collection = getattr(target, "Tracks", None)
                    if tracks_collection is None:
                        return

                    try:
                        count = tracks_collection.Count
                    except Exception as e:
                        print(f"トラック数取得エラー (stream): {e}")
                        return

                    all_tracks: List[Dict[str, Any]] = []
                    for i in range(1, count + 1):
                        if cancel_event is not None and cancel_event.is_set():
                            return
                        try:
                            tr = tracks_collection.Item(i)
                            all_tracks.append(WindowsMusicController._track_to_dict(tr, i))
                        except Exception:
                            continue

                    # iTunes UIの表示順にソートしてからバッチ分割
                    all_tracks.sort(key=lambda x: x.get("play_order", 0))

                    if batch_size <= 0:
                        batch_size_local = len(all_tracks) or 1
                    else:
                        batch_size_local = batch_size

                    for start in range(0, len(all_tracks), batch_size_local):
                        if cancel_event is not None and cancel_event.is_set():
                            return
                        chunk = all_tracks[start : start + batch_size_local]
                        try:
                            on_batch(chunk)
                        except Exception as e:
                            print(f"on_batchコールバックエラー: {e}")
                            return
                finally:
                    try:
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass
            except Exception as e:
                print(f"stream_playlist_tracks_threadsafeエラー: {e}")

        th = threading.Thread(target=_worker, daemon=True)
        th.start()
        return th
    
    def get_current_playlist_tracks(self) -> List[Dict[str, Any]]:
        """現在再生中のプレイリストのトラック一覧を取得"""
        if not self.itunes:
            return []
        
        try:
            current_playlist = getattr(self.itunes, 'CurrentPlaylist', None)
            if current_playlist is None:
                return []
            
            playlist_name = getattr(current_playlist, 'Name', '')
            if not playlist_name:
                return []
            
            return self.get_playlist_tracks(playlist_name)
        except Exception as e:
            print(f"現在のプレイリスト取得エラー: {e}")
            return []
    
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

    def play_playlist(self, playlist_name: str) -> bool:
        """指定された名前のプレイリストを検索して再生する。"""
        if not self.itunes:
            return False
        try:
            target = self._find_playlist_by_name(playlist_name)
            if target:
                print(f"DEBUG: play_playlist target={target} Name={getattr(target, 'Name', 'Unknown')} Kind={getattr(target, 'Kind', 'Unknown')}")
                # 一部のプレイリスト（スマートプレイリスト等）で Play() が直接失敗する場合がある
                # その場合、プレイリスト内の最初の曲を再生することを試みる
                try:
                    target.Play()
                    return True
                except Exception as e:
                    print(f"DEBUG: target.Play() failed: {e}. Trying first track...")
                    tracks = getattr(target, 'Tracks', None)
                    if tracks and tracks.Count > 0:
                        first_track = tracks.Item(1)
                        print(f"DEBUG: Playing first track: {getattr(first_track, 'Name', 'Unknown')}")
                        first_track.Play()
                        return True
                    else:
                        print(f"DEBUG: No tracks found in playlist or Tracks is None.")
            else:
                print(f"DEBUG: Playlist not found: {playlist_name}")
        except Exception as e:
            print(f"プレイリスト再生エラー: {e}")
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
                it = WindowsMusicController._create_itunes()

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

import win32com.client
import pythoncom
from typing import Optional, List, Dict, Any, Set
import time
import threading


class iTunesController:
    """iTunes OLE操作を管理するクラス"""
    
    def __init__(self):
        self.itunes = None
        self.current_track = None
        # Simple cache for current-track playlists to avoid repeated heavy scans
        self._last_result_sig: tuple | None = None
        self._last_result_names: List[str] = []
        self._last_result_time: float = 0.0
        # Playlist DBID cache {playlist_name: (timestamp, set_of_dbids)}
        self._playlist_dbid_cache: Dict[str, tuple[float, Set[int]]] = {}
        self._cache_ttl_sec: float = 300.0
        self._cache_lock = threading.Lock()
        # Recently-added cache keyed by track signature to surface membership when DBID is missing
        # {(name, artist, album, duration): (timestamp, set_of_playlist_names)}
        self._recent_sig_cache: Dict[tuple, tuple[float, Set[str]]] = {}
        self._recent_sig_ttl_sec: float = 300.0
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
            # 明示的な更新要求とみなして、プレイリストDBIDキャッシュをクリア
            with self._cache_lock:
                self._playlist_dbid_cache.clear()
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

            # 追加前に重複チェック（TrackDatabaseID のみ。既に存在する場合は成功扱い）
            try:
                tracks = target.Tracks
                cur_dbid = getattr(current_track, 'TrackDatabaseID', None)
                if tracks is not None and cur_dbid is not None:
                    # TrackDatabaseID で一致チェック（全件走査）
                    for i in range(1, tracks.Count + 1):
                        t = tracks.Item(i)
                        try:
                            if getattr(t, 'TrackDatabaseID', object()) == cur_dbid:
                                # 既に存在 → 成功扱い（キャッシュも更新）
                                self._add_dbid_to_cache(playlist_name, cur_dbid)
                                return True
                        except Exception:
                            continue
            except Exception:
                # 重複チェックに失敗しても追加処理は継続
                pass
            
            # まずは playlist.AddTrack(track) を試す
            try:
                if hasattr(target, 'AddTrack'):
                    target.AddTrack(current_track)
                    # 追加成功時はキャッシュに反映（DBID優先、無ければシグネチャで記録）
                    cur_dbid = getattr(current_track, 'TrackDatabaseID', None)
                    if isinstance(cur_dbid, int):
                        self._add_dbid_to_cache(playlist_name, cur_dbid)
                    else:
                        sig = (
                            (getattr(current_track, 'Name', '') or '').strip(),
                            (getattr(current_track, 'Artist', '') or '').strip(),
                            (getattr(current_track, 'Album', '') or '').strip(),
                            int(getattr(current_track, 'Duration', 0) or 0),
                        )
                        self._add_sig_to_recent_cache(sig, playlist_name)
                    return True
            except Exception:
                # AddTrack が失敗した場合は失敗として扱う（フォールバックは行わない方針）
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
        このスレッド内でCOMを初期化し、iTunesのCOMオブジェクトを新規に取得して照会する。
        既存の self.itunes（別スレッドで作成）には触れない。
        """
        result: List[str] = []
        try:
            # このスレッドをSTAとして初期化
            pythoncom.CoInitialize()
            try:
                start = time.time()
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
                cur_dbid = getattr(track, 'TrackDatabaseID', None)
                cname = (getattr(track, 'Name', '') or '').strip()
                cart = (getattr(track, 'Artist', '') or '').strip()
                calb = (getattr(track, 'Album', '') or '').strip()
                cdur = int(getattr(track, 'Duration', 0) or 0)

                # Cache check by signature
                sig = (cname, cart, calb, cdur)
                if self._last_result_sig == sig and (time.time() - self._last_result_time) <= 2.0:
                    return list(self._last_result_names)
                # Consult recent-added cache first
                recent = self._get_recent_sig_playlists(sig)
                if recent:
                    result.extend([n for n in recent if n not in result])

                sources = getattr(it, 'Sources', None)
                if sources is None:
                    return result
                for i in range(1, sources.Count + 1):
                    if time.time() - start > time_budget_sec:
                        break
                    source = sources.Item(i)
                    if getattr(source, 'Kind', None) != 1:
                        continue
                    playlists = getattr(source, 'Playlists', None)
                    if playlists is None:
                        continue
                    for j in range(1, playlists.Count + 1):
                        if time.time() - start > time_budget_sec:
                            break
                        pl = playlists.Item(j)
                        pl_name = getattr(pl, 'Name', None)
                        try:
                            tracks = getattr(pl, 'Tracks', None)
                            if tracks is None:
                                continue
                            if isinstance(cur_dbid, int):
                                # DBID path with cache
                                cached = self._get_cached_dbid_set(pl_name)
                                if cached is not None:
                                    if cur_dbid in cached:
                                        if pl_name not in result:
                                            result.append(pl_name)
                                    continue
                                seen: Set[int] = set()
                                try:
                                    total = tracks.Count
                                except Exception:
                                    total = 0
                                for k in range(1, total + 1):
                                    if time.time() - start > time_budget_sec:
                                        break
                                    t = tracks.Item(k)
                                    try:
                                        dbid = getattr(t, 'TrackDatabaseID', None)
                                        if isinstance(dbid, int):
                                            seen.add(dbid)
                                            if dbid == cur_dbid and pl_name not in result:
                                                result.append(pl_name)
                                    except Exception:
                                        continue
                                self._set_cached_dbid_set(pl_name, seen)
                            else:
                                # Fallback: metadata match if DBID is unavailable
                                try:
                                    total = tracks.Count
                                except Exception:
                                    total = 0
                                for k in range(1, total + 1):
                                    if time.time() - start > time_budget_sec:
                                        break
                                    t = tracks.Item(k)
                                    try:
                                        name = (getattr(t, 'Name', '') or '').strip()
                                        art = (getattr(t, 'Artist', '') or '').strip()
                                        alb = (getattr(t, 'Album', '') or '').strip()
                                        dur = int(getattr(t, 'Duration', 0) or 0)
                                        if cname == name and cart == art and calb == alb and abs(cdur - dur) <= 1:
                                            if pl_name not in result:
                                                result.append(pl_name)
                                            break
                                    except Exception:
                                        continue
                        except Exception:
                            continue
                # Save cache
                self._last_result_sig = sig
                self._last_result_names = list(result)
                self._last_result_time = time.time()
            finally:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
        except Exception as e:
            print(f"登録プレイリスト取得エラー: {e}")
        return result

    # --- Playlist DBID cache helpers ---
    def _get_cached_dbid_set(self, playlist_name: Optional[str]) -> Optional[Set[int]]:
        if not playlist_name:
            return None
        with self._cache_lock:
            ent = self._playlist_dbid_cache.get(playlist_name)
            if not ent:
                return None
            ts, dbids = ent
            now = time.time()
            if (now - ts) > self._cache_ttl_sec:
                # expired
                self._playlist_dbid_cache.pop(playlist_name, None)
                return None
            # sliding expiration: bump timestamp on access
            self._playlist_dbid_cache[playlist_name] = (now, set(dbids))
            return set(dbids)

    def _set_cached_dbid_set(self, playlist_name: Optional[str], dbids: Set[int]) -> None:
        if not playlist_name:
            return
        with self._cache_lock:
            self._playlist_dbid_cache[playlist_name] = (time.time(), set(dbids))

    def _add_dbid_to_cache(self, playlist_name: Optional[str], dbid: int) -> None:
        if not playlist_name or not isinstance(dbid, int):
            return
        with self._cache_lock:
            ts = time.time()
            ent = self._playlist_dbid_cache.get(playlist_name)
            if ent and (ts - ent[0]) <= self._cache_ttl_sec:
                s = set(ent[1])
                s.add(dbid)
                self._playlist_dbid_cache[playlist_name] = (ts, s)
            else:
                self._playlist_dbid_cache[playlist_name] = (ts, {dbid})

    def _add_sig_to_recent_cache(self, sig: tuple, playlist_name: str) -> None:
        if not playlist_name or not sig:
            return
        with self._cache_lock:
            ts = time.time()
            ent = self._recent_sig_cache.get(sig)
            if ent and (ts - ent[0]) <= self._recent_sig_ttl_sec:
                s = set(ent[1])
                s.add(playlist_name)
                self._recent_sig_cache[sig] = (ts, s)
            else:
                self._recent_sig_cache[sig] = (ts, {playlist_name})

    def _get_recent_sig_playlists(self, sig: tuple) -> Set[str]:
        with self._cache_lock:
            ent = self._recent_sig_cache.get(sig)
            if not ent:
                return set()
            ts, names = ent
            now = time.time()
            if (now - ts) > self._recent_sig_ttl_sec:
                self._recent_sig_cache.pop(sig, None)
                return set()
            # sliding expiration: bump timestamp on access
            self._recent_sig_cache[sig] = (now, set(names))
            return set(names)

    def get_playlist_dbids_threadsafe(self, playlist_name: str, cap: int | None = None, force_refresh: bool = False) -> Set[int]:
        """指定プレイリストの TrackDatabaseID 集合を返す。スレッド内でCOM初期化して取得し、キャッシュする。
        cap を指定すると先頭 cap 件までに制限。force_refresh でキャッシュ無視。
        """
        if not force_refresh:
            cached = self._get_cached_dbid_set(playlist_name)
            if cached is not None:
                return cached
        result: Set[int] = set()
        try:
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
                # find playlist
                sources = getattr(it, 'Sources', None)
                if sources is None:
                    return result
                target = None
                for i in range(1, sources.Count + 1):
                    src = sources.Item(i)
                    if getattr(src, 'Kind', None) != 1:
                        continue
                    pls = getattr(src, 'Playlists', None)
                    if pls is None:
                        continue
                    for j in range(1, pls.Count + 1):
                        pl = pls.Item(j)
                        if getattr(pl, 'Name', None) == playlist_name:
                            target = pl
                            break
                    if target is not None:
                        break
                if target is None:
                    return result
                tracks = getattr(target, 'Tracks', None)
                if tracks is None:
                    return result
                try:
                    count = tracks.Count
                except Exception:
                    count = 0
                limit = count if cap is None else min(count, cap)
                for k in range(1, limit + 1):
                    t = tracks.Item(k)
                    try:
                        dbid = getattr(t, 'TrackDatabaseID', None)
                        if isinstance(dbid, int):
                            result.add(dbid)
                    except Exception:
                        continue
                self._set_cached_dbid_set(playlist_name, result)
            finally:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
        except Exception as e:
            print(f"プレイリストDBID取得エラー: {e}")
        return result

    def is_playlist_cache_fresh(self, playlist_name: str) -> bool:
        """キャッシュに対象プレイリストが存在し、TTL内であるかを返す（COMは呼ばない）。"""
        if not playlist_name:
            return False
        with self._cache_lock:
            ent = self._playlist_dbid_cache.get(playlist_name)
            if not ent:
                return False
            ts, dbids = ent
            now = time.time()
            fresh = (now - ts) <= self._cache_ttl_sec
            if fresh:
                # sliding expiration: bump timestamp on freshness check
                self._playlist_dbid_cache[playlist_name] = (now, set(dbids))
            return fresh

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

    def get_playlists_of_current_track_from_cache(self) -> List[str]:
        """キャッシュのみに基づいて現在の曲が含まれるプレイリスト名を返す（高速・非ブロッキング）。
        - playlist_dbid_cache と recent_sig_cache を参照
        - COM列挙は行わない（CurrentTrack の参照のみ）
        返り値は重複排除済みの一覧
        """
        names: List[str] = []
        if not self.itunes:
            return names
        try:
            track = self.itunes.CurrentTrack
            if not track:
                return names
            cur_dbid = getattr(track, 'TrackDatabaseID', None)
            sig = (
                (getattr(track, 'Name', '') or '').strip(),
                (getattr(track, 'Artist', '') or '').strip(),
                (getattr(track, 'Album', '') or '').strip(),
                int(getattr(track, 'Duration', 0) or 0),
            )
            with self._cache_lock:
                # recent by signature
                ent = self._recent_sig_cache.get(sig)
                if ent and (time.time() - ent[0]) <= self._recent_sig_ttl_sec:
                    for n in ent[1]:
                        if n not in names:
                            names.append(n)
                # dbid-based membership
                if isinstance(cur_dbid, int):
                    for pl_name, (ts, dbids) in self._playlist_dbid_cache.items():
                        if (time.time() - ts) <= self._cache_ttl_sec and cur_dbid in dbids:
                            if pl_name not in names:
                                names.append(pl_name)
        except Exception:
            pass
        return names

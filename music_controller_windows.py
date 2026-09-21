import logging

import win32com.client
import pythoncom
from typing import Optional, List, Dict, Any
import os
from urllib.parse import urlparse, unquote

logger = logging.getLogger(__name__)

# iTunesタイプライブラリの ITUserPlaylistSpecialKindFolder。
# 旧SDKドキュメントでは 1 と記載されるが、実際のタイプライブラリ(iTunes 1.13)では 4。
IT_USER_PLAYLIST_SPECIAL_KIND_FOLDER = 4


class WindowsMusicController:
    """iTunes OLE操作を管理するクラス"""
    
    def __init__(self):
        self.itunes = None
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

    def close(self):
        """終了処理: _connect で CoInitialize した COM を呼び出しスレッドで解放する"""
        self.itunes = None
        try:
            pythoncom.CoUninitialize()
        except Exception as e:
            logger.debug("CoUninitialize に失敗: %s", e)

    def create_worker_controller(self) -> "WindowsMusicController":
        """ワーカースレッド専用の新規コントローラーを返す。

        STA で Dispatch した COM オブジェクトは生成したスレッドでのみ有効なため、
        このファクトリは利用するワーカースレッド内で呼び出すこと
        （そのスレッドで CoInitialize + Dispatch が行われる）。
        使い終わったら同じスレッドで close() して CoUninitialize する。
        """
        return WindowsMusicController()
    
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
            logger.error("トラック情報取得エラー: %s", e)
            return {}

    def get_current_playlist_name(self) -> str | None:
        """現在再生中のプレイリスト名を返す。未再生/取得不可の場合は None"""
        if not self.itunes:
            return None
        try:
            current_playlist = getattr(self.itunes, 'CurrentPlaylist', None)
            if current_playlist is None:
                return None
            name = getattr(current_playlist, 'Name', None)
            return name or None
        except Exception as e:
            logger.error("現在のプレイリスト名取得エラー: %s", e)
            return None

    def play_pause(self):
        """再生/一時停止を切り替える"""
        if not self.itunes:
            return
        try:
            self.itunes.PlayPause()
        except Exception as e:
            # 一時的な COM エラー（RPC_E_CALL_REJECTED 等）を Tk コールバックへ伝播させない
            logger.error("再生/一時停止エラー: %s", e)

    def play_next_track(self, playlist_name: str | None = None):
        """次のトラックへ。

        プレイリストコンテキストが分かる場合は iTunes の再生キューではなく、
        そのプレイリストの表示順で隣接するトラックを直接再生する（Issue #18）。
        playlist_name: GUI が記録している再生コンテキストのプレイリスト名。
        """
        self._play_adjacent_track(1, playlist_name)

    def play_previous_track(self, playlist_name: str | None = None):
        """前のトラックへ。play_next_track と同じくプレイリストコンテキストを優先する"""
        self._play_adjacent_track(-1, playlist_name)

    def _play_adjacent_track(self, direction: int, playlist_name: str | None = None):
        """プレイリストコンテキストでの隣接トラック遷移。

        解決順:
        1. playlist_name 引数（GUI が記録した再生コンテキスト）
        2. itunes.CurrentPlaylist（iTunes 側の再生中プレイリスト）
        いずれでも現在トラックがプレイリスト内に見つからなければ
        NextTrack()/PreviousTrack() にフォールバックする。
        """
        if not self.itunes:
            return
        try:
            current = getattr(self.itunes, 'CurrentTrack', None)
            dbid = getattr(current, 'TrackDatabaseID', None) if current else None
            if isinstance(dbid, int):
                names: List[str] = []
                if playlist_name:
                    names.append(playlist_name)
                current_pl_name = self.get_current_playlist_name()
                if current_pl_name and current_pl_name not in names:
                    names.append(current_pl_name)
                for name in names:
                    playlist = self._find_playlist_by_name(name)
                    if playlist is None:
                        continue
                    if self._play_playlist_neighbor(playlist, direction, current, dbid):
                        return
            if direction > 0:
                self.itunes.NextTrack()
            else:
                self.itunes.PreviousTrack()
        except Exception as e:
            logger.error("トラック遷移エラー: %s", e)

    def _play_playlist_neighbor(self, playlist, direction: int, current_track, database_id: int) -> bool:
        """現在トラックのプレイリスト内インデックス ± direction のトラックを再生する。

        PlayFirstTrack() は呼ばない: コンテキスト確立済みの隣接遷移では
        先頭曲が一瞬再生される副作用の方が害になるため、対象トラックを直接 Play() する。
        """
        try:
            tracks = getattr(playlist, 'Tracks', None)
            if tracks is None:
                return False
            count = tracks.Count
        except Exception:
            return False

        index_hint = getattr(current_track, 'PlayOrderIndex', None)
        if not isinstance(index_hint, int):
            index_hint = None
        track_name = getattr(current_track, 'Name', None)

        idx = self._find_playlist_track_index(
            playlist, tracks, count, database_id, track_name, index_hint
        )
        if idx is None:
            return False

        target_idx = idx + direction
        if not 1 <= target_idx <= count:
            logger.debug(
                "No adjacent track in '%s' (idx=%s dir=%s count=%s)",
                getattr(playlist, 'Name', '?'), idx, direction, count,
            )
            return False
        try:
            tr = tracks.Item(target_idx)
            logger.debug(
                "Playing adjacent track #%s in '%s' (dir=%s)",
                target_idx, getattr(playlist, 'Name', '?'), direction,
            )
            tr.Play()
            return True
        except Exception as e:
            logger.debug("隣接トラックの再生に失敗: %s", e)
            return False

    def _find_playlist_track_index(
        self,
        playlist,
        tracks,
        count: int,
        database_id: int,
        track_name: str | None = None,
        index_hint: int | None = None,
    ) -> int | None:
        """プレイリスト内で TrackDatabaseID が一致するトラックの 1 始まりインデックスを返す。

        index_hint（CurrentTrack.PlayOrderIndex 等）→ Search API の POI → 全件走査の順で試す。
        """
        def _matches(idx: int) -> bool:
            try:
                return getattr(tracks.Item(idx), 'TrackDatabaseID', None) == database_id
            except Exception:
                return False

        # 1. ヒントインデックス周辺
        if index_hint is not None:
            for offset in (0, -1, 1, -2, 2):
                i = index_hint + offset
                if 1 <= i <= count and _matches(i):
                    return i

        # 2. Search API で POI を取得し周辺を確認
        if track_name and hasattr(playlist, 'Search'):
            try:
                search_result = playlist.Search(track_name, 5)  # 5 = SongNames
                if search_result and getattr(search_result, 'Count', 0) > 0:
                    for candidate in search_result:
                        try:
                            if getattr(candidate, 'TrackDatabaseID', None) != database_id:
                                continue
                            poi = getattr(candidate, 'PlayOrderIndex', None)
                            if isinstance(poi, int):
                                for i in range(max(1, poi - 10), min(count + 1, poi + 11)):
                                    if _matches(i):
                                        return i
                        except Exception:
                            continue
            except Exception:
                pass

        # 3. 全件走査
        try:
            idx = 0
            for tr in tracks:
                idx += 1
                try:
                    if getattr(tr, 'TrackDatabaseID', None) == database_id:
                        return idx
                except Exception:
                    continue
        except Exception:
            pass
        return None
    
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
    
    @staticmethod
    def _is_folder_playlist(playlist: Any) -> bool:
        """フォルダプレイリストかどうかを SpecialKind で判定する。

        gen_py静的ラッパー経由では IITPlaylist に SpecialKind が存在しないため、
        AttributeError の場合は IITUserPlaylist へキャストして取得する。
        """
        try:
            return playlist.SpecialKind == IT_USER_PLAYLIST_SPECIAL_KIND_FOLDER
        except AttributeError:
            try:
                casted = win32com.client.CastTo(playlist, "IITUserPlaylist")
                return casted.SpecialKind == IT_USER_PLAYLIST_SPECIAL_KIND_FOLDER
            except Exception:
                return False
        except Exception:
            return False

    def get_playlists(self) -> List[Dict[str, str]]:
        """利用可能なプレイリストを取得（簡易、名前とID相当のインデックス）。フォルダは除外"""
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
                        if self._is_folder_playlist(playlist):
                            continue
                        playlists.append({
                            'name': playlist.Name,
                            'id': str(j)
                        })
        except Exception as e:
            logger.error("プレイリスト取得エラー: %s", e)

        return playlists

    def _play_track_in_playlist_by_dbid(self, playlist, database_id: int, track_name: str | None = None, play_order: int | None = None) -> bool:
        """プレイリスト内から TrackDatabaseID が一致するトラックを特定して再生する。
        iTunesの「ダブルクリック」相当の挙動を模倣し、コンテキストを確実に確立する。
        """
        pname = getattr(playlist, 'Name', 'Unknown')
        logger.debug("_play_track_in_playlist_by_dbid playlist=%s dbid=%s", pname, database_id)
        try:
            # 1. ブラウザウィンドウでプレイリストを選択し、シャッフルをオフにする
            try:
                if hasattr(self.itunes, 'BrowserWindow'):
                    self.itunes.BrowserWindow.SelectedPlaylist = playlist
                if hasattr(playlist, 'Shuffle'):
                    playlist.Shuffle = False
            except Exception as e:
                logger.debug("Failed to prepare playlist UI state: %s", e)

            # 2. コンテキストを強制的にこのプレイリストに切り替える（ダブルクリック挙動の核心）
            # PlayFirstTrack() を呼ぶことで、iTunesの「次はこちら」キューがこのプレイリストで再構成される
            logger.debug("Switching context to '%s' via PlayFirstTrack()...", pname)
            try:
                playlist.PlayFirstTrack()
                # 非常に短い待ち時間を入れることで iTunes の内部状態更新を待つ
                import time
                time.sleep(0.2)
            except Exception as e:
                logger.debug("PlayFirstTrack failed: %s", e)

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
                                logger.debug("Jumping to target via index %s. Playing...", check_idx)
                                tr.Play()
                                return True
                        except Exception:
                            pass

            # 4. Search API で POI (PlayOrderIndex) を取得し、そのインデックス周辺で再試行
            if track_name and hasattr(playlist, 'Search'):
                logger.debug("Searching '%s' for jump target...", track_name)
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
                                            logger.debug("Jumping to target via Search + Index %s. Playing...", check_idx)
                                            tr.Play()
                                            return True
                                except Exception:
                                    pass
                            
                            logger.debug("Playing target from Search result directly.")
                            tr_cand.Play()
                            return True

            # 5. 最終手段: 全件走査
            logger.debug("Starting fast scan for jump target...")
            for tr in tracks:
                try:
                    if getattr(tr, 'TrackDatabaseID', None) == database_id:
                        logger.debug("Found target via scan. Playing...")
                        tr.Play()
                        return True
                except Exception:
                    pass

        except Exception as e:
            logger.error("プレイリスト内再生エラー: %s", e)
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
                logger.error("プレイリスト内再生エラー: %s", e)

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
                logger.error("Search API再生エラー: %s", e)

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
            logger.error("Location一致による再生エラー: %s", e)
        return False

    def add_to_playlist(self, playlist_name: str) -> str | bool:
        """現在のトラックを指定されたプレイリストに追加。
        Search APIを使った高速な重複チェックを実行。
        戻り値: "added"（新規追加）, "already_exists"（既に存在）, False（失敗）
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
                    return "added"
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
                                    return "already_exists"
                            except Exception:
                                continue
                except Exception:
                    # Search失敗時はフォールバック（後述）
                    pass
            
            # 重複なし → 追加
            if hasattr(target, 'AddTrack'):
                target.AddTrack(current_track)
                return "added"
            
            return False
            
        except Exception as e:
            logger.error("プレイリスト追加エラー: %s", e)
            return False
    
    
    def create_playlist(self, name: str) -> bool:
        """新しいプレイリストを作成"""
        if not self.itunes:
            return False
        
        try:
            self.itunes.CreatePlaylist(name)
            return True
        except Exception as e:
            logger.error("プレイリスト作成エラー: %s", e)
            return False

    def play_playlist(self, playlist_name: str) -> bool:
        """指定された名前のプレイリストを検索して再生する。"""
        if not self.itunes:
            return False
        try:
            target = self._find_playlist_by_name(playlist_name)
            if target:
                logger.debug(
                    "play_playlist target=%s Name=%s Kind=%s",
                    target,
                    getattr(target, 'Name', 'Unknown'),
                    getattr(target, 'Kind', 'Unknown'),
                )
                # 一部のプレイリスト（スマートプレイリスト等）で Play() が直接失敗する場合がある
                # その場合、プレイリスト内の最初の曲を再生することを試みる
                try:
                    target.Play()
                    return True
                except Exception as e:
                    logger.debug("target.Play() failed: %s. Trying first track...", e)
                    tracks = getattr(target, 'Tracks', None)
                    if tracks and tracks.Count > 0:
                        first_track = tracks.Item(1)
                        logger.debug("Playing first track: %s", getattr(first_track, 'Name', 'Unknown'))
                        first_track.Play()
                        return True
                    else:
                        logger.debug("No tracks found in playlist or Tracks is None.")
            else:
                logger.debug("Playlist not found: %s", playlist_name)
        except Exception as e:
            logger.error("プレイリスト再生エラー: %s", e)
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
        """プレイリスト名の一覧を返す（表示用）。フォルダは除外"""
        names: List[str] = []
        try:
            sources = self.itunes.Sources
            for i in range(1, sources.Count + 1):
                source = sources.Item(i)
                if source.Kind == 1:
                    playlists = source.Playlists
                    for j in range(1, playlists.Count + 1):
                        playlist = playlists.Item(j)
                        if self._is_folder_playlist(playlist):
                            continue
                        names.append(playlist.Name)
        except Exception as e:
            logger.error("プレイリスト一覧取得エラー: %s", e)
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
            logger.error("BPM設定エラー: %s", e)
        return False

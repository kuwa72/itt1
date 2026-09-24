import logging

import win32com.client
import pythoncom
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# iTunesタイプライブラリの ITUserPlaylistSpecialKindFolder。
# 旧SDKドキュメントでは 1 と記載されるが、実際のタイプライブラリ(iTunes 1.13)では 4。
IT_USER_PLAYLIST_SPECIAL_KIND_FOLDER = 4


class WindowsMusicController:
    """iTunes OLE操作を管理するクラス。

    再生はローカル再生エンジン（playback_engine）が担うため、
    このクラスはプレイリストの列挙・作成・トラック追加のみを扱う（Issue #39）。
    """

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

    def add_to_playlist(
        self,
        playlist_name: str,
        database_id: int | None = None,
        track_name: str | None = None,
    ) -> str | bool:
        """dbid で指定したトラックをプレイリストに追加。

        再生はローカルエンジンが担うため、iTunes 側の CurrentTrack ではなく
        コントローラーが記録する再生中トラックの dbid でライブラリ内を特定する
        （Issue #39）。
        戻り値: "added"（新規追加）, "already_exists"（既に存在）, False（失敗）
        """
        if not self.itunes:
            logger.error("プレイリスト追加失敗: iTunesに接続されていません (%s)", playlist_name)
            return False

        try:
            if not isinstance(database_id, int):
                logger.error("プレイリスト追加失敗: 再生中のトラックがありません (%s)", playlist_name)
                return False

            target = self._find_playlist_by_name(playlist_name)
            if target is None:
                logger.error("プレイリスト追加失敗: プレイリストが見つかりません: %s", playlist_name)
                return False

            current_track = self._find_library_track(database_id, track_name)
            if current_track is None:
                logger.error(
                    "プレイリスト追加失敗: ライブラリにトラックがありません (dbid=%s, %s)",
                    database_id, track_name,
                )
                return False

            # Search APIで重複チェック（高速）。曲名が無い場合は全件走査にフォールバック
            if track_name and hasattr(target, 'Search'):
                try:
                    search_result = target.Search(track_name, 5)  # 5 = SongNames
                    if search_result and hasattr(search_result, 'Count') and search_result.Count > 0:
                        for i in range(1, search_result.Count + 1):
                            t = search_result.Item(i)
                            try:
                                if getattr(t, 'TrackDatabaseID', None) == database_id:
                                    return "already_exists"
                            except Exception:
                                continue
                except Exception:
                    # Search失敗時は全件走査で重複確認（後述）
                    pass
            else:
                try:
                    for t in getattr(target, 'Tracks', None) or []:
                        if getattr(t, 'TrackDatabaseID', None) == database_id:
                            return "already_exists"
                except Exception:
                    pass

            if self._add_track(target, current_track, playlist_name):
                return "added"
            return False

        except Exception as e:
            logger.error("プレイリスト追加エラー (%s): %s", playlist_name, e)
            return False

    def _find_library_track(self, database_id: int, track_name: str | None = None):
        """LibraryPlaylist から TrackDatabaseID が一致するトラックを返す。

        Search API（曲名）→ dbid 照合の高速パスを先に試し、
        見つからなければライブラリ全件走査にフォールバックする。
        """
        try:
            lib = getattr(self.itunes, 'LibraryPlaylist', None)
            if lib is None:
                return None
            if track_name and hasattr(lib, 'Search'):
                try:
                    res = lib.Search(track_name, 5)  # 5 = SongNames
                    if res and getattr(res, 'Count', 0) > 0:
                        for i in range(1, res.Count + 1):
                            t = res.Item(i)
                            if getattr(t, 'TrackDatabaseID', None) == database_id:
                                return t
                except Exception:
                    pass
            tracks = getattr(lib, 'Tracks', None)
            if tracks is not None:
                for t in tracks:
                    try:
                        if getattr(t, 'TrackDatabaseID', None) == database_id:
                            return t
                    except Exception:
                        continue
        except Exception as e:
            logger.error("ライブラリトラック検索エラー: %s", e)
        return None

    @staticmethod
    def _add_track(target, current_track, playlist_name: str) -> bool:
        """target.AddTrack(current_track) を呼ぶ。成功なら True。

        PyInstaller exe では gen_py(makepy) の静的ラッパーが有効になり、
        Playlists.Item() が基底 IITPlaylist 型を返すため AddTrack が見えない。
        AttributeError 時は IITUserPlaylist へ CastTo してから再試行する。
        """
        try:
            target.AddTrack(current_track)
            return True
        except AttributeError:
            pass
        except Exception as e:
            logger.error("プレイリスト追加エラー (%s): %s", playlist_name, e)
            return False
        try:
            casted = win32com.client.CastTo(target, "IITUserPlaylist")
            casted.AddTrack(current_track)
            return True
        except Exception as e:
            logger.error(
                "プレイリスト追加失敗: 対象にAddTrackがありません: %s (%s)",
                playlist_name, e,
            )
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
        except Exception as e:
            logger.error("プレイリスト検索エラー (%s): %s", name, e)
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

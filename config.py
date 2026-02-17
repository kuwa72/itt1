import json
import os
import platform
from typing import List, Dict, Any
from pydantic import BaseModel, Field
try:
    # pydantic v2
    from pydantic import ConfigDict  # type: ignore
except Exception:  # pragma: no cover
    ConfigDict = dict  # fallback for type hints


class AppConfig(BaseModel):
    """アプリケーション設定（playlists は廃止）"""
    # 既存の config.json に playlists が残っていても無視する
    model_config = ConfigDict(extra='ignore')  # type: ignore

    quick_slots: List[str] = Field(
        default_factory=list,
        description="クイックスロット(最大22: F1–F12, 0–9)に割り当てる既存プレイリスト名"
    )
    skip_seconds: int = Field(
        default=10,
        description="スキップする秒数"
    )
    auto_create_playlists: bool = Field(
        default=True,
        description="起動時にプレイリストを自動作成するか（現状未使用）"
    )
    refresh_interval: float = Field(
        default=0.25,
        description="画面更新間隔（秒）"
    )

    library_xml_path_windows: str = Field(
        default="",
        description="Windows iTunesのライブラリXMLパス（未設定の場合は標準パスを探索）"
    )
    library_xml_path_macos: str = Field(
        default="",
        description="macOS Music/iTunesのライブラリXMLパス（未設定の場合は標準パスを探索）"
    )


class ConfigManager:
    """設定ファイル管理"""
    
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.config = self.load_config()
    
    def load_config(self) -> AppConfig:
        """設定ファイルを読み込む"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return AppConfig(**data)
            except Exception as e:
                print(f"設定ファイル読み込みエラー: {e}")
                return AppConfig()
        
        return AppConfig()
    
    def save_config(self):
        """設定をファイルに保存"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config.dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"設定ファイル保存エラー: {e}")
    
    def get_quick_slots(self) -> List[str]:
        """クイックスロットを取得（playlists フォールバックは廃止）。"""
        slots = self.config.quick_slots or []
        return list(slots)

    def set_quick_slots(self, slots: List[str]):
        """クイックスロットを設定して保存"""
        self.config.quick_slots = list(slots)
        self.save_config()

    def _detect_platform_name(self) -> str:
        system = platform.system().lower()
        if system == "windows":
            return "windows"
        if system == "darwin":
            return "macos"
        return system

    def get_library_xml_path(self) -> str:
        """設定または標準パス推測からライブラリXMLのパスを返す（見つからない場合は空文字）。"""
        platform_name = self._detect_platform_name()
        configured = ""
        if platform_name == "windows":
            configured = (self.config.library_xml_path_windows or "").strip()
        elif platform_name == "macos":
            configured = (self.config.library_xml_path_macos or "").strip()
        if configured:
            return os.path.expanduser(configured)

        candidates: List[str] = []
        if platform_name == "windows":
            base = os.path.expanduser("~")
            candidates.extend([
                os.path.join(base, "Music", "iTunes", "iTunes Music Library.xml"),
                os.path.join(base, "Music", "iTunes", "iTunes Library.xml"),
            ])
        elif platform_name == "macos":
            base = os.path.expanduser("~")
            candidates.extend([
                os.path.join(base, "Music", "iTunes", "iTunes Music Library.xml"),
                os.path.join(base, "Music", "iTunes", "iTunes Library.xml"),
                os.path.join(base, "Music", "Music", "Music Library.xml"),
            ])

        for p in candidates:
            try:
                if os.path.exists(p):
                    return p
            except Exception:
                continue
        return ""

    def check_library_xml_exists(self) -> tuple[str, bool]:
        """ライブラリXMLの存在チェック。("path", exists) を返す。"""
        p = self.get_library_xml_path()
        if not p:
            return "", False
        try:
            return p, os.path.exists(p)
        except Exception:
            return p, False

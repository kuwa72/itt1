import json
import os
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
        """クイックスロット(最大22件)を取得（playlists フォールバックは廃止）。"""
        slots = self.config.quick_slots or []
        return slots[:22]

    def set_quick_slots(self, slots: List[str]):
        """クイックスロット(最大22)を設定して保存"""
        self.config.quick_slots = slots[:22]
        self.save_config()

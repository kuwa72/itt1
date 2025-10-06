#!/usr/bin/env python3
"""
iTunes OLE操作アプリケーション
WindowsのiTunesをワンキー操作で制御し、プレイリストを効率的に構築する
"""

import sys
import os
import argparse
from pathlib import Path

# プロジェクトルートをPythonパスに追加
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from gui_tk import run_gui
from config import ConfigManager


def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(description="iTunes OLE操作アプリケーション (GUI)")
    parser.add_argument("--config", "-c", default="config.json", help="設定ファイルパス")
    
    args = parser.parse_args()
    
    # 設定マネージャーを初期化
    config_manager = ConfigManager(args.config)
    
    # GUIのみ提供
    run_gui()


def run_app_gui_only(config_manager):
    """GUIのみを起動"""
    try:
        run_gui()
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

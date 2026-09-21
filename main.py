#!/usr/bin/env python3
"""
iTunes OLE操作アプリケーション
WindowsのiTunesをワンキー操作で制御し、プレイリストを効率的に構築する
"""

import sys
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


if __name__ == "__main__":
    main()

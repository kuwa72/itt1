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

from tui_interface import iTunesTUI
from gui_tk import run_gui
from config import ConfigManager


def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(description="iTunes OLE操作アプリケーション")
    parser.add_argument("--config", "-c", default="config.json", help="設定ファイルパス")
    parser.add_argument("--setup", action="store_true", help="設定モードで起動")
    parser.add_argument("--no-hook", action="store_true", help="グローバルキーボードフックを無効化して起動（msvcrtのみ）")
    parser.add_argument("--gui", action="store_true", help="Tk GUIモードで起動")
    
    args = parser.parse_args()
    
    # 設定マネージャーを初期化
    config_manager = ConfigManager(args.config)
    
    if args.setup:
        # 設定モード
        setup_mode(config_manager)
    elif args.gui:
        # GUIモード
        run_gui()
    else:
        # 通常モード
        run_app(config_manager, use_global_hook=not args.no_hook)


def setup_mode(config_manager):
    """設定モードで実行"""
    print("=== iTunes Controller 設定モード ===")
    print("現在のプレイリスト設定:")
    
    playlists = config_manager.get_playlists()
    for i, playlist in enumerate(playlists, 1):
        print(f"{i}. {playlist}")
    
    print("\n新しいプレイリスト名をカンマ区切りで入力してください")
    print("例: お気に入り,作業用BGM,集中用,運動用")
    
    try:
        new_playlists = input("> ").strip()
        if new_playlists:
            playlist_list = [p.strip() for p in new_playlists.split(",")]
            config_manager.update_playlists(playlist_list)
            print("設定を保存しました")
    except KeyboardInterrupt:
        print("\nキャンセルしました")
    
    print("通常モードで起動するには --setup オプションを付けずに実行してください")


def run_app(config_manager, use_global_hook: bool = True):
    """アプリケーションを実行"""
    try:
        print("iTunes Controller を起動しています...")
        print("iTunesが起動していることを確認してください")
        
        tui = iTunesTUI(use_global_hook=use_global_hook)
        # 設定を適用
        tui.config_playlists = config_manager.get_playlists()
        
        tui.run()
        
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        print("iTunesが起動していることを確認してください")
        sys.exit(1)


if __name__ == "__main__":
    main()

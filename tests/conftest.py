"""pytest 共通設定。

- リポジトリルートを sys.path に追加
- Linux/macOS 上に存在しない Windows COM モジュール (win32com, pythoncom) を stub 化
  ※ music_controller_windows は import 時にのみ必要。インスタンス化はしない
    （__init__ が iTunes COM へ接続するため）
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

win32com = types.ModuleType("win32com")
win32com.client = MagicMock(name="win32com.client")
sys.modules.setdefault("win32com", win32com)
sys.modules.setdefault("win32com.client", win32com.client)
sys.modules.setdefault("pythoncom", MagicMock(name="pythoncom"))

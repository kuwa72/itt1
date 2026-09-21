"""pytest 共通設定。

- リポジトリルートを sys.path に追加
- Linux/macOS 上に存在しない Windows COM モジュール (win32com, pythoncom) を stub 化
  ※ music_controller_windows は import 時にのみ必要。インスタンス化はしない
    （__init__ が iTunes COM へ接続するため）
- Windows/TUI 依存モジュール (rich, keyboard, msvcrt) を stub 化
  ※ tui_interface の import と __new__ + Mock 注入によるテストを可能にする
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


def _install_fake_tkinter() -> None:
    """最小限のフェイク tkinter モジュールを sys.modules に登録する。

    tkinter が存在しない環境で gui_tk を import 可能にするための stub。
    - `tk.Toplevel` 等のクラス名 → 同名の新規クラス（基底クラス/isinstance に使える）
    - `tk.TclError` 等の *Error → Exception 派生クラス
    - `tk.BOTH` 等の大文字名 → 定数（名前文字列）
    - その他 → MagicMock
    """
    if "tkinter" in sys.modules:
        return

    cache: dict = {}

    def _resolve(fullname: str, attr: str):
        key = f"{fullname}.{attr}"
        if key not in cache:
            if attr.isupper():
                cache[key] = attr
            elif attr.endswith("Error"):
                cache[key] = type(attr, (Exception,), {})
            elif attr and attr[0].isupper():
                cache[key] = type(attr, (), {})
            else:
                cache[key] = MagicMock(name=key)
        return cache[key]

    def _make_module(fullname: str) -> types.ModuleType:
        mod = types.ModuleType(fullname)

        def _getattr(attr: str, _fn: str = fullname):
            if attr.startswith("__") and attr.endswith("__"):
                raise AttributeError(attr)
            return _resolve(_fn, attr)

        mod.__getattr__ = _getattr
        return mod

    tk = _make_module("tkinter")
    for sub in ("ttk", "messagebox", "simpledialog"):
        submod = _make_module(f"tkinter.{sub}")
        setattr(tk, sub, submod)
        sys.modules[f"tkinter.{sub}"] = submod
    sys.modules["tkinter"] = tk


def _install_fake_rich() -> None:
    """rich サブモジュール群を sys.modules に登録する stub。

    tui_interface は `from rich.console import Console` 等を行う。
    各クラスは MagicMock で十分（context manager 等のマジックメソッドも使える）。
    """
    if "rich" in sys.modules:
        return
    rich = types.ModuleType("rich")
    for sub, attrs in {
        "console": ("Console",),
        "layout": ("Layout",),
        "panel": ("Panel",),
        "text": ("Text",),
        "live": ("Live",),
        "table": ("Table",),
        "align": ("Align",),
    }.items():
        mod = types.ModuleType(f"rich.{sub}")
        for attr in attrs:
            setattr(mod, attr, MagicMock(name=f"rich.{sub}.{attr}"))
        setattr(rich, sub, mod)
        sys.modules[f"rich.{sub}"] = mod
    sys.modules["rich"] = rich


def _install_fake_keyboard() -> None:
    """keyboard モジュールの stub。グローバルフックは登録しない。"""
    if "keyboard" in sys.modules:
        return
    keyboard = types.ModuleType("keyboard")
    keyboard.on_press = MagicMock(name="keyboard.on_press")
    keyboard.is_pressed = MagicMock(name="keyboard.is_pressed", return_value=False)
    keyboard.unhook_all = MagicMock(name="keyboard.unhook_all")
    sys.modules["keyboard"] = keyboard


def _install_fake_msvcrt() -> None:
    """msvcrt モジュールの stub。デフォルトはキー入力なし。"""
    if "msvcrt" in sys.modules:
        return
    msvcrt = types.ModuleType("msvcrt")
    msvcrt.kbhit = MagicMock(name="msvcrt.kbhit", return_value=False)
    msvcrt.getwch = MagicMock(name="msvcrt.getwch")
    sys.modules["msvcrt"] = msvcrt


_install_fake_tkinter()
_install_fake_rich()
_install_fake_keyboard()
_install_fake_msvcrt()

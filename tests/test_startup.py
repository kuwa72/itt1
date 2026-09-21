"""Issue #4: --config 引数の引き回しと起動時エラーハンドリングのテスト。

tkinter は conftest.py のフェイクモジュール経由。
gui_tk.tk / gui_tk.ITunesTkApp / gui_tk.messagebox を patch して GUI 非依存で検証する。
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

import gui_tk
import main as main_module
from config import ConfigManager


# --- run_gui の設定引き回し ---

def test_run_gui_uses_given_config_manager():
    """run_gui は渡された ConfigManager を使い、内部で ConfigManager("config.json") を再生成しない"""
    cfg = MagicMock(spec=ConfigManager)
    with patch.object(gui_tk, "tk") as mock_tk, \
         patch.object(gui_tk, "ITunesTkApp") as MockApp, \
         patch.object(gui_tk, "ConfigManager") as MockCM:
        result = gui_tk.run_gui(cfg)

    MockCM.assert_not_called()
    MockApp.assert_called_once_with(mock_tk.Tk.return_value, cfg)
    mock_tk.Tk.return_value.mainloop.assert_called_once()
    assert result == 0


def test_run_gui_without_arg_falls_back_to_default_config():
    """引数なし呼出し（後方互換）では従来通り config.json を読む"""
    with patch.object(gui_tk, "tk"), \
         patch.object(gui_tk, "ITunesTkApp"), \
         patch.object(gui_tk, "ConfigManager") as MockCM:
        gui_tk.run_gui()

    MockCM.assert_called_once_with("config.json")


# --- 起動時クラッシュ対策 ---

def test_run_gui_controller_failure_returns_nonzero_and_shows_message(capsys):
    """コントローラー初期化失敗時、未処理例外ではなく案内メッセージ付きで非0終了"""
    cfg = MagicMock(spec=ConfigManager)
    with patch.object(gui_tk, "tk") as mock_tk, \
         patch.object(gui_tk, "ITunesTkApp",
                      side_effect=RuntimeError("iTunes is not running")), \
         patch.object(gui_tk, "messagebox") as mock_mb:
        result = gui_tk.run_gui(cfg)  # 例外が外へ漏れないこと

    assert result != 0
    mock_mb.showerror.assert_called_once()
    mock_tk.Tk.return_value.mainloop.assert_not_called()
    err = capsys.readouterr().err
    assert "iTunes" in err


def test_run_gui_not_implemented_platform_also_handled():
    """detect_platform の NotImplementedError も同じ経路で処理される"""
    cfg = MagicMock(spec=ConfigManager)
    with patch.object(gui_tk, "tk") as mock_tk, \
         patch.object(gui_tk, "ITunesTkApp",
                      side_effect=NotImplementedError("Unsupported platform: linux")), \
         patch.object(gui_tk, "messagebox") as mock_mb:
        result = gui_tk.run_gui(cfg)

    assert result != 0
    mock_mb.showerror.assert_called_once()
    mock_tk.Tk.return_value.mainloop.assert_not_called()


def test_run_gui_failure_survives_messagebox_error(capsys):
    """messagebox 自体が失敗しても stderr 出力と非0終了は維持される"""
    cfg = MagicMock(spec=ConfigManager)
    with patch.object(gui_tk, "tk"), \
         patch.object(gui_tk, "ITunesTkApp", side_effect=RuntimeError("boom")), \
         patch.object(gui_tk, "messagebox") as mock_mb:
        mock_mb.showerror.side_effect = RuntimeError("no display")
        result = gui_tk.run_gui(cfg)

    assert result != 0
    assert capsys.readouterr().err


# --- main() の --config 引き回し ---

def test_main_passes_config_path_to_config_manager_and_run_gui():
    """--config で指定したパスが ConfigManager に渡り、そのインスタンスが run_gui に渡る"""
    with patch.object(main_module, "ConfigManager") as MockCM, \
         patch.object(main_module, "run_gui", return_value=0) as mock_run, \
         patch.object(sys, "argv", ["main.py", "--config", "custom.json"]):
        with pytest.raises(SystemExit) as exc:
            main_module.main()

    MockCM.assert_called_once_with("custom.json")
    mock_run.assert_called_once_with(MockCM.return_value)
    assert exc.value.code == 0


def test_main_run_gui_unexpected_exception_exits_nonzero(capsys):
    """run_gui が未処理例外を投げてもトレースバックではなくメッセージ付きで非0終了"""
    with patch.object(main_module, "ConfigManager"), \
         patch.object(main_module, "run_gui", side_effect=RuntimeError("boom")), \
         patch.object(sys, "argv", ["main.py"]):
        with pytest.raises(SystemExit) as exc:
            main_module.main()

    assert exc.value.code != 0
    assert "boom" in capsys.readouterr().err

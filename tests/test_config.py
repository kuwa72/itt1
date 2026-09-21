"""config.py の動作テスト（pydantic v2 が利用可能なため実動作で検証）"""

import json
import warnings

from config import AppConfig, ConfigManager


def test_save_and_load_roundtrip(tmp_path):
    cfg_file = tmp_path / "config.json"
    cm = ConfigManager(str(cfg_file))
    cm.config.quick_slots = ["playlist-a", "playlist-b"]
    cm.save_config()

    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert data["quick_slots"] == ["playlist-a", "playlist-b"]
    assert data["skip_seconds"] == 10

    cm2 = ConfigManager(str(cfg_file))
    assert cm2.get_quick_slots() == ["playlist-a", "playlist-b"]


def test_set_quick_slots_persists(tmp_path):
    cfg_file = tmp_path / "config.json"
    cm = ConfigManager(str(cfg_file))
    cm.set_quick_slots(["x"])
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["quick_slots"] == ["x"]


def test_save_config_uses_pydantic_v2_dump(tmp_path):
    """save_config が非推奨の .dict() ではなく model_dump() 経由で保存されること"""
    cfg_file = tmp_path / "config.json"
    cm = ConfigManager(str(cfg_file))
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        cm.save_config()
    deprecated = [w for w in rec if "deprecat" in str(w.message).lower()]
    assert not deprecated, f"非推奨API警告が発生: {[str(w.message) for w in deprecated]}"


def test_load_config_ignores_legacy_playlists_key(tmp_path):
    """旧 config.json に残る playlists キーを無視して読み込めること"""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"playlists": ["legacy"], "skip_seconds": 15}),
        encoding="utf-8",
    )
    cm = ConfigManager(str(cfg_file))
    assert cm.config.skip_seconds == 15
    assert isinstance(cm.config, AppConfig)


def test_load_config_missing_file_returns_default(tmp_path):
    cm = ConfigManager(str(tmp_path / "nonexistent.json"))
    assert cm.config.quick_slots == []
    assert cm.config.skip_seconds == 10

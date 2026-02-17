from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.table import Table
from rich.align import Align
import keyboard
import threading
import time
from typing import List, Dict, Any
from itunes_controller import iTunesController
from config import ConfigManager
import msvcrt


class iTunesTUI:
    """iTunes操作のためのTUIインターフェース"""
    
    def __init__(self, config_manager: ConfigManager = None, use_global_hook: bool = True):
        self.console = Console()
        self.itunes = iTunesController()
        self.config_manager = config_manager or ConfigManager()
        self.use_global_hook = use_global_hook
        self.running = True
        self.current_track_info = {}
        self.playlists = []
        # ASCII keyboard top 3 rows: number row + QWERTY + ASDF (per-page)
        self.slot_keys: List[str] = list("1234567890-=") + list("qwertyuiop[]\\") + list("asdfghjkl;'")
        self.bank_size = len(self.slot_keys)
        self.slot_bank = 0
        self._last_bank_toggle_at = 0.0
        self.config_playlists = self.config_manager.get_quick_slots()
        self.selected_playlist = 0
        self.skip_seconds = self.config_manager.config.skip_seconds
        xml_path, xml_exists = ("", False)
        try:
            xml_path, xml_exists = self.config_manager.check_library_xml_exists()
        except Exception:
            xml_path, xml_exists = ("", False)
        if xml_path and xml_exists:
            self.last_action = f"XML検出: {xml_path}"
        elif xml_path and not xml_exists:
            self.last_action = f"XML未検出: {xml_path}"
        else:
            self.last_action = "XML未設定"
        
        # キーバインディング（操作は最下段に寄せる）
        self.key_bindings = {
            'space': self.toggle_play_pause,
            'right': self.skip_forward,
            'left': self.skip_backward,
            'up': self.previous_track,
            'down': self.next_track,
            # bottom row ops
            'z': self.toggle_play_pause,
            'x': self.quit,
            'c': self.create_config_playlists,
            'v': self.refresh_playlists,
            'b': self.pick_quick_slots,
            ',': self.toggle_slot_bank,
            '.': self.toggle_slot_bank,
        }
    
    def toggle_play_pause(self):
        """再生/一時停止を切り替える"""
        self.itunes.play_pause()
        self.last_action = "再生/一時停止"
    
    def skip_forward(self):
        """10秒スキップ"""
        self.itunes.skip_forward(self.skip_seconds)
        self.last_action = f"+{self.skip_seconds}秒"
    
    def skip_backward(self):
        """10秒戻る"""
        self.itunes.skip_backward(self.skip_seconds)
        self.last_action = f"-{self.skip_seconds}秒"
    
    def next_track(self):
        """次のトラック"""
        self.itunes.play_next_track()
        self.last_action = "次の曲"
    
    def previous_track(self):
        """前のトラック"""
        self.itunes.play_previous_track()
        self.last_action = "前の曲"
    
    def add_to_playlist(self, playlist_index: int):
        """指定されたプレイリストに現在のトラックを追加"""
        if not (0 <= playlist_index < len(self.config_playlists)):
            self.last_action = "未設定スロット"
            return
        playlist_name = self.config_playlists[playlist_index]
        if not playlist_name:
            self.last_action = "未設定スロット"
            return
        success = self.itunes.add_to_playlist(playlist_name)
        if success:
            self.console.print(f"[green]✓ {playlist_name}に追加しました[/green]")
            self.last_action = f"追加: {playlist_name}"
        else:
            self.console.print(f"[red]✗ 追加に失敗しました: {playlist_name}[/red]")
            self.last_action = f"追加失敗: {playlist_name}"
    
    def refresh_playlists(self):
        """プレイリスト情報を更新"""
        self.playlists = self.itunes.get_playlists()
    
    def create_config_playlists(self):
        """設定されたプレイリストを作成"""
        for playlist_name in self.config_playlists:
            self.itunes.create_playlist(playlist_name)
        self.refresh_playlists()
        self.console.print("[green]✓ プレイリストを作成しました[/green]")
        self.last_action = "プレイリスト作成"

    def pick_quick_slots(self):
        """iTunes上の既存プレイリストからクイックスロットを選択"""
        all_names = self.itunes.get_all_playlists()
        if not all_names:
            self.console.print("[red]iTunesのプレイリストを取得できませんでした[/red]")
            self.last_action = "取得失敗"
            return
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("番号", style="cyan", width=6)
        table.add_column("プレイリスト名", style="white")
        for i, name in enumerate(all_names, 1):
            table.add_row(str(i), name)
        self.console.print(Panel(table, title="クイックスロットに割り当てるプレイリスト番号をカンマ区切りで入力（順番は入力順）", border_style="magenta"))
        try:
            ans = self.console.input(": ")
            if not ans:
                return
            indices = []
            for part in ans.split(','):
                part = part.strip()
                if not part:
                    continue
                if part.isdigit():
                    idx = int(part)
                    if 1 <= idx <= len(all_names):
                        indices.append(idx - 1)
            # 重複を除いて最大5件
            unique = []
            for i in indices:
                if i not in unique:
                    unique.append(i)
            new_slots = [all_names[i] for i in unique]
            self.config_manager.set_quick_slots(new_slots)
            self.config_playlists = new_slots
            self.console.print("[green]✓ クイックスロットを更新しました[/green]")
            self.last_action = "クイックスロット更新"
        except KeyboardInterrupt:
            pass

    def toggle_slot_bank(self):
        now = time.monotonic()
        if (now - self._last_bank_toggle_at) < 0.25:
            return
        self._last_bank_toggle_at = now
        self.slot_bank = 0 if self.slot_bank else 1
        self.last_action = f"バンク: {self.slot_bank + 1}"
    
    def quit(self):
        """アプリケーションを終了"""
        self.running = False
        self.last_action = "終了"
    
    def update_track_info(self):
        """トラック情報を更新"""
        self.current_track_info = self.itunes.get_current_track_info()
    
    def create_layout(self) -> Layout:
        """レイアウトを作成"""
        layout = Layout()
        
        # 上部: 現在のトラック情報
        track_info = self.get_track_display()
        
        # 中央: プレイリスト情報
        playlists_display = self.get_playlists_display()
        
        # 下部: 操作説明
        help_display = self.get_help_display()
        
        layout.split_column(
            Layout(Panel(track_info, title="現在のトラック", border_style="blue"), name="track", size=6),
            Layout(Panel(playlists_display, title="プレイリスト", border_style="green"), name="playlists", size=10),
            Layout(Panel(help_display, title="操作説明", border_style="yellow"), name="help", size=8)
        )
        
        return layout
    
    def get_track_display(self) -> Text:
        """現在のトラック情報を表示"""
        if not self.current_track_info:
            return Text("トラック情報を取得中...", style="dim")
        
        name = self.current_track_info.get('name', '不明')
        artist = self.current_track_info.get('artist', '不明')
        album = self.current_track_info.get('album', '不明')
        is_playing = self.current_track_info.get('is_playing', False)
        position = self.current_track_info.get('position', 0)
        duration = self.current_track_info.get('duration', 0)
        
        status = "▶ 再生中" if is_playing else "⏸ 一時停止"
        
        text = Text()
        text.append(f"{status}\n", style="bold cyan" if is_playing else "dim")
        text.append(f"曲名: {name}\n", style="bold white")
        text.append(f"アーティスト: {artist}\n", style="white")
        text.append(f"アルバム: {album}\n", style="dim")
        
        if duration > 0:
            pos_min, pos_sec = divmod(int(position), 60)
            dur_min, dur_sec = divmod(int(duration), 60)
            text.append(f"時間: {pos_min:02d}:{pos_sec:02d} / {dur_min:02d}:{dur_sec:02d}", style="dim")
        if not self.config_playlists:
            text.append("\n[ヒント] 'p' でクイックスロットにプレイリストを割り当てできます", style="yellow")
        text.append(f"\n最終アクション: {self.last_action}", style="cyan")
        
        return text
    
    def get_playlists_display(self) -> Table:
        """プレイリスト情報を表示"""
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("番号", style="cyan", width=4)
        table.add_column("キー", style="cyan", width=4)
        table.add_column("プレイリスト名", style="white")
        table.add_column("状態", style="green", width=8)

        base = self.slot_bank * self.bank_size
        for i in range(self.bank_size):
            idx = base + i
            playlist_name = self.config_playlists[idx] if idx < len(self.config_playlists) else "(未設定)"
            key_label = self.slot_keys[i]
            if playlist_name == "(未設定)":
                status = "未設定"
                style = "dim"
            else:
                status = "存在" if any(p['name'] == playlist_name for p in self.playlists) else "未作成"
                style = "green" if status == "存在" else "red"
            table.add_row(str(idx + 1), key_label, playlist_name, Text(status, style=style))
        
        return table
    
    def get_help_display(self) -> Text:
        """操作説明を表示"""
        text = Text()
        text.append("再生制御:\n", style="bold yellow")
        text.append("スペース: 再生/一時停止  ")
        text.append(f"→: +{self.skip_seconds}秒  ")
        text.append(f"←: -{self.skip_seconds}秒  ")
        text.append("↑: 前の曲  ")
        text.append("↓: 次の曲\n")
        
        text.append("プレイリスト追加:\n", style="bold yellow")
        text.append("上3段キー(数字/QWERTY/ASDF): 現在バンクのクイックスロットに追加\n")
        text.append("Ctrl+上3段キー: バンク2のクイックスロットに追加\n")
        text.append(",/. : バンク切替 (フォールバック)\n")
        
        text.append("その他:\n", style="bold yellow")
        text.append("z: 再生/一時停止  ")
        text.append("v: プレイリスト更新  ")
        text.append("c: プレイリスト作成  ")
        text.append("b: クイックスロット選択  ")
        text.append("x: 終了")
        
        return text
    
    def handle_key_press(self, key, ctrl: bool = False):
        """キー押下を処理"""
        k = self.normalize_key(str(key))
        eff_bank = 1 if ctrl else self.slot_bank
        if k in self.slot_keys:
            idx = eff_bank * self.bank_size + self.slot_keys.index(k)
            self.add_to_playlist(idx)
            return
        if k in self.key_bindings:
            self.key_bindings[k]()

    def normalize_key(self, key: str) -> str:
        k = key.lower()
        alias = {
            'minus': '-',
            'equal': '=',
            'left bracket': '[',
            'right bracket': ']',
            'backslash': '\\',
            'semicolon': ';',
            'apostrophe': "'",
            'comma': ',',
            'period': '.',
        }
        return alias.get(k, k)
    
    def run(self):
        """メインループ"""
        self.console.clear()
        self.console.print("[bold green]iTunes Controller 起動中...[/bold green]")
        if str(self.last_action).startswith("XML"):
            self.console.print(f"[cyan]{self.last_action}[/cyan]")
        
        # 初期化
        self.refresh_playlists()
        
        # キーボードフックを設定（必要時のみ）
        if self.use_global_hook:
            def on_key_press(event):
                name = getattr(event, 'name', '')
                if str(name).lower() in ("ctrl", "left ctrl", "right ctrl"):
                    return False
                is_ctrl = False
                try:
                    is_ctrl = keyboard.is_pressed('ctrl')
                except Exception:
                    is_ctrl = False
                self.handle_key_press(name, ctrl=is_ctrl)
                return False  # イベントを消費
            keyboard.on_press(on_key_press)
        
        # msvcrt フォールバック: グローバルフックが不安定な環境向け
        def local_key_reader():
            special_map = {
                'H': 'up',
                'P': 'down',
                'K': 'left',
                'M': 'right',
            }
            while self.running:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch == '\x00' or ch == '\xe0':
                        # 特殊キー（矢印など）
                        code = msvcrt.getwch()
                        mapped = special_map.get(code)
                        if mapped and mapped in self.key_bindings:
                            self.handle_key_press(mapped)
                    else:
                        # 通常キー
                        if ch == ' ':
                            keyname = 'space'
                        else:
                            keyname = ch.lower()
                        if keyname in self.key_bindings:
                            self.handle_key_press(keyname)
                time.sleep(0.02)
        t = threading.Thread(target=local_key_reader, daemon=True)
        t.start()
        
        # メインループ
        try:
            with Live(self.create_layout(), refresh_per_second=4, console=self.console) as live:
                while self.running:
                    self.update_track_info()
                    live.update(self.create_layout())
                    time.sleep(0.25)
        
        except KeyboardInterrupt:
            pass
        finally:
            keyboard.unhook_all()
            self.console.print("[yellow]iTunes Controller を終了しました[/yellow]")


if __name__ == "__main__":
    tui = iTunesTUI()
    tui.run()

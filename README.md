# iTunes Controller
Windows の iTunes / macOS の Music アプリを制御して、再生操作とプレイリスト構築を効率化する GUI アプリ。

## 機能

- **ワンキー操作**: 再生/一時停止、スキップ、前後曲などを1キーで実行
- **クイックスロット F1–F12, 0–9**: 最大22個のプレイリストへ即追加
- **GUI (Tk)**: 現在の曲/時間/BPM 表示、割当キー表示のスロット一覧、割当ダイアログ付き
- **スロット割当ダイアログ**: スクロール付きリストで既存プレイリストから最大22件選択、既存割当は自動選択、選択数カウンタ表示
- **BPM タップ**: Tap/Reset で簡易 BPM 測定
- **設定カスタマイズ**: 設定ファイルでプレイリスト名や動作を変更

## 必要な環境

- **Windows**: Python 3.10以上、iTunes（Windows版）
- **macOS**: Python 3.10以上、Music アプリ、Tkinter

## インストール

1. リポジトリをクローン
```bash
git clone <repository-url>
cd itt1
```

2. システム依存関係のインストール

**macOS の場合:**

Homebrewで直接Pythonを使用している場合:
```bash
# Tkinterのインストール（Python 3.13の場合）
brew install python-tk@3.13

# 他のバージョンの場合は @3.XX を適宜変更
# 例: brew install python-tk@3.12
```

asdfでPythonを管理している場合:
```bash
# まずtcl-tkをインストール
brew install tcl-tk

# 既存のPythonをアンインストール
asdf uninstall python <version>

# Tkinterサポート付きで再インストール
PYTHON_CONFIGURE_OPTS="--with-tcltk-includes='-I/opt/homebrew/opt/tcl-tk/include' --with-tcltk-libs='-L/opt/homebrew/opt/tcl-tk/lib -ltcl9.0 -ltk9.0'" asdf install python <version>

# 例: Python 3.12.10の場合
# PYTHON_CONFIGURE_OPTS="--with-tcltk-includes='-I/opt/homebrew/opt/tcl-tk/include' --with-tcltk-libs='-L/opt/homebrew/opt/tcl-tk/lib -ltcl9.0 -ltk9.0'" asdf install python 3.12.10
```

3. Python依存関係をインストール

**共通の依存関係:**
```bash
pip install -r requirements.txt
```

**Windows の場合、追加で:**
```bash
pip install -r requirements-windows.txt
```

## 使い方

### 起動
```bash
python main.py
# または Windows バッチ: run_gui.bat
```

（TUIやセットアップモードは廃止。GUIのみ提供）

### GUI 操作キー（US 101 キーボード想定）

| キー | 機能 |
|------|------|
| スペース | 再生/一時停止 |
| → / ← | ±skip秒（既定: 10秒） |
| ↑ / ↓ | 前の曲 / 次の曲 |
| F1–F12, 0–9（テンキー対応） | クイックスロット1–22に追加 |
| p | クイックスロット割当ダイアログを開く |
| r | プレイリスト情報の再取得 |
| c | プレイリストを1件新規作成（名前入力） |
| t / x | BPM: Tap / Reset |
| q | 終了 |

## 設定

`config.json` ファイルで以下の設定が可能：

- `playlists`: ワンキーで追加できるプレイリスト名（任意数・先頭から使用）
- `quick_slots`: クイックスロット(最大22: F1–F12, 0–9)に割り当てる既存プレイリスト名（最大22）
- `skip_seconds`: スキップする秒数（デフォルト: 10秒）
- `auto_create_playlists`: 起動時にプレイリストを自動作成
- `refresh_interval`: 画面更新間隔（秒）

## 使い方の流れ

1. iTunes を起動
2. アプリを起動（GUI なら `--gui`）
3. 曲を再生し、気に入ったら F1–F12, 0–9 のキーでスロットに追加
4. スロット割当を変えたいときは `p` でダイアログを開き、最大22件まで選択（既存割当は選択済み・選択数表示）
5. 新しいプレイリストを作りたいときは `c` で名前入力して作成

## トラブルシューティング

### iTunesに接続できない
- iTunesが起動していることを確認
- iTunesのバージョンが古すぎないか確認

### プレイリストに追加できない
- プレイリストが存在するか確認（cキーで作成可能）
- iTunesでプレイリストの権限を確認

### キーが反応しない
- 他のアプリケーションがキーを占有していないか確認
- 管理者権限で実行してみる

## ライセンス

MIT License

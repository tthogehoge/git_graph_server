# git_graph_server

ライセンス: [MIT License](LICENSE)

Linux 向けのローカルサーバーです。Git のコミットグラフ、ログ、差分、作業ツリーの変更をブラウザで確認できます。ブランチ、タグ、merge、rebase、stash などの操作にも対応しています。

## 必要なもの

対応プラットフォームは Linux です。

- Python 3（標準ライブラリのみ）
- Git
- Graphviz の `dot`

## 起動

```sh
python git_graph_server.py --open
```

既定の URL は `http://127.0.0.1:8000/` です。

```sh
python git_graph_server.py -C /path/to/repo --port 8080
```

対象の rev を指定しない場合は、全ブランチ・タグを表示します。終了は `Ctrl+C`、または画面左上の「終了」ボタンです。

## 表示言語

起動時の環境変数 `LANG` で切り替えます。未指定または未対応の言語は日本語になります。

```sh
LANG=ja_JP.UTF-8 python git_graph_server.py
LANG=en_US.UTF-8 python git_graph_server.py
```

## 主なオプション

| オプション | 説明 |
| --- | --- |
| `-C PATH` | 対象リポジトリ（既定: カレントディレクトリ） |
| `--host HOST` / `--port PORT` | 待ち受けアドレス / ポート (既定 8000。使用中なら空きポートを自動選択。`--port` を指定して使用中ならエラー終了) |
| `--open` | 起動後にブラウザで開く |
| `--no-remotes` / `--no-tags` | リモート / タグを非表示 |
| `--no-wip` | 未コミット変更の WIP ノードを非表示 |
| `--no-count` | 省略コミット数を計算しない |
| `--full-layout` | Graphviz のレイアウト最適化を省略しない |
| `--read-only` | 書き込み操作を無効化 |
| `--lan` | LAN から接続可能にして書き込みを許可 |
| `--dot` | dot を出力して終了 |

## 注意

書き込み操作は既定で localhost からのみ有効です。`--lan` と `--allow-write` は認証なしで操作を受け付けるため、信頼できるネットワークでのみ使用してください。

reset、checkout、stash の破棄などはデータを失う可能性があります。重要なリポジトリではバックアップを取り、操作内容を確認してから実行してください。

## スクリーンショット

![グラフ](screenshot/Screenshot1.png)
![コミット](screenshot/Screenshot2.png)
![差分](screenshot/Screenshot3.png)

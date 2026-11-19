# static 配信・再編成 実施計画

対象: `git_graph_server`（`git_graph/static/` と、それを組み立てる `i18n.py` / `pages.py` / `server.py`）

## 1. 目的

- JS を連結して `app.js` を作る処理、JS / CSS を HTML に埋め込む処理をやめ、ふつうの個別ファイルとして配信する。
- `static/` の中を、共通 / graph 用 / files 用 / diff 用などに整理する。
- 修正は `git am` で取り込める patch として出す。

## 2. 現状（調査結果）

| 項目 | 現状 |
| --- | --- |
| グラフ画面の JS | `i18n.py` の `APP_JS` が約16ファイルを連結し、`/static/app.js` として配信 |
| グラフ画面の CSS | `i18n.py` の `APP_CSS` が `menubar.css` + `wip_panel.css` + `app.css` を連結（`app.css` の59行目・68行目に `commit_log.css` / `log_select.css` を差し込み） |
| `/files` | `files.html` の `<style>` / `<script>` に、`FILES_JS`（連結）と各部品を `pages.py` が埋め込み |
| `/diff` | `pages.py` の f 文字列に、CSS / JS を直接埋め込み |
| `/merge` | CSS は HTML 内、JS・エディタ本体は `pages.py` が埋め込み |
| `/setup` | HTML・CSS・JS が1ファイルで、翻訳とパスを差し込み |
| 動的な値 | `I18N`、`WIP_ENABLED`、`CAN_COPY`、`DESKTOP`、`T`、`MERGE_PATH` などが連結・埋め込みの途中に入る |
| 確認済み | `'use strict'` なし。`__HASH__` は static に残っていない。属性の `onclick=` は `js/09_boot.js` の1箇所のみ |

JS は約7000行・30ファイル超で、部品間がトップレベルの関数や const をグローバルに共有している。

## 3. 方針

1. まず **通常の `<script src>` / `<link>` で個別配信**する（グローバル共有はそのままなので、動作は変わらない）。
2. ES modules は、連結・埋め込みが無くなった後に、必要なら**ページ単位で段階的に**行う（最後の任意フェーズ）。
   - いきなり全体を ES modules 化すると、全部品を import / export に書き換える大改修になり、リスクが大きい。
3. 動的な値は、小さな動的スクリプトに分離する。
   - 翻訳: `/static/i18n.js`（`const I18N = …`）
   - フラグ類・`T`・`MERGE_PATH`: HTML 内の小さなインライン `<script>`

## 4. `static/` の新しい構成案

```
static/
  common/   toast, copy, pane_resize, log_select(js/css), commit_log(js/css), log_graph,
            patch_ops, other_lists, menubar(css/html), page_menubar,
            wip_panel(js/css/mount), diff_dialog
  graph/    index.html, app.css, js/01..09
  files/    files.html, files.css, js/01..07
  diff/     diff.css, diff_*.js, ext_diff, wip_diff_remote
  merge/    merge.html, merge.css（HTML から分離）, merge.js
  setup/    setup.html, setup.css, setup.js（同上）
  vendor/   既存のまま（merge_editor.js, text_editor.js）
```

- 配信 URL は `/static/<この構成のパス>` にする。

## 5. 実施フェーズ

### Phase 0: 土台の確認
- pygit2 と graphviz を用意し、既存テストの結果を基準として取る。
- 移動・外部化の前後で、レンダリング結果を比較できるようにする。

### Phase 1: 静的ファイルの汎用配信（`server.py`）
- `/static/<path>` の汎用ハンドラを追加する。
  - 許可する拡張子は `.js` / `.css` のみ（`.html` テンプレートは配信しない）。
  - `..` などのパス脱出は拒否する（`STATIC_DIR` の外は 404）。
  - `ETag` と `Cache-Control: no-cache` を付け、`If-None-Match` には 304 を返す。
  - ファイルは更新時刻でキャッシュし、JS / CSS の編集はリロードだけで反映する（テンプレートと翻訳は従来どおり再起動が必要）。
- `/static/i18n.js` を動的に生成する（内容は `client_translations()`）。
- 既存の `/static/app.js` と `/static/app.css` は、Phase 4 まで残す。

### Phase 2: `static/` の再編成（移動だけのコミット）
- `git mv` のみで、内容の変更は混ぜない。
- 追従して直すもの: `i18n.py` / `pages.py` のパス、テスト、`DEVELOPMENT.md`、`build.bat`（PyInstaller の同梱パス）。

### Phase 3: ページごとに外部化（1ページ1コミット）
順序: graph → files → diff → merge → setup

- HTML に `<link>` / `<script src>` を直接書く。順序は、今の連結順と同じにする。
- JS 内のプレースホルダ（`/*__I18N__*/`、`/*__FILE_I18N__*/`、`/*__MERGE_I18N__*/`、`/*__SETUP_I18N__*/`）を無くす。
- 動的な値は、ページ先頭の小さなインライン `<script>` に置く（`WIP_ENABLED`、`CAN_COPY`、`DESKTOP`、`T`、`MERGE_PATH`）。
- `/files` 末尾のマウント用の数行（WIP パネルの接続）は、独立した静的 JS にする。
- `/diff` は、条件付きで入れるスクリプト（外部差分・push/pull・差分エディタなど）を、タグを返す小さなヘルパー経由にする。
- **CSS の順序**
  - `app.css` を、差し込み位置（59行目・68行目）で前半 / 後半に分け、間に `commit_log.css` と `log_select.css` を挟む。詳細度が同じルールは後勝ちなので、見た目の優先順位を保つため。
  - `files.css` は差し込みが末尾なので、そのまま後ろに並べればよい。

### Phase 4: Python 側の掃除
- 削除するもの: `APP_JS`、`FILES_JS`、`APP_CSS`、`render_app_js`、各ページのプレースホルダ置換、`i18n.py` の連結コード。
- `text_editor.js` 用の専用ルートを、汎用配信に統合する。
- 旧 `/static/app.js` / `/static/app.css` を削除する。

### Phase 5: テストの更新
- 今のテストは、ページ文字列に JS が丸ごと入っている前提（`render_*_page()` の結果への `assertIn`、`APP_JS` の参照など、60か所超）。
- テスト用ヘルパー `page_with_assets(page)` を作る。ページが参照する script / link を、実際の配信と同じ経路で集めて連結する。これでテストの意図を変えずに置き換える。
- `test_static_assets.py` を、次の2点を検査する形に作り直す。
  - ページが参照するファイルが存在すること。
  - どのページからも参照されず、動的にも読まれないファイルが無いこと。
- `tests/wip_panel_behavior.js` のパスを更新する。
- `--check-i18n` は `rglob` なので、移動後もそのまま動く（念のため確認する）。

### Phase 6: ビルドとドキュメント
- `build.bat`（PyInstaller）の同梱パスを、新構成に合わせる。
- `DEVELOPMENT.md` の構成表と「画面や翻訳を変更する」を更新する。
- README に影響があれば更新する。

### Phase 7（任意）: ES modules 化
- 依存の少ない `common/` から、1ファイルずつ `import` / `export` に変える。
- 注意点: グローバル共有が壊れる、`type="module"` は遅延実行になる、循環 import、インライン設定との受け渡し。

## 6. リスクと対策

| リスク | 対策 |
| --- | --- |
| リクエスト数が約35本に増える（HTTP/1.0 で毎回接続し直す） | Windows で実測し、遅ければバージョン付き URL + 長期キャッシュに切り替える |
| スクリプト順序の崩れ | 現在の連結順を厳守する。`node --check` と画面の動作確認で検証する |
| CSS の優先順位が変わる | Phase 3 の分割位置（`app.css` の差し込み2か所）で保つ |
| 1ファイルが例外を投げたときの挙動が変わる（連結時は以降が止まり、個別だと続行する） | 障害時だけの違いなので許容する |
| 大量のテスト修正 | `page_with_assets` で機械的に置き換え、フェーズごとにテストを通す |
| PyInstaller / pywebview での同梱漏れ | Phase 6 で確認する（Windows の実機確認はユーザーにお願いする） |

## 7. 納品形態

`git am` で適用できる patch を、1つのファイル（`git format-patch` 形式の連番）にまとめて、ダウンロード用に出す。

1. 静的配信の基盤（Phase 1）とテストヘルパー
2. ファイル移動（Phase 2）
3. ページごとの外部化（Phase 3、ページごとに分割）
4. 掃除（Phase 4）・テスト整理（Phase 5）・ドキュメント（Phase 6）

## 8. 決めておくこと

- `static/` の構成案（4章）でよいか。

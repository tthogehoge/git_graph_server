# ワークツリー (リポジトリ) ごとの表示設定。
# .git/git-graph.json に置く。別リポジトリや別マシンでも、同じクローンなら同じ設定になる。
# ブラウザの localStorage は端末共通なので、リポジトリ固有の値はこちらを優先する。
import json
import os
import subprocess
import threading
from pathlib import Path
from . import app_state as state
from .i18n import UserError, tr
from .routing import route

PREFS_NAME = "git-graph.json"

# 画面の ON/OFF (真偽)
UI_BOOL_KEYS = (
    "show_merges",   # グラフのマージ表示
    "editor_vim",    # エディタ Vim モード
    "editor_wrap",   # エディタ折り返し
    "md_preview",    # Markdown プレビュー
    "md_breaks",     # Markdown 改行を <br> に
    "html_preview",  # HTML プレビュー
    "html_js",       # HTML プレビューで script を許可
)

# グラフ描画 (数値・真偽)。ファイルにあれば起動時 / 保存時に state.OPTS へ反映する
GRAPH_KEYS = (
    "cluster_min_nodes",  # このノード数を超えたらクラスター表示 (0=しない)
    "merge_max_nodes",    # マージ表示時のノード上限 (0=無制限)
    "full_layout",        # 大きなグラフでも厳密レイアウト
    "no_count",           # 変更ファイル数を数えない
)

_lock = threading.Lock()


def _git_dir():
    """現在の state.REPO の git ディレクトリ (絶対パス)。無ければ None。"""
    if not state.REPO or state.NEED_REPO:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", state.REPO, "rev-parse", "--absolute-git-dir"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
    except FileNotFoundError:
        return None
    if r.returncode != 0:
        return None
    path = (r.stdout or "").strip()
    return path or None


def prefs_path():
    """設定ファイルのパス。.git/git-graph.json。git-dir が取れなければ None。"""
    gd = _git_dir()
    return os.path.join(gd, PREFS_NAME) if gd else None


def _normalize(data):
    """ファイルの中身を、知っているキーだけ残した dict にする。"""
    if not isinstance(data, dict):
        raise ValueError("top level must be an object")
    out = {}
    for key in UI_BOOL_KEYS:
        if key in data:
            out[key] = bool(data[key])
    for key in GRAPH_KEYS:
        if key not in data:
            continue
        val = data[key]
        if key in ("full_layout", "no_count"):
            out[key] = bool(val)
        else:
            if val is None:
                continue
            if not isinstance(val, int) or isinstance(val, bool):
                raise ValueError(f'"{key}" must be an integer')
            if val < 0:
                raise ValueError(f'"{key}" must be >= 0')
            out[key] = val
    return out


def read_prefs_file(path=None):
    """設定ファイルを読む。無ければ {}。壊れていれば UserError。"""
    path = path or prefs_path()
    if not path or not os.path.isfile(path):
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return _normalize(data)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        raise UserError("prefs_invalid", path=str(path), error=str(e))


def write_prefs_file(prefs, path=None):
    """設定をファイルに書く (知っているキーだけ)。"""
    path = path or prefs_path()
    if not path:
        raise UserError("prefs_no_git_dir")
    data = _normalize(prefs)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    parent = os.path.dirname(path)
    try:
        os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        raise UserError("prefs_write_failed", path=path, error=str(e))
    return data


def apply_graph_prefs(prefs):
    """prefs のグラフ項目を state.OPTS に載せる。キーが無い項目は触らない。"""
    if not state.OPTS or not prefs:
        return
    for key in GRAPH_KEYS:
        if key in prefs:
            setattr(state.OPTS, key, prefs[key])


def load_into_state():
    """開いたリポジトリの設定を読み、state と OPTS に反映する。"""
    path = prefs_path()
    state.WORKTREE_PREFS_PATH = path or ""
    try:
        prefs = read_prefs_file(path) if path else {}
    except UserError:
        # 壊れていてもサーバーは起動し、空として扱う (メッセージは API で分かる)
        prefs = {}
    state.WORKTREE_PREFS = dict(prefs)
    apply_graph_prefs(prefs)
    return prefs


def merge_prefs(updates):
    """updates を現在の設定にマージして保存し、OPTS も更新する。戻り値は新しい prefs。"""
    with _lock:
        path = prefs_path()
        if not path:
            raise UserError("prefs_no_git_dir")
        current = dict(state.WORKTREE_PREFS or {})
        # ファイルが手で直されていれば取り込む
        try:
            on_disk = read_prefs_file(path)
            current.update(on_disk)
        except UserError:
            pass
        for key, val in (updates or {}).items():
            if key in UI_BOOL_KEYS:
                current[key] = bool(val)
            elif key in GRAPH_KEYS:
                if key in ("full_layout", "no_count"):
                    current[key] = bool(val)
                else:
                    if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                        raise UserError("prefs_invalid", path=path, error=f'"{key}" must be a non-negative integer')
                    current[key] = val
            # 未知のキーは無視
        written = write_prefs_file(current, path)
        state.WORKTREE_PREFS = dict(written)
        state.WORKTREE_PREFS_PATH = path
        apply_graph_prefs(written)
        try:
            from . import graph as graph_module
            graph_module._cache.clear()
        except Exception:
            pass
        return written


def prefs_js_literal():
    """ページに埋め込む WORKTREE_PREFS の JS オブジェクトリテラル。"""
    prefs = state.WORKTREE_PREFS or {}
    return json.dumps(prefs, ensure_ascii=False).replace("</", "<\\/")


@route("/api/prefs", method="GET")
def api_prefs_get(_query=None):
    """ワークツリー設定の取得。path はファイルの場所 (無ければ空)。"""
    return {
        "prefs": dict(state.WORKTREE_PREFS or {}),
        "path": state.WORKTREE_PREFS_PATH or prefs_path() or "",
    }


@route("/api/prefs", method="POST")
def api_prefs_post(body):
    """ワークツリー設定をファイルへ書く。画面からの明示保存用。
    prefs: 更新するキー。include_opts が真 (既定) のときは、いまの state.OPTS の
    グラフ用の値 (cluster_min_nodes など) もファイルに含める。"""
    if state.NEED_REPO:
        raise UserError("setup_required")
    if not isinstance(body, dict):
        raise ValueError("bad body")
    updates = body.get("prefs", body)
    if not isinstance(updates, dict):
        raise ValueError("bad body")
    updates = dict(updates)
    if body.get("include_opts", True) and state.OPTS is not None:
        for key in GRAPH_KEYS:
            if hasattr(state.OPTS, key):
                updates[key] = getattr(state.OPTS, key)
    written = merge_prefs(updates)
    return {
        "ok": True,
        "prefs": written,
        "path": state.WORKTREE_PREFS_PATH or "",
        "message": tr_saved(path=state.WORKTREE_PREFS_PATH or ""),
    }


def tr_saved(path=""):
    return tr("prefs_saved", path=path or "")


__all__ = [
    "PREFS_NAME", "UI_BOOL_KEYS", "GRAPH_KEYS",
    "prefs_path", "read_prefs_file", "write_prefs_file",
    "apply_graph_prefs", "load_into_state", "merge_prefs", "prefs_js_literal",
    "api_prefs_get", "api_prefs_post",
]

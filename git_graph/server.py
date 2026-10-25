import argparse
import html
import json
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse
from . import app_state as state
from .gitrun import git, is_loopback, resolve_commit
# 次の import のうち、api_* などは名前を直接使わない。各モジュールの @route 登録を実行させるための import なので、
# 未使用の警告 (F401) を抑止している。
from .graph import CLUSTER_MIN_NODES, DEFAULT_MERGE_MAX_NODES, build_graph, graph_payload  # noqa: F401
from .i18n import (
    APP_CSS, check_i18n, detect_language, FILES_PAGE, fill_template, HTML_TEMPLATE, render_app_js,
    render_main_page, tr, TRANSLATIONS, UserError,
)
from .rebase import (  # noqa: F401
    api_abort, api_cherry_pick, api_find_equivalent_branch, api_find_equivalent_parents, api_head,
    api_merge, api_rebase, api_rebase_descendants, api_rebase_equivalent, api_rebase_i,
    api_rebase_range, api_rebase_todo, api_resolve, api_revert,
)
from .refops import (  # noqa: F401
    api_branch, api_branch_rename, api_checkout, api_delete_ref, api_discard, api_reset, api_stash,
    api_stash_push, api_tag,
)
from .remote import (  # noqa: F401
    api_fetch, api_pull, api_push, api_push_tag, api_remote_add, api_remote_remove, api_remotes,
)
from .routing import route, ROUTES
from .files import (  # noqa: F401
    api_blame, api_file, api_file_copy, api_file_save, api_patch_apply, api_tree, file_download, patch_download,
    PATCH_BODY_LIMIT, SAVE_BODY_LIMIT, WIP_HASH,
)
from .history import (  # noqa: F401
    api_branches, api_branches_containing, api_commit, api_compare, api_log, api_merge_base,
    api_reflog, api_stash_list, api_tags,
)
from .external import DEFAULT_CONFIG_PATH, load_config
from .pages import render_diff_page, render_files_page
from .wip import api_status, api_wip_commit  # noqa: F401


class Forbidden(Exception):
    pass

_write_lock = threading.Lock()

DEFAULT_PORT = 8000

class QuietServer(ThreadingHTTPServer):
    # Windows では SO_REUSEADDR が「使用中のポートへの二重 bind」を許してしまうため、無効にする
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

@route("/api/shutdown", method="POST")
def api_shutdown(body):
    """終了要求を返す。サーバーの停止は HTTP 応答の送信後に行う。"""
    return {"ok": True}

@route("/api/restart", method="POST")
def api_restart(body):
    """再起動要求を返す。サーバーの停止と os.execv による再起動は HTTP 応答の送信後に行う。"""
    return {"ok": True}

POST_ROUTES = {path: handler for (method, path), handler in ROUTES.items() if method == "POST"}

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_download(self, name, data):
        """ファイルをダウンロードとして返す。日本語などのファイル名は RFC 5987 (filename*) で渡す。"""
        fallback = "".join(c if c.isascii() and c.isprintable() and c not in '"\\/;' else "_" for c in name) or "download"
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/":
                self._send(200, "text/html", render_main_page())
            elif u.path == "/static/app.js":
                self._send(200, "application/javascript", render_app_js())
            elif u.path == "/static/app.css":
                self._send(200, "text/css", APP_CSS)
            elif (handler := ROUTES.get(("GET", u.path))) is not None:
                result = handler(q)
                self._send(200, "application/json", json.dumps(result, ensure_ascii=False))
            elif u.path == "/files":
                h = q.get("hash", [""])[0]
                if h != WIP_HASH:  # WIP は作業ツリーのファイル表示 (作業ツリーの有無は API 側で確認する)
                    h = resolve_commit(h)
                self._send(200, "text/html", render_files_page().replace("__HASH__", html.escape(h)))
            elif u.path == "/download":
                self._send_download(*file_download(q))
            elif u.path == "/patch":
                self._send_download(*patch_download(q))
            elif u.path == "/diff":
                self._send(200, "text/html", render_diff_page(q))
            else:
                self._send(404, "text/plain", "not found")
        except ValueError as e:
            self._send(400, "text/plain", str(e))
        except FileNotFoundError:
            self._send(500, "text/plain", tr("dot_missing"))
        except subprocess.CalledProcessError as e:
            self._send(500, "text/plain", f"command failed: {e.cmd}\n{e.stderr or ''}")

    def _check_post(self, shutdown=False):
        """他サイトのページからの書き込みを防ぐ (CSRF / DNS rebinding 対策)。"""
        if shutdown:  # 終了は --read-only でもできる。ただし他の端末から止められないよう、ローカルで待ち受けているときだけ
            if not (state.WRITABLE or is_loopback(state.OPTS.host)):
                raise Forbidden(tr("shutdown_forbidden"))
        elif not state.WRITABLE:
            raise Forbidden(tr("write_disabled"))
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":  # 他サイトからは preflight が必要になるため送れない
            raise UserError("content_type_json")
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            raise Forbidden(tr("origin_mismatch"))
        if is_loopback(state.OPTS.host):
            name = host if host.endswith("]") or ":" not in host else host.rsplit(":", 1)[0]
            if name not in ("127.0.0.1", "localhost", "[::1]"):
                raise Forbidden(tr("host_invalid"))

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            handler = ROUTES.get(("POST", path))
            if handler is None:
                self._send(404, "text/plain", "not found")
                return
            self._check_post(shutdown=path in ("/api/shutdown", "/api/restart"))
            n = int(self.headers.get("Content-Length") or 0)
            if n > {"/api/file-save": SAVE_BODY_LIMIT, "/api/patch-apply": PATCH_BODY_LIMIT}.get(path, 100000):
                raise ValueError("body too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("bad body")
            with _write_lock:
                res = handler(body)
            self._send(200, "application/json", json.dumps(res, ensure_ascii=False))
            if path in ("/api/shutdown", "/api/restart") and state.SERVER is not None:
                if path == "/api/restart":
                    # os.execv はプロセスを丸ごと置き換えるので shutdown() 不要。
                    # daemon スレッドだとメインスレッド終了後に殺されるため、daemon=False にする。
                    def _do_restart():
                        # 実際に使用中のポートを引き継ぐ。--open は再起動のたびにブラウザが開くので除外する。
                        args = [a for a in sys.argv[1:] if a != "--open"]
                        if "--port" not in args and "-port" not in args:
                            args = ["--port", str(state.OPTS.port)] + args
                        os.execv(sys.executable, [sys.executable] + sys.argv[:1] + args)
                    threading.Thread(target=_do_restart, daemon=False).start()
                else:
                    threading.Thread(target=state.SERVER.shutdown, daemon=True).start()
        except Forbidden as e:
            self._send(403, "text/plain", str(e))
        except ValueError as e:  # JSON の不正も含む
            self._send(400, "text/plain", str(e))

    def log_message(self, fmt, *args):  # アクセスログは出さない
        pass

def main():
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)  # --lan を --lang の省略形と誤認しない  # --help の文言も言語に合わせるため、先に --lang だけ読む
    pre.add_argument("--lang", choices=sorted(TRANSLATIONS))
    state.UI_LANG = detect_language(pre.parse_known_args()[0].lang)
    ap = argparse.ArgumentParser(description="git commit graph server")
    ap.add_argument("revs", nargs="*", help=tr("cli_help_revs"))
    ap.add_argument("-C", dest="repo", default=".", help=tr("cli_help_repo"))
    ap.add_argument("--host", default="127.0.0.1", help=tr("cli_help_host"))
    ap.add_argument("--port", type=int, default=None, help=tr("cli_help_port"))
    ap.add_argument("--open", action="store_true", help=tr("cli_help_open"))
    ap.add_argument("--no-remotes", action="store_true", help=tr("cli_help_no_remotes"))
    ap.add_argument("--no-tags", action="store_true", help=tr("cli_help_no_tags"))
    ap.add_argument("--include-tips", action="store_true", help=tr("cli_help_include_tips"))
    ap.add_argument("--full-layout", action="store_true", help=tr("cli_help_full_layout"))
    ap.add_argument("--no-count", action="store_true", help=tr("cli_help_no_count"))
    ap.add_argument("--merge-max-nodes", type=int, default=DEFAULT_MERGE_MAX_NODES, metavar="N",
                    help=tr("cli_help_merge_max_nodes"))
    ap.add_argument("--cluster-min-nodes", type=int, default=CLUSTER_MIN_NODES, metavar="N",
                    help=tr("cli_help_cluster_min_nodes"))
    ap.add_argument("--rankdir", default="BT", help=tr("cli_help_rankdir"))
    ap.add_argument("--font", default="Helvetica", help=tr("cli_help_font"))
    ap.add_argument("--dot", action="store_true", help=tr("cli_help_dot"))
    ap.add_argument("--no-wip", action="store_true", help=tr("cli_help_no_wip"))
    ap.add_argument("--read-only", action="store_true", help=tr("cli_help_read_only"))
    ap.add_argument("--allow-write", action="store_true", help=tr("cli_help_allow_write"))
    ap.add_argument("--lan", action="store_true", help=tr("cli_help_lan"))
    ap.add_argument("--lang", choices=sorted(TRANSLATIONS), help=tr("cli_help_lang"))
    ap.add_argument("--desktop", action="store_true", help=tr("cli_help_desktop"))
    ap.add_argument("--config", metavar="FILE", help=tr("cli_help_config"))
    ap.add_argument("--check-i18n", action="store_true", help=tr("cli_help_check_i18n"))
    state.OPTS = ap.parse_args()
    if state.OPTS.check_i18n:
        sys.exit(check_i18n())
    if state.OPTS.lan:
        if state.OPTS.host == "127.0.0.1":  # --host を明示しているときはそちらを優先する
            state.OPTS.host = "0.0.0.0"
        state.OPTS.allow_write = True
    state.REPO = state.OPTS.repo
    state.DESKTOP = state.OPTS.desktop and is_loopback(state.OPTS.host)  # 外部アプリの起動は、同じ PC の画面からだけ
    state.CONFIG_PATH = state.OPTS.config or str(DEFAULT_CONFIG_PATH)
    try:
        state.CONFIG = load_config(state.CONFIG_PATH, required=bool(state.OPTS.config))
    except UserError as e:
        sys.exit(str(e))
    state.WRITABLE = (not state.OPTS.read_only) and (is_loopback(state.OPTS.host) or state.OPTS.allow_write)

    try:
        git("rev-parse", "--git-dir")
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit(tr("cli_not_a_repo", repo=repr(state.REPO)))
    # サブディレクトリが指定されても、パスの扱い (status / diff はルート基準) がずれないようルートに揃える
    r = subprocess.run(["git", "-C", state.REPO, "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    state.HAS_WORKTREE = r.returncode == 0
    if state.HAS_WORKTREE:
        state.REPO = r.stdout.strip()
    state.WIP_ENABLED = state.HAS_WORKTREE and not state.OPTS.no_wip
    state.PROJECT_NAME = os.path.basename(os.path.abspath(state.REPO)) or state.REPO

    if state.OPTS.dot:
        print(build_graph(state.OPTS)[0])
        return

    fill_template(HTML_TEMPLATE), fill_template(FILES_PAGE)  # 未定義の翻訳キーがあれば、起動時に KeyError で気づけるようにする
    wanted = state.OPTS.port if state.OPTS.port is not None else DEFAULT_PORT
    try:
        state.SERVER = QuietServer((state.OPTS.host, wanted), Handler)
    except OSError:
        if state.OPTS.port is not None:  # --port を指定したときは、自動で変えずにエラーで終わる
            sys.exit(tr("cli_port_in_use", port=state.OPTS.port))
        state.SERVER = QuietServer((state.OPTS.host, 0), Handler)  # OS に空いているポートを選ばせる
        print(tr("cli_port_auto", port=wanted, actual=state.SERVER.server_address[1]))
    server = state.SERVER
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    state.OPTS.port = server.server_address[1]
    url = f"http://{'localhost' if state.OPTS.host == '0.0.0.0' else state.OPTS.host}:{state.OPTS.port}/"  # 0.0.0.0 はブラウザで開く宛先にならない
    print(tr("cli_serving", url=url, state=tr("cli_state_on" if state.WRITABLE else "cli_state_off")))
    if state.OPTS.host == "0.0.0.0":
        print(tr("cli_listening", port=state.OPTS.port))
        if state.WRITABLE:
            print(tr("cli_lan_warning"))
    if state.OPTS.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = ["DEFAULT_PORT", "Forbidden", "Handler", "POST_ROUTES", "QuietServer", "ROUTES",
           "api_restart", "api_shutdown", "main", "route"]

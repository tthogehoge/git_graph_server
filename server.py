import argparse
import html
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import app_state as state
from gitrun import is_loopback, resolve_commit
from graph import build_graph, graph_payload
from i18n import (
    APP_CSS, HTML_TEMPLATE, TRANSLATIONS, UserError, detect_language, fill_template,
    render_app_js, render_main_page, tr,
)
from rebase import (
    api_abort, api_cherry_pick, api_find_equivalent_branch, api_find_equivalent_parents,
    api_head, api_merge, api_rebase, api_rebase_descendants, api_rebase_equivalent,
    api_rebase_i, api_rebase_range, api_rebase_todo, api_resolve, api_revert,
)
from refops import (
    api_branch, api_branch_rename, api_checkout, api_delete_ref, api_discard, api_reset,
    api_stash, api_tag,
)
from remote import (
    api_fetch, api_pull, api_push, api_push_tag, api_remote_add, api_remote_remove,
    api_remotes,
)
from viewer import (
    api_blame, api_branches, api_branches_containing, api_commit, api_compare, api_file,
    api_file_copy, api_log, api_merge_base, api_reflog, api_stash_list, api_tags,
    api_tree, render_diff_page, render_files_page,
)
from wip import api_status, api_wip_commit
from routing import ROUTES, route


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
                h = resolve_commit(q.get("hash", [""])[0])
                self._send(200, "text/html", render_files_page().replace("__HASH__", html.escape(h)))
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
            self._check_post(shutdown=path == "/api/shutdown")
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100000:
                raise ValueError("body too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("bad body")
            with _write_lock:
                res = handler(body)
            self._send(200, "application/json", json.dumps(res, ensure_ascii=False))
            if path == "/api/shutdown" and state.SERVER is not None:
                threading.Thread(target=state.SERVER.shutdown, daemon=True).start()
        except Forbidden as e:
            self._send(403, "text/plain", str(e))
        except ValueError as e:  # JSON の不正も含む
            self._send(400, "text/plain", str(e))

    def log_message(self, fmt, *args):  # アクセスログは出さない
        pass

def main():
    from gitrun import git
    from graph import build_graph
    from i18n import check_i18n, detect_language, fill_template, tr
    from viewer import FILES_PAGE
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
    ap.add_argument("--rankdir", default="BT", help=tr("cli_help_rankdir"))
    ap.add_argument("--font", default="Helvetica", help=tr("cli_help_font"))
    ap.add_argument("--dot", action="store_true", help=tr("cli_help_dot"))
    ap.add_argument("--no-wip", action="store_true", help=tr("cli_help_no_wip"))
    ap.add_argument("--read-only", action="store_true", help=tr("cli_help_read_only"))
    ap.add_argument("--allow-write", action="store_true", help=tr("cli_help_allow_write"))
    ap.add_argument("--lan", action="store_true", help=tr("cli_help_lan"))
    ap.add_argument("--lang", choices=sorted(TRANSLATIONS), help=tr("cli_help_lang"))
    ap.add_argument("--check-i18n", action="store_true", help=tr("cli_help_check_i18n"))
    state.OPTS = ap.parse_args()
    if state.OPTS.check_i18n:
        sys.exit(check_i18n())
    if state.OPTS.lan:
        if state.OPTS.host == "127.0.0.1":  # --host を明示しているときはそちらを優先する
            state.OPTS.host = "0.0.0.0"
        state.OPTS.allow_write = True
    state.REPO = state.OPTS.repo
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


__all__ = ["DEFAULT_PORT", "Forbidden", "Handler", "POST_ROUTES", "QuietServer", "ROUTES",
           "api_shutdown", "main", "route"]

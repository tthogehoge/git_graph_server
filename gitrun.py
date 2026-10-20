import os
import re
import shutil
import subprocess
import tempfile
import app_state as state


def git(*args, literal=False):
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1") if literal else None  # パスをglobとして解釈しない
    return subprocess.check_output(["git", "-C", state.REPO, *args], text=True,
                                   encoding="utf-8", errors="replace", env=env)

def git_bytes(*args, literal=False):
    """git の出力を変換せずバイト列で取得する (ファイル内容を含む出力用)。"""
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1") if literal else None
    return subprocess.check_output(["git", "-C", state.REPO, *args], env=env)

HASH_RE = re.compile(r"[0-9a-f]{7,40}")

def resolve_commit(h):
    from i18n import UserError
    if not HASH_RE.fullmatch(h or ""):
        raise ValueError("bad hash")
    try:
        return git("rev-parse", "--verify", "--quiet", h + "^{commit}").strip()
    except subprocess.CalledProcessError:
        raise UserError("commit_not_found")

def check_ref_name(kind, name):
    from i18n import UserError
    prefix = {"branch": "refs/heads/", "tag": "refs/tags/"}[kind]
    label = {"branch": ("branch_name_invalid", "branch_name_unusable"),
             "tag": ("tag_name_invalid", "tag_name_unusable")}[kind]
    if not name or name.startswith("-") or "\0" in name:
        raise UserError(label[0])
    r = subprocess.run(["git", "-C", state.REPO, "check-ref-format", prefix + name], capture_output=True)
    if r.returncode != 0:
        raise UserError(label[1], name=repr(name))

def is_loopback(host):
    return host in ("localhost", "::1") or host.startswith("127.")

def run_git_write(*args, literal=False, cwd=None):
    """git を実行し、失敗したら git のエラーメッセージを ValueError にして返す。"""
    from i18n import tr
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1") if literal else None  # パスをglobとして解釈しない
    r = subprocess.run(["git", "-C", cwd or state.REPO, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if r.returncode != 0:
        raise ValueError((r.stderr or r.stdout).strip() or tr("git_command_failed"))
    return (r.stdout + r.stderr).strip()

def git_c_locale(*args, cwd=None):
    """メッセージを解析するため、ロケールを C にして git を実行する (失敗しても例外にしない)。"""
    env = dict(os.environ, LC_ALL="C", LANGUAGE="C")
    return subprocess.run(["git", "-C", cwd or state.REPO, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)

NET_TIMEOUT = 300

HTTP_AUTH_CACHE = {}

def run_git_net(*args, timeout=None, auth=None):
    """リモートと通信する git を実行する。
    SSH の未知ホストは初回だけ known_hosts に登録し、登録済みホストの
    鍵が変わった場合は SSH に拒否させる。必要なら一時的な HTTP 認証用
    askpass を使う。
    """
    from i18n import UserError
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    # stdin は閉じて実行するため、SSH の yes/no 確認プロンプトは表示できない。
    # accept-new なら未知ホストだけを自動登録し、鍵が変わったホストは拒否する。
    # ユーザーが独自の SSH 設定を指定している場合はそれを優先する。
    env.setdefault("GIT_SSH_COMMAND", "ssh -o StrictHostKeyChecking=accept-new")
    timeout = timeout or NET_TIMEOUT
    askpass_dir = None
    if auth:
        if not isinstance(auth, dict) or not isinstance(auth.get("username", ""), str) or not isinstance(auth.get("password", ""), str):
            raise UserError("auth_invalid")
        askpass_dir = tempfile.mkdtemp(prefix="git_graph_askpass_")
        askpass = os.path.join(askpass_dir, "askpass.py")
        with open(askpass, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env python3\nimport os, sys\np = (sys.argv[1] if len(sys.argv) > 1 else '').lower()\nprint(os.environ.get('GIT_GRAPH_HTTP_USERNAME' if 'username' in p or 'user' in p else 'GIT_GRAPH_HTTP_PASSWORD', ''))\n")
        os.chmod(askpass, 0o700)
        env.update(GIT_ASKPASS=askpass,
                   GIT_GRAPH_HTTP_USERNAME=auth.get("username", ""),
                   GIT_GRAPH_HTTP_PASSWORD=auth.get("password", ""))
    try:
        return subprocess.run(["git", "-C", state.REPO, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=timeout,
                              stdin=subprocess.DEVNULL, start_new_session=True)  # 制御端末を切り離し ssh が /dev/tty に尋ねないように
    except subprocess.TimeoutExpired:
        raise UserError("net_timeout", cmd=args[0], sec=timeout)
    finally:
        if askpass_dir:
            shutil.rmtree(askpass_dir, ignore_errors=True)

def net_error(r, fallback):
    text = (r.stderr or r.stdout).strip()
    text = "\n".join(l for l in text.splitlines() if not l.startswith("hint:")).strip()  # 長い hint は省く
    return text or fallback

def is_http_auth_error(r):
    text = ((r.stderr or "") + "\n" + (r.stdout or "")).lower()
    return any(s in text for s in (
        "authentication failed", "invalid username or token", "could not read username",
        "terminal prompts disabled", "http 401", "http 403",
    ))

def auth_for_remote(remote, body):
    """明示された認証情報を優先し、なければアプリ実行中のキャッシュを使う。"""
    if body.get("auth") and (body.get("auth_remote") is None or body.get("auth_remote") == remote):
        return body["auth"]
    return HTTP_AUTH_CACHE.get(remote)

def remember_auth(remote, auth):
    if auth:
        HTTP_AUTH_CACHE[remote] = {"username": auth.get("username", ""), "password": auth.get("password", "")}


__all__ = [
    "HASH_RE", "HTTP_AUTH_CACHE", "NET_TIMEOUT", "auth_for_remote",
    "check_ref_name", "git", "git_bytes", "git_c_locale", "is_http_auth_error",
    "is_loopback", "net_error", "remember_auth", "resolve_commit", "run_git_net",
    "run_git_write",
]

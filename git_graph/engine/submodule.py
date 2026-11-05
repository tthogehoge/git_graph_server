# submodule の追加 (git submodule add)。インデックスと .gitmodules を変えるので、engine の操作として履歴に積む。
# 元に戻すと、インデックスと .gitmodules は戻る。取得したディレクトリは (中身を勝手に消さないため) 残り、同じ場所へ追加し直せる。
# 引数を受け取って git を実行し、結果を dict で返す。HTTP の body も、画面に出す文言の組み立ても route 側の仕事。
import re
from ..gitrun import check_ref_name, net_error, run_git_net
from ..i18n import tr, UserError
from ..query.files import validate_file_path
from . import operation

MAX_URL = 2000


def check_submodule_url(url):
    """追加する submodule の URL として使える形にして返す。"""
    if not isinstance(url, str):
        raise ValueError("bad url")
    url = url.strip()
    if not url or len(url) > MAX_URL or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise UserError("submodule_url_invalid")
    if url.startswith("-") or re.match(r"(ext|fd)::", url):  # オプション注入と、任意コマンドを実行できる転送方式を避ける
        raise UserError("submodule_url_invalid")
    return url


@operation("submodule_add", touches_worktree="names", detail=lambda url, path, *_, **__: path if isinstance(path, str) else "")
def add_submodule(url, path, *, branch="", recursive=False):
    """submodule を追加する (git submodule add [-b branch] -- url path)。recursive なら、続けて入れ子の submodule も取得する
    (取得に失敗しても、追加は済んでいる)。戻り値: {"path", "nested_error": 入れ子の取得の失敗の内容 (なければ空)}。"""
    url = check_submodule_url(url)
    if not isinstance(path, str) or not isinstance(branch, str):
        raise ValueError("bad request")
    path = validate_file_path(path.strip().rstrip("/"), allow_empty=False)
    branch = branch.strip()
    if branch:
        check_ref_name("branch", branch)
    args = ["-c", "protocol.ext.allow=never", "submodule", "add"] + (["-b", branch] if branch else []) + ["--", url, path]
    r = run_git_net(*args)
    if r.returncode != 0:
        raise UserError("submodule_add_failed", error=net_error(r, tr("git_command_failed")))
    nested_error = ""
    if recursive:
        r = run_git_net("submodule", "update", "--init", "--recursive", "--", ":(literal)" + path)
        if r.returncode != 0:
            nested_error = net_error(r, tr("git_command_failed"))
    return {"path": path, "nested_error": nested_error}

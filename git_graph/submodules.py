# submodule の更新・fetch・追加。
# 更新と fetch は、submodule の中身 (作業ツリーとその HEAD) が別のリポジトリなので、元に戻す履歴 (engine) の対象外 (engine.IGNORED)。
# 追加はこのリポジトリのインデックスと .gitmodules を変えるので、engine の操作 (engine/submodule.py) として履歴に積む。
# いずれも --force は使わず、submodule の中の編集中のファイルは上書きしない。
from .engine.submodule import add_submodule
from .gitrun import net_error, run_git_net
from .i18n import tr, UserError
from .launcher import check_submodule, submodule_dir
from .routing import route

MAX_PATHS = 200


def _paths(body):
    """body["paths"] を、重複を除いた登録済みの submodule のパスにする。実行の前に、すべて登録済みか確認する。"""
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or len(paths) > MAX_PATHS or not all(isinstance(p, str) for p in paths):
        raise ValueError("bad paths")
    return [check_submodule(p) for p in dict.fromkeys(paths)]


def _summary(ok_key, results):
    done = sum(1 for x in results if x["ok"])
    return {"ok": done == len(results), "results": results, "message": tr(ok_key, done=done, total=len(results))}


def _result(path, r):
    ok = r.returncode == 0
    return {"path": path, "ok": ok, "error": "" if ok else net_error(r, tr("git_command_failed"))}


@route("/api/submodule-update", method="POST")
def api_submodule_update(body):
    """paths の submodule を更新する。remote が真なら、記録されたコミットではなくリモートの最新へ (--remote)。
    recursive が真なら、入れ子の submodule も (--recursive)。未初期化のものは取得する (--init)。
    1 つずつ実行し、失敗したものがあっても残りは続ける。戻り値: {ok, results: [{path, ok, error}], message}。"""
    paths = _paths(body)
    extra = (["--remote"] if body.get("remote") else []) + (["--recursive"] if body.get("recursive") else [])
    results = [_result(path, run_git_net("submodule", "update", "--init", *extra, "--", ":(literal)" + path))
               for path in paths]
    return _summary("submodule_update_done", results)


@route("/api/submodule-fetch", method="POST")
def api_submodule_fetch(body):
    """paths の submodule で fetch する (作業ツリーは変えない)。recursive が真なら入れ子の submodule も。
    未初期化のものは失敗として返す (取得は更新で行う)。"""
    paths = _paths(body)
    results = []
    for path in paths:
        try:
            submodule_dir(path)
        except UserError as e:
            results.append({"path": path, "ok": False, "error": str(e)})
            continue
        extra = ["--recurse-submodules"] if body.get("recursive") else []
        results.append(_result(path, run_git_net("-C", path, "fetch", *extra)))
    return _summary("submodule_fetch_done", results)


@route("/api/submodule-add", method="POST", engine=True)
def api_submodule_add(body):
    """submodule を追加する (engine の操作 submodule_add。履歴に積まれる)。recursive が真なら、入れ子の submodule も取得する。"""
    res = add_submodule(body.get("url"), body.get("path"), branch=body.get("branch") or "", recursive=bool(body.get("recursive")))
    message = tr("submodule_added", path=res["path"])
    if res["nested_error"]:
        message += "\n" + tr("submodule_add_recursive_failed", error=res["nested_error"])
    return {"ok": True, "path": res["path"], "message": message}


__all__ = ["MAX_PATHS", "api_submodule_add", "api_submodule_fetch", "api_submodule_update"]

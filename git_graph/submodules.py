# submodule の更新・fetch・追加。
# 更新と fetch は、submodule の中身 (作業ツリーとその HEAD) が別のリポジトリなので、元に戻す履歴 (engine) の対象外 (engine.IGNORED)。
# 追加はこのリポジトリのインデックスと .gitmodules を変えるので、engine の操作 (engine/submodule.py) として履歴に積む。
# いずれも --force は使わず、submodule の中の編集中のファイルは上書きしない。
# HTTP 認証は origin (scheme://host[:port]) 単位で扱い、入力された認証情報は、その origin の質問にだけ答える (gitrun.run_git_net_origin)。
from .engine.submodule import add_submodule
from .gitrun import net_error, run_git_net_origin
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


def _auth_args(body):
    """body の HTTP 認証の指定を、(認証情報, その origin) にする。origin は、その認証情報を使ってよい接続先 (scheme://host[:port])。"""
    auth, origin = body.get("auth"), body.get("auth_origin")
    if auth is None:
        return None, None
    if (not isinstance(auth, dict) or not isinstance(origin, str) or not origin
            or not all(isinstance(auth.get(k, ""), str) for k in ("username", "password"))):
        raise UserError("auth_invalid")
    return auth, origin


def _run(path, args, auth, origin):
    """1 つの submodule について git を実行して、結果の dict にする。HTTP 認証が必要なときは auth_origin を付ける。"""
    r, need = run_git_net_origin(*args, auth=auth, origin=origin)
    if need:
        return {"path": path, "ok": False, "error": tr("submodule_auth_needed", origin=need), "auth_origin": need}
    ok = r.returncode == 0
    return {"path": path, "ok": ok, "error": "" if ok else net_error(r, tr("git_command_failed"))}


def _summary(ok_key, results):
    """結果をまとめる。認証が必要だったものは、origin ごとにまとめて auth_requests に入れる (画面が尋ねて、そのパスだけをやり直す)。"""
    done = sum(1 for x in results if x["ok"])
    out = {"ok": done == len(results), "results": results, "message": tr(ok_key, done=done, total=len(results))}
    groups = {}
    for x in results:
        if x.get("auth_origin"):
            groups.setdefault(x["auth_origin"], []).append(x["path"])
    if groups:
        out["auth_required"] = True
        out["auth_requests"] = [{"origin": o, "paths": p} for o, p in groups.items()]
    return out


@route("/api/submodule-update", method="POST")
def api_submodule_update(body):
    """paths の submodule を更新する。remote が真なら、記録されたコミットではなくリモートの最新へ (--remote)。
    recursive が真なら、入れ子の submodule も (--recursive)。未初期化のものは取得する (--init)。
    1 つずつ実行し、失敗したものがあっても残りは続ける。戻り値: {ok, results: [{path, ok, error}], message}。
    HTTP 認証が必要なものは、結果に auth_origin を付け、{auth_required, auth_requests: [{origin, paths}]} も返す。
    画面は、origin ごとに認証情報を尋ね、そのパスだけを auth / auth_origin 付きでやり直す。"""
    paths = _paths(body)
    auth, origin = _auth_args(body)
    extra = (["--remote"] if body.get("remote") else []) + (["--recursive"] if body.get("recursive") else [])
    results = [_run(path, ["submodule", "update", "--init", *extra, "--", ":(literal)" + path], auth, origin)
               for path in paths]
    return _summary("submodule_update_done", results)


@route("/api/submodule-fetch", method="POST")
def api_submodule_fetch(body):
    """paths の submodule で fetch する (作業ツリーは変えない)。recursive が真なら入れ子の submodule も。
    未初期化のものは失敗として返す (取得は更新で行う)。HTTP 認証が必要なときの返し方は更新と同じ。"""
    paths = _paths(body)
    auth, origin = _auth_args(body)
    results = []
    for path in paths:
        try:
            submodule_dir(path)
        except UserError as e:
            results.append({"path": path, "ok": False, "error": str(e)})
            continue
        extra = ["--recurse-submodules"] if body.get("recursive") else []
        results.append(_run(path, ["-C", path, "fetch", *extra], auth, origin))
    return _summary("submodule_fetch_done", results)


@route("/api/submodule-add", method="POST", engine=True)
def api_submodule_add(body):
    """submodule を追加する (engine の操作 submodule_add。履歴に積まれる)。recursive が真なら、入れ子の submodule も取得する。"""
    auth, origin = _auth_args(body)
    res = add_submodule(body.get("url"), body.get("path"), branch=body.get("branch") or "", recursive=bool(body.get("recursive")),
                        auth=auth, auth_origin=origin)
    if res.get("auth_required"):
        return {"auth_required": True, "auth_requests": [{"origin": res["auth_origin"]}]}
    message = tr("submodule_added", path=res["path"])
    if res["nested_error"]:
        message += "\n" + tr("submodule_add_recursive_failed", error=res["nested_error"])
    return {"ok": True, "path": res["path"], "message": message}


__all__ = ["MAX_PATHS", "api_submodule_add", "api_submodule_fetch", "api_submodule_update"]

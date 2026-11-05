# 選んだ submodule の更新 (git submodule update)。
# submodule の中身 (作業ツリーとその HEAD) は別のリポジトリなので、元に戻す履歴 (engine) の対象外 (engine.IGNORED)。
# 未初期化のものは --init で取得する。ローカルの変更は上書きしない (--force は使わない)。
from .gitrun import net_error, run_git_net
from .i18n import tr
from .launcher import check_submodule
from .routing import route

MAX_PATHS = 200


@route("/api/submodule-update", method="POST")
def api_submodule_update(body):
    """paths の submodule を更新する。remote が真なら、記録されたコミットではなくリモートの最新へ (--remote)。
    1 つずつ実行し、失敗したものがあっても残りは続ける。戻り値: {ok, results: [{path, ok, error}], message}。"""
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or len(paths) > MAX_PATHS or not all(isinstance(p, str) for p in paths):
        raise ValueError("bad paths")
    remote = bool(body.get("remote"))
    paths = [check_submodule(p) for p in dict.fromkeys(paths)]  # 重複は除く。実行の前に、すべて登録済みか確認する
    results = []
    for path in paths:
        args = ["submodule", "update", "--init"] + (["--remote"] if remote else []) + ["--", ":(literal)" + path]
        r = run_git_net(*args)
        ok = r.returncode == 0
        results.append({"path": path, "ok": ok, "error": "" if ok else net_error(r, tr("git_command_failed"))})
    done = sum(1 for x in results if x["ok"])
    return {"ok": done == len(results), "results": results, "message": tr("submodule_update_done", done=done, total=len(results))}


__all__ = ["MAX_PATHS", "api_submodule_update"]

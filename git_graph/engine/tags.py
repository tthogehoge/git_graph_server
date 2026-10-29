# リベースで作り直されたコミットに付いていたタグを、新しいコミットへ付け替える。
import subprocess
from .. import app_state as state
from ..gitrun import git
from ..i18n import tr

def tag_targets():
    """[{ref, sha, annotated, commit}]。注釈付きタグは、直接コミットを指しているものだけ (タグのタグは対象外)。"""
    fmt = "%(refname)%09%(objecttype)%09%(objectname)%09%(*objectname)%09%(*objecttype)"
    tags = []
    for line in git("for-each-ref", "--format=" + fmt, "refs/tags").splitlines():
        ref, kind, sha, peeled, peeled_kind = (line.split("\t") + [""] * 5)[:5]
        if kind == "commit":
            tags.append({"ref": ref, "sha": sha, "annotated": False, "commit": sha})
        elif kind == "tag" and peeled_kind == "commit":
            tags.append({"ref": ref, "sha": sha, "annotated": True, "commit": peeled})
    return tags


def commit_patch_ids(rev_range):
    """rev_range のマージ以外のコミットを古い順に [(コミット, patch-id)] で返す (空のコミットは含まれない)。"""
    log = subprocess.run(["git", "-C", state.REPO, "log", "--reverse", "--no-merges", "-p", "--format=commit %H", rev_range],
                         capture_output=True)
    if log.returncode != 0:
        return []
    pid = subprocess.run(["git", "-C", state.REPO, "patch-id", "--stable"], input=log.stdout, capture_output=True)
    result = []
    for line in pid.stdout.decode("utf-8", "replace").splitlines():
        patch_id, _, commit = line.partition(" ")
        if commit.strip():
            result.append((commit.strip(), patch_id))
    return result


def snapshot_for_tags(rev_range):
    """リベース前に呼ぶ。rev_range の中のコミットを指すタグがあれば、そのコミットの patch-id を返す (無ければ None)。"""
    tags = tag_targets()
    if not tags:
        return None
    commits = set(git("rev-list", rev_range).split())
    if not any(t["commit"] in commits for t in tags):
        return None
    return commit_patch_ids(rev_range)


def tag_mapping(old_ids, new_range):
    """リベース前後で同じ変更 (patch-id) を持つコミットを、古い順に突き合わせて {旧コミット: 新コミット} にする。"""
    new_ids = commit_patch_ids(new_range)
    used, mapping = set(), {}
    for old_commit, old_pid in old_ids or []:
        for new_commit, new_pid in new_ids:
            if new_pid == old_pid and new_commit not in used:
                mapping[old_commit] = new_commit
                used.add(new_commit)
                break
    return mapping


def move_tags(mapping):
    """mapping ({旧コミット: 新コミット}) の旧コミットに付いていたタグを、新コミットへ付け替えて、付け替えた数を返す。
    注釈付きタグは、メッセージ・タグ付けした人と日時を保ったまま、指す先だけ変えたタグを作り直す (署名は無効になる)。"""
    mapping = {old: new for old, new in mapping.items() if old != new}
    if not mapping:
        return 0
    lines = []
    for tag in tag_targets():
        new = mapping.get(tag["commit"])
        if not new:
            continue
        new_obj = new
        if tag["annotated"]:
            raw = subprocess.run(["git", "-C", state.REPO, "cat-file", "tag", tag["sha"]], capture_output=True)
            first, newline, rest = raw.stdout.partition(b"\n")
            if raw.returncode != 0 or first != b"object " + tag["commit"].encode():
                continue
            made = subprocess.run(["git", "-C", state.REPO, "mktag"], input=b"object " + new.encode() + newline + rest,
                                  capture_output=True)
            if made.returncode != 0:
                continue
            new_obj = made.stdout.decode().strip()
        lines.append(f"update {tag['ref']} {new_obj} {tag['sha']}")
    if lines:
        r = subprocess.run(["git", "-C", state.REPO, "update-ref", "--stdin"], input="\n".join(lines) + "\n",
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise ValueError((r.stderr or r.stdout).strip() or tr("rebase_failed"))
    return len(lines)

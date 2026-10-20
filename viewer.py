import codecs
import html
import json
import os
import re
import subprocess
from collections import Counter
from urllib.parse import urlencode
import app_state as state
from i18n import FAVICON_LINK


COPY_JS = """
function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) {}
  document.body.removeChild(ta);
  return ok;
}
function updatePushActions() {
  document.getElementById("pushBtn").disabled = !currentBranchLoaded || !DATA.writable;
}
function updateCurrentBranch() {
  return fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(head => {
      currentBranch = head.branch; currentBranchLoaded = true;
      Object.keys(DATA.nodes).forEach(h => {
        document.querySelectorAll('tr[data-h="' + h + '"] .rc').forEach(el => { el.innerHTML = chips(h); });
      });
      const c = detailView.querySelector("#dChips");
      if (c && curDetailHash) c.innerHTML = detailChips(curDetailHash);
      updatePushActions();
    });
}
function copyText(text) {  // localhost / https なら Clipboard API、それ以外 (LAN の http など) は execCommand で代替
  if (navigator.clipboard && window.isSecureContext)
    return navigator.clipboard.writeText(text).then(() => true, () => fallbackCopy(text));
  return Promise.resolve(fallbackCopy(text));
}
// .copy リンクは data-copy の値をコピーする。行やリンク本来のクリック処理には渡さない
document.addEventListener("click", ev => {
  const a = ev.target.closest(".copy");
  if (!a) return;
  ev.preventDefault(); ev.stopPropagation();
  const label = a.dataset.label || (a.dataset.label = a.textContent);
  copyText(a.dataset.copy).then(ok => {
    const cls = ok ? "ok" : "ng";
    a.textContent = ok ? "✅" : "⚠️"; a.classList.add(cls);
    setTimeout(() => { a.textContent = label; a.classList.remove(cls); }, 1500);
  });
}, true);
"""

REV_RE = re.compile(r"[0-9a-f]{40}(\.\.[0-9a-f]{40})?")

def api_log(q):
    from gitrun import git
    rev = q.get("rev", [""])[0]
    if not REV_RE.fullmatch(rev):
        raise ValueError("bad rev")
    path = q.get("path", [""])[0]
    if "\0" in path:
        raise ValueError("bad path")
    skip, limit = paging(q)
    args = ["log", "--date=short", f"--skip={skip}", "-n", str(limit),
            "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%s", rev, "--"]
    if path:
        args.append(path)
    out = git(*args, literal=bool(path))
    commits = [l.split("\x1f") for l in out.splitlines() if l]
    res = {"commits": commits, "has_more": len(commits) == limit}
    if skip == 0:
        count_args = ["rev-list", "--count", rev, "--"]
        if path:
            count_args.append(path)
        res["total"] = int(git(*count_args, literal=bool(path)).strip())
    return res

def paging(q):
    skip = int(q.get("skip", ["0"])[0])
    limit = min(int(q.get("limit", ["500"])[0]), 2000)
    if skip < 0 or limit < 1:
        raise ValueError("bad paging")
    return skip, limit

def api_stash_list(q):
    """stash の一覧 (新しい順)。api_log と同じ [hash, short, 選択子, 日時, 件名] の形で返す。"""
    from gitrun import git
    try:
        out = git("stash", "list", "--format=%H%x1f%h%x1f%gd%x1f%ci%x1f%gs")
    except subprocess.CalledProcessError:
        out = ""
    commits = []
    for l in out.splitlines():
        h, short, sel, date, subj = (l.split("\x1f") + [""] * 5)[:5]
        commits.append([h, short, sel, date[:16], subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}

def _ref_list(refs, fmt, kinds):
    """for-each-ref の結果を api_log と同じ [hash, short, 名前, 日付, 件名] の形にする。"""
    from gitrun import git
    out = git("for-each-ref", "--format=" + fmt, *refs)
    rows = []
    for l in out.splitlines():
        parts = (l.split("\x1f") + [""] * 6)[:6]
        rows.append(parts)
    return rows

def api_branches(q):
    """ローカルブランチ → リモート追跡ブランチの順の一覧。現在のブランチには ★ を付ける。"""
    from gitrun import git
    fmt = "%(refname)%1f%(objectname)%1f%(HEAD)%1f%(committerdate:short)%1f%(subject)"
    commits = []
    for ref, name_, head, date, subj in (r[:5] for r in _ref_list(["refs/heads"], fmt, None)):
        h = git("rev-parse", ref + "^{commit}").strip()
        commits.append([h, h[:7], ("★ " if head == "*" else "") + ref[len("refs/heads/"):], date, subj])
    if not state.OPTS.no_remotes:
        for ref, name_, head, date, subj in (r[:5] for r in _ref_list(["refs/remotes"], fmt, None)):
            if ref.endswith("/HEAD"):
                continue
            h = git("rev-parse", ref + "^{commit}").strip()
            commits.append([h, h[:7], ref[len("refs/remotes/"):], date, subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}

def api_branches_containing(q):
    """指定コミットを含むローカル / リモート追跡ブランチと各 tip を返す。"""
    from gitrun import git, resolve_commit
    h = resolve_commit(q.get("hash", [""])[0])
    refs = ["refs/heads"]
    if not state.OPTS.no_remotes:
        refs.append("refs/remotes")
    out = git("for-each-ref", "--contains=" + h, "--format=%(refname)%1f%(objectname)", *refs)
    branches = []
    for line in out.splitlines():
        ref, tip = line.split("\x1f", 1)
        if ref.endswith("/HEAD"):
            continue
        name = ref.removeprefix("refs/heads/").removeprefix("refs/remotes/")
        branches.append({"ref": ref, "name": name, "tip": tip})
    branches.sort(key=lambda b: (0 if b["ref"].startswith("refs/heads/") else 1, b["name"].casefold(), b["name"]))
    return {"branches": branches}

def api_tags(q):
    """タグの一覧 (新しい順)。注釈付きタグは指しているコミットを使う。"""
    from gitrun import git
    fmt = "%(refname)%1f%(creatordate:short)%1f%(*subject)%1f%(subject)"
    out = git("for-each-ref", "--sort=-creatordate", "--format=" + fmt, "refs/tags")
    commits = []
    for l in out.splitlines():
        ref, date, psubj, subj = (l.split("\x1f") + [""] * 4)[:4]
        try:
            h = git("rev-parse", "--verify", "--quiet", ref + "^{commit}").strip()
        except subprocess.CalledProcessError:  # コミットを指さないタグ (ツリーなど) は出さない
            continue
        commits.append([h, h[:7], ref[len("refs/tags/"):], date, psubj or subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}

def api_reflog(q):
    """reflog (新しい順)。branch 省略で HEAD、指定でそのローカルブランチ。"""
    from gitrun import check_ref_name, git
    from i18n import UserError
    branch = q.get("branch", [""])[0]
    if branch:
        check_ref_name("branch", branch)
        ref, label = "refs/heads/" + branch, branch
        if subprocess.run(["git", "-C", state.REPO, "show-ref", "--verify", "--quiet", ref]).returncode != 0:
            raise UserError("branch_not_found")
    else:
        ref, label = "HEAD", "HEAD"
    skip, limit = paging(q)
    try:  # --date=iso にすると %gd が "名前@{日時}" になる (リフログの時刻を出したい)。番号は並び順から振る
        out = git("reflog", "show", "--date=iso", f"--skip={skip}", "-n", str(limit),
                  "--format=%H%x1f%h%x1f%gd%x1f%gs", ref, "--")
    except subprocess.CalledProcessError:  # コミットがまだない / reflog がない
        out = ""
    commits = []
    for i, l in enumerate(x for x in out.splitlines() if x):
        h, short, gd, subj = (l.split("\x1f") + [""] * 4)[:4]
        when = gd[gd.find("@{") + 2:-1][:16] if "@{" in gd else ""
        commits.append([h, short, f"{label}@{{{skip + i}}}", when, subj])
    res = {"commits": commits, "has_more": len(commits) == limit}
    if skip == 0:
        res["total"] = len(commits) if not res["has_more"] else sum(
            1 for _ in git("reflog", "show", "--format=%H", ref, "--").splitlines())
    return res

def commit_info(h):
    from gitrun import HASH_RE, git
    if not HASH_RE.fullmatch(h):
        raise ValueError("bad hash")
    o = git("show", "-s", "--date=iso", "--format=%H%x1f%an <%ae>%x1f%ad%x1f%P%x1f%B", h, "--")
    full, author, date, par, msg = (o.split("\x1f", 4) + [""] * 5)[:5]
    return {"hash": full, "author": author, "date": date, "parents": par.split(),
            "message": msg.strip()}

def api_commit(q):
    from gitrun import git
    info = commit_info(q.get("hash", [""])[0])
    raw = git("show", "--numstat", "-z", "-M", "--first-parent", "--format=", info["hash"], "--")
    info["files"] = parse_numstat(raw)
    return info

FILE_CONTENT_LIMIT = 2_000_000

def validate_file_path(path, allow_empty=True):
    """リポジトリルートからの相対パスとして安全な形にする。"""
    if "\0" in path or (not allow_empty and not path) or path.startswith("/"):
        raise ValueError("bad path")
    parts = path.split("/") if path else []
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("bad path")
    return path

def api_tree(q):
    from gitrun import git, resolve_commit
    from i18n import UserError
    h = resolve_commit(q.get("hash", [""])[0])
    path = validate_file_path(q.get("path", [""])[0])
    treeish = h if not path else h + ":" + path
    try:
        raw = git("ls-tree", "-z", "-l", treeish)
    except subprocess.CalledProcessError:
        raise UserError("dir_not_found")
    entries = []
    for item in raw.split("\0"):
        if not item:
            continue
        head, name = item.split("\t", 1)
        mode, kind, obj, size = (head.split() + [""])[:4]
        entries.append({"name": name, "type": kind, "size": None if size == "-" else int(size), "mode": mode})
    return {"hash": h, "path": path, "entries": sorted(entries, key=lambda x: (x["type"] != "tree", x["name"]))}

_BOMS = ((codecs.BOM_UTF8, "utf-8-sig", "UTF-8 (BOM)"),
         (codecs.BOM_UTF32_LE, "utf-32", "UTF-32"), (codecs.BOM_UTF32_BE, "utf-32", "UTF-32"),
         (codecs.BOM_UTF16_LE, "utf-16", "UTF-16"), (codecs.BOM_UTF16_BE, "utf-16", "UTF-16"))

def _strict_decode(data, enc, truncated):
    """厳密にデコードする。先頭のみ読んだ場合は末尾の欠けた文字を最大3バイト捨てて再試行する。"""
    for cut in ((0, 1, 2, 3) if truncated else (0,)):
        try:
            return data[:len(data) - cut].decode(enc)
        except UnicodeDecodeError:
            pass
    return None

def _jp_score(text):
    """日本語として自然なほど高い点を返す (Shift_JIS と EUC-JP の判別用)。"""
    score = 0
    for ch in text:
        c = ord(ch)
        if 0x3040 <= c <= 0x30FF or 0x4E00 <= c <= 0x9FFF:
            score += 1
        elif 0xFF61 <= c <= 0xFF9F:  # 半角カナは誤判定のときに増える
            score -= 3
        elif c < 0x20 and ch not in "\t\r\n":
            score -= 5
    return score

def decode_text(data, truncated=False):
    """バイト列の文字コードを自動判別してデコードし、(text, 文字コード名) を返す。バイナリなら (None, None)。
    BOM → UTF-8 → ISO-2022-JP → Shift_JIS(CP932) / EUC-JP (点数の高い方) の順に試し、全て失敗したら UTF-8 で置換する。"""
    for bom, enc, label in _BOMS:
        if data.startswith(bom):
            text = _strict_decode(data, enc, truncated)
            if text is not None:
                return text, label
    if b"\0" in data:
        return None, None
    if b"\x1b$B" in data or b"\x1b$@" in data or b"\x1b$(" in data:  # ISO-2022-JP は 7bit なので UTF-8 より先に見る
        text = _strict_decode(data, "iso2022_jp", truncated)
        if text is not None:
            return text, "ISO-2022-JP"
    text = _strict_decode(data, "utf-8", truncated)
    if text is not None:
        return text, "ASCII" if text.isascii() else "UTF-8"
    best = None
    for enc, label in (("cp932", "Shift_JIS (CP932)"), ("euc_jp", "EUC-JP")):
        text = _strict_decode(data, enc, truncated)
        if text is not None:
            score = _jp_score(text)
            if best is None or score > best[0]:
                best = (score, text, label)
    if best:
        return best[1], best[2]
    return data.decode("utf-8", errors="replace"), "UTF-8 (invalid bytes replaced)"

def api_file(q):
    from gitrun import git, resolve_commit
    from i18n import UserError
    h = resolve_commit(q.get("hash", [""])[0])
    path = validate_file_path(q.get("path", [""])[0], allow_empty=False)
    spec = h + ":" + path
    kind = git("cat-file", "-t", spec).strip()
    if kind != "blob":
        raise UserError("not_a_file")
    raw = subprocess.check_output(["git", "-C", state.REPO, "cat-file", "blob", spec])
    truncated = len(raw) > FILE_CONTENT_LIMIT
    data = raw[:FILE_CONTENT_LIMIT]
    text, encoding = decode_text(data, truncated)
    return {"hash": h, "path": path, "content": text or "", "encoding": encoding,
            "binary": text is None, "truncated": truncated, "size": len(raw)}

def api_file_copy(body):
    """コミット内のファイルを、作業ツリーの別のパスにコピーする。既存のファイルは上書きしない。"""
    from gitrun import git, resolve_commit
    from i18n import UserError, tr
    if not state.HAS_WORKTREE:
        raise UserError("copy_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    path = validate_file_path(body.get("path", ""), allow_empty=False)
    dest = body.get("dest", "")
    if not isinstance(dest, str):
        raise UserError("copy_dest_invalid")
    dest = dest.strip()
    try:
        validate_file_path(dest, allow_empty=False)
    except ValueError:
        raise UserError("copy_dest_invalid")
    if "\\" in dest or dest.split("/")[0].lower() == ".git":
        raise UserError("copy_dest_invalid")
    root = os.path.realpath(state.REPO)
    target = os.path.realpath(os.path.join(root, *dest.split("/")))
    try:
        inside = os.path.commonpath([root, target]) == root
    except ValueError:  # 別ドライブなど
        inside = False
    if not inside or target == root:
        raise UserError("copy_dest_invalid")
    spec = h + ":" + path
    if git("cat-file", "-t", spec).strip() != "blob":
        raise UserError("not_a_file")
    if os.path.lexists(target):
        raise UserError("copy_dest_exists", path=dest)
    # --filters で改行コードなどを作業ツリーでの形 (autocrlf など) にそろえる。使えない git では素のまま
    r = subprocess.run(["git", "-C", state.REPO, "cat-file", "--filters", spec], capture_output=True)
    if r.returncode != 0:
        r = subprocess.run(["git", "-C", state.REPO, "cat-file", "blob", spec], capture_output=True)
        if r.returncode != 0:
            raise ValueError(r.stderr.decode("utf-8", errors="replace").strip() or tr("git_command_failed"))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "xb") as f:
            f.write(r.stdout)
    except OSError as e:
        raise ValueError(str(e))
    return {"ok": True, "message": tr("file_copied", path=dest)}

def api_compare(q):
    """2つのコミット間の差分 (from → to) のファイル一覧。"""
    from gitrun import git
    a = commit_info(q.get("from", [""])[0])
    b = commit_info(q.get("to", [""])[0])
    raw = git("diff", "--numstat", "-z", "-M", a["hash"], b["hash"], "--")
    return {"from": a, "to": b, "files": parse_numstat(raw)}

def api_merge_base(q):
    """2つのコミットの共通祖先 (最も新しいもの)。"""
    from gitrun import git, resolve_commit
    a = resolve_commit(q.get("from", [""])[0])
    b = resolve_commit(q.get("to", [""])[0])
    try:
        base = git("merge-base", a, b).strip()
    except subprocess.CalledProcessError:
        base = ""
    return {"hash": base}

def parse_numstat(raw):
    """--numstat -z の出力を [{path, old_path, added, deleted}] にする (バイナリは None)。"""
    toks = raw.split("\0")
    files, i = [], 0
    while i < len(toks):
        t = toks[i].lstrip("\n")
        if not t:
            i += 1
            continue
        a, d, path = (t.split("\t", 2) + ["", ""])[:3]
        old = None
        if path == "":  # リネーム: 続く2トークンが 旧パス, 新パス
            old, path = toks[i + 1], toks[i + 2]
            i += 3
        else:
            i += 1
        files.append({"path": path, "old_path": old,
                      "added": None if a == "-" else int(a),
                      "deleted": None if d == "-" else int(d)})
    return files

DIFF_LIMIT = 400000

def api_diff(q):
    """1ファイルの差分。hash=... (1コミット) か from=...&to=... (2コミット間) を指定する。"""
    from gitrun import HASH_RE, git_bytes
    from wip import wip_diff
    path = q.get("path", [""])[0]
    old = q.get("old", [""])[0]
    if not path or "\0" in path or "\0" in old:
        raise ValueError("bad path")
    specs = [old, path] if old else [path]
    wip = q.get("wip", [""])[0]
    if wip:  # 作業ツリーの変更
        out = wip_diff(wip, path, specs)
    elif "from" in q or "to" in q:
        a, b = q.get("from", [""])[0], q.get("to", [""])[0]
        if not (HASH_RE.fullmatch(a) and HASH_RE.fullmatch(b)):
            raise ValueError("bad hash")
        out = git_bytes("diff", "--no-color", "-M", a, b, "--", *specs, literal=True)
    else:
        h = q.get("hash", [""])[0]
        if not HASH_RE.fullmatch(h):
            raise ValueError("bad hash")
        out = git_bytes("show", "--format=", "--first-parent", "-M", "--no-color", h, "--", *specs, literal=True)
    truncated = len(out) > DIFF_LIMIT
    diff, encoding = decode_text(out[:DIFF_LIMIT], truncated=truncated)
    if diff is None:
        # バイナリ差分でも git の "Binary files ... differ" 等は表示できるようにする。
        diff, encoding = out[:DIFF_LIMIT].decode("utf-8", errors="replace"), "UTF-8 (binary diff)"
    return {"diff": diff, "encoding": encoding, "truncated": truncated}

WIP_DIR_COLLAPSE = 10

def dir_of(path):
    return path.rpartition("/")[0]

DIFF_CSS = """
  html, body { height: 100%; margin: 0; font-family: system-ui, sans-serif; font-size: 13px; color: #222; }
  body { display: flex; }
  #side { width: 320px; flex: none; overflow: auto; border-right: 1px solid #ccc; background: #fafafa; padding: 10px; box-sizing: border-box; }
  #side a { display: flex; justify-content: space-between; gap: 8px; padding: 3px 6px; color: #06c; text-decoration: none;
            font-family: monospace; font-size: 12px; border-radius: 3px; word-break: break-all; }
  #side a:hover { background: #eef; }
  #side a.cur { background: #ffe9a0; color: #222; font-weight: bold; }
  #side .n { white-space: nowrap; }
  #side .grp { margin: 10px 0 2px; font-weight: bold; }
  #side .srow { display: flex; align-items: center; } #side .srow a { flex: 1; min-width: 0; }
  #side input[type=checkbox] { margin: 0 6px 0 0; cursor: pointer; }
  .cbox { margin: 6px 0 8px; } .cbox textarea { width: 100%; box-sizing: border-box; min-height: 56px; padding: 4px 6px; border: 1px solid #bbb; border-radius: 3px; font: inherit; resize: vertical; }
  .cbox button { margin-top: 4px; } .cbox .hint { white-space: pre-wrap; }
  .wbtn { background: #1a7f37; color: #fff; border: 1px solid #176b30; padding: 3px 10px; border-radius: 3px; cursor: pointer; }
  .wbtn:hover { background: #176b30; } .wbtn:disabled { opacity: .5; cursor: default; }
  #main { flex: 1; min-width: 0; overflow: auto; padding: 14px 18px; }
  h1 { font-size: 15px; margin: 0 0 6px; word-break: break-all; }
  .meta { color: #555; margin: 2px 0; }
  code { background: #eee; padding: 1px 4px; border-radius: 3px; user-select: all; }
  .plus { color: #1a7f37; } .minus { color: #cf222e; }
  .hint { color: #777; }
  .copy { cursor: pointer; font-size: 12px; margin-left: 6px; user-select: none; text-decoration: none; border-radius: 3px; padding: 0 2px; }
  .copy:hover { background: #e4e4e4; }
  .tag { display: inline-block; padding: 0 6px; border-radius: 3px; margin-right: 4px; font-weight: bold; color: #fff; }
  .tagA { background: #06c; } .tagB { background: #e67e00; }
  .diff { font-family: monospace; font-size: 12px; white-space: pre; margin-top: 12px; border: 1px solid #ddd; }
  .dl { padding: 0 6px; min-width: max-content; }
  .dl.ad { background: #e6ffec; } .dl.de { background: #ffebe9; }
  .dl.hk { background: #ddf4ff; color: #555; } .dl.hd { color: #777; background: #f6f8fa; }
  #side .srow.dhide { display: none; }
  #side .drow { margin-top: 2px; padding: 3px 6px; background: #f1f3f6; cursor: pointer; border-radius: 3px;
                font-family: monospace; font-size: 12px; word-break: break-all; }
  #side .drow:hover { background: #e6ebf5; }
  #side .drow .dname { flex: 1; min-width: 0; }
  #side .dtog { display: inline-block; width: 1em; color: #666; }
"""

def diff_lines_html(text):
    """差分を行ごとに色分けする (ヘッダ部と本文を区別)。"""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    in_hunk, out = False, []
    for l in lines:
        cls = ""
        if l.startswith("diff "):
            in_hunk, cls = False, "hd"
        elif not in_hunk:
            if l.startswith("@@"):
                in_hunk, cls = True, "hk"
            else:
                cls = "hd"
        elif l.startswith("@@"):
            cls = "hk"
        elif l.startswith("+"):
            cls = "ad"
        elif l.startswith("-"):
            cls = "de"
        elif l.startswith("\\"):
            cls = "hd"
        out.append(f'<div class="dl {cls}">{html.escape(l) or " "}</div>')
    return "".join(out)

def localized_wip_title(kind):
    from i18n import tr
    return {
        "amend": tr("amend_content"),
        "conflict": tr("conflict"),
        "staged": tr("staged"),
        "unstaged": tr("unstaged"),
        "untracked": tr("untracked"),
    }.get(kind, kind)

def diff_url(ctx, f):
    params = dict(ctx, path=f["path"])
    if f.get("wip"):
        params["wip"] = f["wip"]
    if f.get("old_path"):
        params["old"] = f["old_path"]
    return "/diff?" + urlencode(params)

def commit_line_html(label, c):
    from i18n import tr
    subject = c["message"].split("\n", 1)[0]
    return (f'<div class="meta"><span class="tag tag{label}">{label}</span>{c["hash"][:7]}'
            f'<a class="copy" data-copy="{c["hash"]}" title="{html.escape(tr("copy_hash_title"))}">📋</a> — '
            f'{html.escape(subject)} <span class="hint">({html.escape(c["author"])} / '
            f'{html.escape(c["date"])})</span></div>')

def wip_files(kind="", status=None):
    """WIP の全ファイルを 競合 → ステージ済み → 未ステージ → 未追跡 の順に、種別(wip)つきで返す。
    kind が amend のときは、amend 後のコミットの内容 (直前のコミットの親との差分) を返す。"""
    from i18n import UserError
    from wip import api_status
    if kind == "amend":
        s = api_status({"amend": ["1"]})
        if "amend_files" not in s:
            raise UserError("amend_unavailable")
        return s["amend_files"]
    s = status or api_status({})
    out = []
    for kind, key in (("conflict", "conflicts"), ("staged", "staged"),
                      ("unstaged", "unstaged"), ("untracked", "untracked")):
        out.extend(dict(f, wip=kind) for f in s[key])
    return out

def render_diff_page(q):
    """1ファイルの差分ページ。左にそのコミット(比較 / WIP)の変更ファイル一覧を出す。"""
    from i18n import tr
    from wip import api_status
    path = q.get("path", [""])[0]
    old = q.get("old", [""])[0]
    wip = q.get("wip", [""])[0]
    d = api_diff(q)
    wip_status = None
    if wip:  # 作業ツリーの変更
        if wip != "amend":
            wip_status = api_status({})
        files, ctx, ref = wip_files(wip, wip_status), {}, "WIP"
        info = f'<div class="meta">{html.escape(tr("working_tree"))} — {html.escape(localized_wip_title(wip))}</div>'
        cmd = {"staged": f"git diff --cached -- {path}", "amend": f"git diff HEAD^ -- {path}",
               "untracked": f"git diff --no-index /dev/null {path}"}.get(wip, f"git diff -- {path}")
    elif "from" in q:  # 2コミット間の比較
        c = api_compare(q)
        a, b = c["from"], c["to"]
        files, ctx = c["files"], {"from": a["hash"], "to": b["hash"]}
        ref = f"{a['hash'][:7]}..{b['hash'][:7]}"
        info = commit_line_html("A", a) + commit_line_html("B", b)
        cmd = f"git diff {a['hash'][:7]} {b['hash'][:7]} -- {path}"
    else:
        c = api_commit(q)
        files, ctx, ref = c["files"], {"hash": c["hash"]}, c["hash"][:7]
        subject = c["message"].split("\n", 1)[0]
        info = (f'<div class="meta">{html.escape(tr("commit_label"))} {ref}'
                f'<a class="copy" data-copy="{c["hash"]}" title="{html.escape(tr("copy_hash_title"))}">📋</a>'
                f' — {html.escape(subject)}</div>'
                f'<div class="meta">{html.escape(c["author"])} / {html.escape(c["date"])}</div>')
        cmd = f"git show {ref} -- {path}"
    name = f"{old} => {path}" if old else path

    can_commit = bool(wip_status is not None and state.WRITABLE and state.WIP_ENABLED)  # amend の差分ページには出さない
    staged_paths = {f["path"] for f in wip_status["staged"]} if can_commit else set()
    merging = bool(wip_status and wip_status.get("merging"))

    # WIP: ディレクトリごとの件数・行数 (右ペインの filesHtml と同じ集計)
    dir_count, dir_stat = Counter(), {}
    if wip:
        for f in files:
            key = (f.get("wip"), dir_of(f["path"]))
            dir_count[key] += 1
            st = dir_stat.setdefault(key, [0, 0])
            st[0] += f.get("added") or 0
            st[1] += f.get("deleted") or 0
    cur_dir_key = (wip, dir_of(path)) if wip else None  # いま開いているファイルのディレクトリは最初から開く
    seen_dirs = set()
    attr = lambda s: html.escape(s, quote=True)

    side, last = [], None
    for f in files:
        if wip and f.get("wip") != last:
            last = f.get("wip")
            gall = (f'<input type="checkbox" class="gall" data-kind="{html.escape(last)}" '
                    f'title="{html.escape(tr("select_all"))}"> ') if can_commit else ""
            side.append(f'<div class="grp">{gall}{html.escape(localized_wip_title(last))}</div>')
        fdir = dir_of(f["path"])
        key = (f.get("wip"), fdir)
        folded = bool(wip) and dir_count[key] >= WIP_DIR_COLLAPSE
        is_cur_dir = folded and key == cur_dir_key
        if folded and key not in seen_dirs:
            seen_dirs.add(key)
            dck = (f'<input type="checkbox" class="dck" data-kind="{attr(f["wip"])}" data-dir="{attr(fdir)}"> '
                   if can_commit else "")
            data_cur = ' data-cur="1"' if is_cur_dir else ""
            label = fdir + "/" if fdir else tr("dir_root")
            plus, minus = dir_stat[key]
            side.append(
                f'<div class="srow drow" data-kind="{attr(f["wip"])}" data-dir="{attr(fdir)}"'
                f'{data_cur} title="{attr(tr("dir_toggle"))}">{dck}'
                f'<span class="dname"><span class="dtog">▶</span> {html.escape(label)} '
                f'<span class="hint">({html.escape(tr("n_items", n=dir_count[key]))}) '
                f'<span class="plus">+{plus}</span> <span class="minus">-{minus}</span></span></span></div>')
        fname = f"{f['old_path']} => {f['path']}" if f.get("old_path") else f["path"]
        if f.get("label"):
            counts = html.escape(tr(f["label"]))
        elif "added" not in f:
            counts = ""
        elif f["added"] is None:
            counts = "Bin"
        else:
            counts = (f'<span class="plus">+{f["added"]}</span> '
                      f'<span class="minus">-{f["deleted"]}</span>')
        cur = "cur" if f["path"] == path and (not wip or f.get("wip") == wip) else ""
        link = (f'<a class="{cur}" href="{html.escape(diff_url(ctx, f))}">'
                f'<span>{html.escape(fname)}</span><span class="n">{counts}</span></a>')
        if wip:
            box = ""
            if can_commit:
                on = " checked" if (merging or f["path"] in staged_paths) else ""
                box = (f'<input type="checkbox" class="wck" data-kind="{attr(f["wip"])}" '
                       f'data-dir="{attr(fdir)}" data-path="{attr(f["path"])}"{on}>')
            hide = " dhide" if folded and not is_cur_dir else ""
            link = (f'<div class="srow frow{hide}" data-kind="{attr(f["wip"])}" '
                    f'data-dir="{attr(fdir)}">{box}{link}</div>')
        side.append(link)
    commit_box = ""
    commit_js = ""
    fold_js = DIFF_FOLD_JS if wip else ""
    if can_commit:
        commit_box = ('<div class="cbox"><textarea id="cmsg" placeholder="' + html.escape(tr("wip_commit_message"), quote=True) + '"></textarea>'
                      '<div><button id="cbtn" class="wbtn">' + html.escape(tr("wip_commit")) + '</button></div>'
                      '<div id="cout" class="hint"></div></div>')
        texts = {k: tr(k) for k in ("wip_commit", "message_required", "select_files", "wip_execute")}
        commit_js = "const T = " + json.dumps(texts, ensure_ascii=False).replace("</", "<\\/") + ";\n" + WIP_DIFF_COMMIT_JS
    return f"""<!DOCTYPE html>
<html lang="{state.UI_LANG}"><head><meta charset="utf-8">
<title>{html.escape(path)} @ {ref} - {html.escape(state.PROJECT_NAME)}</title>
{FAVICON_LINK}
<style>{DIFF_CSS}</style></head><body>
<div id="side">
  <div class="meta">{len(files)} {tr("files_changed")}</div>
  {commit_box}
  {"".join(side)}
</div>
<div id="main">
  <h1>{html.escape(name)}</h1>
  {info}
  <div class="meta"><code>{html.escape(cmd)}</code></div>
  <div class="meta">{html.escape(d["encoding"] or "binary")}</div>
  {'<p class="hint">' + tr("large_file_diff") + '</p>' if d["truncated"] else ""}
  <div class="diff">{diff_lines_html(d["diff"]) or '<p class="hint">' + tr("no_diff") + '</p>'}</div>
</div>
<script>{COPY_JS}</script>
<script>{fold_js}</script>
<script>{commit_js}</script>
</body></html>
"""

DIFF_FOLD_JS = r"""
const OPEN_KEY = "git-graph-wip-open-dirs";  // 右ペイン (WIP 一覧) と共有する。キーは "種別|ディレクトリ"
function loadOpen() { try { return new Set(JSON.parse(sessionStorage.getItem(OPEN_KEY) || "[]")); } catch (e) { return new Set(); } }
function saveOpen(s) { try { sessionStorage.setItem(OPEN_KEY, JSON.stringify([...s])); } catch (e) {} }
const openDirs = loadOpen();
function setDir(row, on) {
  row.querySelector(".dtog").textContent = on ? "▼" : "▶";
  document.querySelectorAll(".frow").forEach(r => {
    if (r.dataset.kind === row.dataset.kind && r.dataset.dir === row.dataset.dir) r.classList.toggle("dhide", !on);
  });
}
document.querySelectorAll(".drow").forEach(row => {
  const key = row.dataset.kind + "|" + row.dataset.dir;
  if (row.dataset.cur) openDirs.add(key);
  setDir(row, openDirs.has(key));
});
saveOpen(openDirs);
document.addEventListener("click", ev => {
  const row = ev.target.closest(".drow");
  if (!row || ev.target.closest("input")) return;
  const key = row.dataset.kind + "|" + row.dataset.dir, on = !openDirs.has(key);
  if (on) openDirs.add(key); else openDirs.delete(key);
  saveOpen(openDirs);
  setDir(row, on);
});
"""

WIP_DIFF_COMMIT_JS = r"""
const cks = () => [...document.querySelectorAll(".wck")];
function paths() { return [...new Set(cks().filter(x => x.checked).map(x => x.dataset.path))]; }
function sync() {
  const n = paths().length, b = document.getElementById("cbtn");
  if (!b) return;
  b.disabled = n === 0;
  b.textContent = T.wip_commit + " (" + n + ")";
  document.querySelectorAll(".gall").forEach(g => {
    const c = cks().filter(x => x.dataset.kind === g.dataset.kind), k = c.filter(x => x.checked).length;
    g.checked = c.length > 0 && k === c.length;
    g.indeterminate = k > 0 && k < c.length;
  });
  document.querySelectorAll(".dck").forEach(g => {
    const c = cks().filter(x => x.dataset.kind === g.dataset.kind && x.dataset.dir === g.dataset.dir), k = c.filter(x => x.checked).length;
    g.checked = c.length > 0 && k === c.length;
    g.indeterminate = k > 0 && k < c.length;
  });
}
document.addEventListener("change", ev => {
  const setPath = (p, v) => cks().filter(y => y.dataset.path === p).forEach(y => { y.checked = v; });
  const g = ev.target.closest(".gall"), d = ev.target.closest(".dck");
  if (g) cks().filter(x => x.dataset.kind === g.dataset.kind).forEach(x => setPath(x.dataset.path, g.checked));
  else if (d) cks().filter(x => x.dataset.kind === d.dataset.kind && x.dataset.dir === d.dataset.dir)
    .forEach(x => setPath(x.dataset.path, d.checked));
  else if (ev.target.closest(".wck")) setPath(ev.target.dataset.path, ev.target.checked);
  else return;
  sync();
});
const cbtn = document.getElementById("cbtn");
if (cbtn) cbtn.addEventListener("click", () => {
  const out = document.getElementById("cout"), msg = document.getElementById("cmsg").value;
  const say = (text, ok) => { out.textContent = text; out.style.color = ok ? "#1a7f37" : "#cf222e"; };
  if (!msg.trim()) { say(T.message_required, false); return; }
  if (!paths().length) { say(T.select_files, false); return; }
  cbtn.disabled = true; say(T.wip_execute, true);
  fetch("/api/wip-commit", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({paths: paths(), message: msg, amend: false})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => { say(r.message, true); setTimeout(() => location.reload(), 800); })
    .catch(e => { say(e.message, false); sync(); });
});
sync();
"""

FILES_CSS = """
  html, body { height: 100%; margin: 0; font-family: system-ui, sans-serif; font-size: 13px; color: #222; }
  body { display: flex; }
  #tree { width: 360px; flex: none; overflow: auto; border-right: 1px solid #ccc; background: #fafafa; padding: 12px; box-sizing: border-box; }
  #main { flex: 1; min-width: 0; overflow: auto; padding: 14px 18px; box-sizing: border-box; }
  h1 { font-size: 15px; margin: 0 0 8px; word-break: break-all; }
  h2 { font-size: 14px; margin: 0 0 8px; }
  .meta, .hint { color: #666; margin: 3px 0; }
  .crumb { margin: 8px 0; word-break: break-all; }
  .crumb a, .entry { color: #06c; text-decoration: none; cursor: pointer; }
  .crumb a:hover, .entry:hover { text-decoration: underline; }
  .entry { display: flex; gap: 8px; padding: 4px 6px; border-radius: 3px; font-family: monospace; }
  .entry:hover { background: #eef; }
  .entry .size { margin-left: auto; color: #777; white-space: nowrap; }
  pre { white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid #ddd; padding: 10px; font: 12px monospace; }
  pre.code { color: #24292f; tab-size: 4; }
  pre.code { padding-left: 0; }
  .ln { display: flex; }
  .ln:hover { background: #f6f8fa; }
  .ln .no { flex: none; width: var(--nw, 3ch); box-sizing: content-box; padding: 0 10px; margin-right: 10px; text-align: right; color: #8c959f; border-right: 1px solid #e3e3e3; user-select: none; }
  .ln .tx { flex: 1; min-width: 0; }
  .tok-keyword { color: #8250df; font-weight: 600; }
  .tok-string { color: #0a7b35; }
  .tok-comment { color: #6e7781; font-style: italic; }
  .tok-number { color: #0550ae; }
  .tok-type { color: #953800; }
  .tok-tag { color: #116329; }
  .tok-attr { color: #8250df; }
  .tok-regex { color: #cf222e; }
  .binary { color: #777; padding: 10px; border: 1px solid #ddd; }
  .file-actions { display: flex; gap: 6px; margin: 8px 0; }
  .file-actions button { background: #e8eefc; border: 1px solid #b8c7ee; padding: 4px 8px; border-radius: 3px; cursor: pointer; }
  .file-actions button:hover { background: #d6e2fb; }
  .file-actions .msg { margin-left: 8px; color: #0a7b35; white-space: pre-wrap; }
  .file-actions .msg.err { color: #c00; }
  table.file-log { border-collapse: collapse; width: 100%; margin-top: 8px; }
  table.file-log td { border-bottom: 1px solid #e3e3e3; padding: 5px 6px; vertical-align: top; }
  table.file-log td.hash { width: 1%; font-family: monospace; white-space: nowrap; }
  table.file-log td.date { width: 1%; color: #777; white-space: nowrap; }
  table.file-log a { color: inherit; text-decoration: none; }
  table.file-log tr:hover td { background: #eef; }
"""

FILES_PAGE = r"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>{{file_view}} - __PROJECT__</title>
__FAVICON__
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<style>__CSS__</style><script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script></head>
<body><aside id="tree"><h1>{{file_view}}</h1><div class="meta" id="commit"></div><div id="list"></div></aside>
<main id="main"><h2 id="title">{{select_file}}</h2>
<div id="fileActions" class="file-actions" hidden><button id="fileLogBtn">{{file_log}}</button><button id="fileContentBtn" hidden>{{file_content}}</button><button id="fileCopyBtn" hidden>{{copy_to_work}}</button><span id="fileMsg" class="msg"></span></div>
<div id="content" class="hint">{{select_file_hint}}</div></main>
<script>
/*__FILE_I18N__*/
function t(key, values = {}) {
  let text = key in I18N ? I18N[key] : key;
  Object.entries(values).forEach(([name, value]) => { text = text.replaceAll("{" + name + "}", () => String(value)); });
  return text;
}

const PROJECT_DOCUMENT_TITLE = document.title.substring(document.title.indexOf(" - ") + 3);
function setDocumentTitle(label) {
  document.title = label ? label + " - " + PROJECT_DOCUMENT_TITLE : t("file_view") + " - " + PROJECT_DOCUMENT_TITLE;
}

const qs = new URLSearchParams(location.search), hash = qs.get("hash"), fileViewTab = qs.get("file_view") === "1";
const list = document.getElementById("list"), content = document.getElementById("content"), title = document.getElementById("title");
const fileActions = document.getElementById("fileActions"), fileLogBtn = document.getElementById("fileLogBtn"), fileContentBtn = document.getElementById("fileContentBtn");
const fileCopyBtn = document.getElementById("fileCopyBtn"), fileMsg = document.getElementById("fileMsg");
fileCopyBtn.hidden = !CAN_COPY;
let current = "", currentFile = "";
function esc(s) { return String(s).replace(/[&<>\"]/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;"}[c])); }
function api(path, params) { return fetch(path + "?" + new URLSearchParams(params)).then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); })); }
function showError(e) { list.innerHTML = '<p class="hint">' + t("error") + esc(e.message) + '</p>'; }
function loadTree(path, push) {
  current = path;
  fileActions.hidden = true;
  if (push) history.pushState({path: path}, "", "?hash=" + encodeURIComponent(hash) + (path ? "&path=" + encodeURIComponent(path) : ""));
  api("/api/tree", {hash: hash, path: path}).then(r => {
    document.getElementById("commit").textContent = r.hash.slice(0, 7);
    const parts = path ? path.split("/") : [];
    let crumb = '<div class="crumb"><a data-dir="">📁 ' + t("root") + '</a>';
    parts.forEach((p, i) => { const d = parts.slice(0, i + 1).join("/"); crumb += ' / <a data-dir="' + esc(d) + '">' + esc(p) + '</a>'; });
    crumb += '</div>';
    list.innerHTML = crumb + (r.entries.length ? r.entries.map(e => {
      const p = path ? path + "/" + e.name : e.name;
      const icon = e.type === "tree" ? "📁" : "📄";
      const size = e.size === null ? "" : e.size.toLocaleString() + " B";
      return '<div class="entry" data-type="' + e.type + '" data-path="' + esc(p) + '"><span>' + icon + ' ' + esc(e.name) + '</span><span class="size">' + size + '</span></div>';
    }).join("") : '<p class="hint">' + t("empty_directory") + '</p>');
    list.querySelectorAll("[data-dir]").forEach(a => a.onclick = () => loadTree(a.dataset.dir, true));
    list.querySelectorAll(".entry").forEach(a => a.onclick = () => a.dataset.type === "tree" ? loadTree(a.dataset.path, true) : loadFile(a.dataset.path));
  }).catch(showError);
}
function setFileMsg(text, ok) { fileMsg.textContent = text; fileMsg.className = "msg" + (ok ? "" : " err"); }
function defaultCopyName(path) {  // 例: src/a.py → src/a_1234567.py
  const i = path.lastIndexOf("/"), dir = path.slice(0, i + 1), name = path.slice(i + 1), dot = name.lastIndexOf(".");
  const suffix = "_" + hash.slice(0, 7);
  return dir + (dot > 0 ? name.slice(0, dot) + suffix + name.slice(dot) : name + suffix);
}
fileCopyBtn.onclick = () => {
  if (!currentFile) return;
  const dest = prompt(t("prompt_copy_dest"), defaultCopyName(currentFile));
  if (dest === null || !dest.trim()) return;
  fetch("/api/file-copy", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({hash: hash, path: currentFile, dest: dest.trim()})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => setFileMsg(r.message, true))
    .catch(e => setFileMsg(e.message, false));
};
function loadFile(path) {
  currentFile = path;
  setDocumentTitle(path);
  setFileMsg("", true);
  fileActions.hidden = false;
  fileLogBtn.hidden = false;
  fileContentBtn.hidden = true;
  api("/api/file", {hash: hash, path: path}).then(r => {
    const lang = languageFor(r.path);
    title.textContent = r.path + (lang ? " (" + lang + ")" : "") + (r.encoding ? " [" + r.encoding + "]" : "");
    content.className = r.binary ? "binary" : "";
    content.innerHTML = r.binary ? t("binary_file") : numberedCode(r.content, lang) + (r.truncated ? '<p class="hint">' + t("large_file_head") + '</p>' : '');
  }).catch(e => { title.textContent = path; content.className = "hint"; content.textContent = t("error") + e.message; });
}
function loadFileLog(path) {
  if (fileViewTab || (window.opener && !window.opener.closed)) {
    // 別タブでは親のグラフへ通知せず、このファイル画面にログを表示する。
    fileLogBtn.hidden = true;
    fileContentBtn.hidden = false;
    setDocumentTitle(t("file_history_title", {path: path}));
    title.textContent = t("file_history_title", {path: path});
    content.className = "hint";
    content.textContent = t("loading");
    api("/api/log", {rev: hash, path: path}).then(r => {
      const rows = r.commits.map(c => '<tr><td class="hash">' + esc(c[1]) +
        '</td><td class="date">' + esc(c[3]) + '</td><td>' + esc(c[4]) + '</td></tr>').join("");
      content.className = "";
      content.innerHTML = '<div class="meta">' + esc(t("n_commits", {n: r.total})) + '</div>' +
        (rows ? '<table class="file-log"><tbody>' + rows + '</tbody></table>' : '<p class="hint">' + t("no_history") + '</p>');
    }).catch(e => { content.className = "hint"; content.textContent = t("error") + e.message; });
    return;
  }
  // 同一タブではファイル画面にログを描画せず、グラフ画面へ戻して表示する。
  location.href = "/?file_log_hash=" + encodeURIComponent(hash) + "&file_log_path=" + encodeURIComponent(path);
}
fileLogBtn.onclick = () => { if (currentFile) loadFileLog(currentFile); };
fileContentBtn.onclick = () => { if (currentFile) loadFile(currentFile); };
const KEYWORDS = {
  python: "and as assert async await break case class continue def del elif else except finally for from global if import in is lambda match nonlocal not or pass raise return try while with yield None True False self",
  javascript: "as async await break case catch class const continue debugger default delete do else export extends false finally for from function get if import in instanceof let new null of return set static super switch this throw true try typeof undefined var void while with yield",
  typescript: "as async await break case catch class const continue debugger default delete do else export extends false finally for from function get if import in instanceof interface let new null of private protected public readonly return set static super switch this throw true try typeof type undefined var void while with yield",
  java: "abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while true false null",
  c: "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while true false NULL",
  go: "break default func interface select case defer go map struct chan else goto package switch const fallthrough if range type continue for import return var true false nil",
  rust: "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while",
  sql: "select from where and or not insert into update delete create alter drop table index view join inner left right full outer on as group by order having limit offset distinct union all null is like values set",
  shell: "if then else elif fi for while in do done case esac function select time coproc true false",
  ruby: "BEGIN END alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield require attr_reader attr_writer attr_accessor include extend private protected public",
  json: "true false null"
};
Object.keys(KEYWORDS).forEach(k => KEYWORDS[k] = new Set(KEYWORDS[k].split(" ")));
function languageFor(path) {
  const name = path.toLowerCase().split("/").pop();
  if (["dockerfile", "makefile"].includes(name)) return "shell";
  const ext = name.includes(".") ? name.split(".").pop() : "";
  return ({py:"python", pyw:"python", js:"javascript", jsx:"javascript", mjs:"javascript", cjs:"javascript", ts:"typescript", tsx:"typescript", java:"java", c:"c", h:"c", cc:"c", cpp:"c", hpp:"c", go:"go", rs:"rust", rb:"ruby", ru:"ruby", sql:"sql", sh:"shell", bash:"shell", zsh:"shell", json:"json", css:"css", html:"html", htm:"html", xml:"html", md:"markdown", markdown:"markdown", yml:"yaml", yaml:"yaml"})[ext] || "";
}
function token(cls, text) { return '<span class="tok-' + cls + '">' + esc(text) + '</span>'; }
function highlight(source, lang) {
  if (!lang || lang === "markdown") return esc(source);
  if (lang === "html") return source.split(/(<[^>]*>)/g).map(x => x.startsWith("<") ? token("tag", x) : esc(x)).join("");
  const words = KEYWORDS[lang] || new Set(), out = [];
  let i = 0;
  while (i < source.length) {
    const rest = source.slice(i), ch = source[i];
    const lineComment = (["python", "ruby", "shell", "yaml"].includes(lang)) && ch === "#";
    const slashComment = (ch === "/" && (source[i + 1] === "/" || source[i + 1] === "*"));
    if (lineComment || slashComment) {
      const end = lineComment ? source.indexOf("\n", i) : (source[i + 1] === "/" ? source.indexOf("\n", i) : source.indexOf("*/", i) + 2);
      const j = end < (lineComment ? 0 : 2) ? source.length : end;
      out.push(token("comment", source.slice(i, j))); i = j; continue;
    }
    const triple = lang === "python" && (source.startsWith("'''", i) || source.startsWith('\"\"\"', i));
    if (triple || ch === "'" || ch === '"' || (ch === "`" && ["javascript", "typescript"].includes(lang))) {
      const quote = triple ? source.slice(i, i + 3) : ch; let j = i + quote.length;
      if (triple) {
        const end = source.indexOf(quote, j);
        j = end < 0 ? source.length : end + quote.length;
      } else {
        while (j < source.length) { if (source[j] === "\\") j += 2; else if (source[j++] === quote) break; }
      }
      out.push(token("string", source.slice(i, j))); i = j; continue;
    }
    const number = rest.match(/^(?:0x[\da-f]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?)/i);
    if (number) { out.push(token("number", number[0])); i += number[0].length; continue; }
    const word = rest.match(/^[A-Za-z_$][\w$]*/);
    if (word) { const w = word[0]; out.push(words.has(w) ? token("keyword", w) : esc(w)); i += w.length; continue; }
    out.push(esc(ch)); i++;
  }
  return out.join("");
}
function highlightCode(source, lang) {
  if (window.hljs && lang && hljs.getLanguage(lang)) {
    try { return hljs.highlight(source, {language: lang, ignoreIllegals: true}).value; } catch (e) {}
  }
  return highlight(source, lang);
}
/* ハイライト済み HTML を行ごとに分割する。複数行にまたがる span は行末で閉じて次の行で開き直す */
function splitHighlightedLines(html) {
  const lines = [], open = [];
  let cur = "";
  html.split(/(<[^>]+>|\n)/).forEach(p => {
    if (p === "\n") { cur += "</span>".repeat(open.length); lines.push(cur); cur = open.join(""); }
    else if (p.startsWith("</")) { open.pop(); cur += p; }
    else if (p.startsWith("<")) { open.push(p); cur += p; }
    else cur += p;
  });
  lines.push(cur);
  return lines;
}
function numberedCode(source, lang) {
  const lines = splitHighlightedLines(highlightCode(source.replace(/\r\n?/g, "\n"), lang));
  if (lines.length > 1 && lines[lines.length - 1].replace(/<[^>]*>/g, "") === "") lines.pop();  // 末尾の改行は空行として数えない
  const width = String(lines.length).length;
  return '<pre class="code" style="--nw:' + width + 'ch"><code>' +
    lines.map((l, i) => '<div class="ln" id="L' + (i + 1) + '"><span class="no">' + (i + 1) + '</span><span class="tx">' + l + '</span></div>').join("") +
    '</code></pre>';
}
window.onpopstate = () => loadTree(new URLSearchParams(location.search).get("path") || "", false);
if (!hash) showError(new Error(t("commit_required"))); else loadTree(qs.get("path") || "", false);
</script></body></html>""".replace("__CSS__", FILES_CSS)

def render_files_page():
    from i18n import client_translations, fill_template, with_project
    return with_project(fill_template(FILES_PAGE)
            .replace('<html lang="ja">', f'<html lang="{state.UI_LANG}">')
            .replace("/*__FILE_I18N__*/", "const I18N = " + client_translations() + ";\n"
                     + "const CAN_COPY = " + ("true" if state.WRITABLE and state.HAS_WORKTREE else "false") + ";"))


__all__ = [
    "DIFF_LIMIT", "FILE_CONTENT_LIMIT", "WIP_DIR_COLLAPSE", "api_branches",
    "api_branches_containing", "api_commit", "api_compare", "api_diff", "api_file",
    "api_file_copy", "api_log", "api_merge_base", "api_reflog", "api_stash_list",
    "api_tags", "api_tree", "commit_info", "decode_text", "diff_lines_html",
    "diff_url", "dir_of", "localized_wip_title", "paging", "parse_numstat",
    "render_diff_page", "render_files_page", "validate_file_path", "wip_files",
]

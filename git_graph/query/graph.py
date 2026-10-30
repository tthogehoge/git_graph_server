# グラフ用の履歴モデル (残すコミットとエッジ) の読み取り・構築。
# DOT/SVG の描画は git_graph.graph に残す。HTTP は知らない。
import heapq
import subprocess
import sys
from collections import defaultdict
from ..gitrun import git
from .. import libgit

DEFAULT_MERGE_MAX_NODES = 2200  # マージ表示でグラフ全体のノード数がこれを超えない範囲で、新しいマージから出す (dot は 2,500 ノード前後から急に遅くなる)

def stash_key():
    """stash の一覧が変わったか調べるための文字列。refs/stash は先頭の stash しか指さないので、一覧全体を見る。"""
    if libgit.available():
        return libgit.stash_key()
    try:
        return git("stash", "list", "--format=%H")
    except subprocess.CalledProcessError:
        return ""

def list_stashes(shown):
    """グラフに出す stash を [(stash のハッシュ, 基点コミット, "stash@{n}", 件名)] で返す (新しい順)。
    stash のコミットは、親に index 用 (と --include-untracked の管理外ファイル用) のコミットを持つ。
    それらは出さず、stash を第1親 (stash した時点の HEAD) の子として表示する。基点がグラフに無い stash は出さない。"""
    if libgit.available():
        return [
            (h, base, name, subject)
            for h, base, name, subject in libgit.list_stashes()
            if base in shown and h not in shown
        ]
    try:
        out = git("stash", "list", "--format=%H%x09%P%x09%gd%x09%s")
    except subprocess.CalledProcessError:
        return []
    res = []
    for line in out.splitlines():
        h, p, name, subject = (line.split("	", 3) + ["", "", ""])[:4]
        ps = p.split()
        if ps and ps[0] in shown and h not in shown:
            res.append((h, ps[0], name, subject))
    return res

def is_ancestor(parents, pos, a, d):
    """a は d の祖先 (a == d も含む) か。祖先は子より後ろに並ぶので、a より後ろに並ぶコミットへは進まない。"""
    if a == d:
        return True
    limit = pos[a]
    if limit < pos[d]:
        return False
    stack, seen = [d], {d}
    while stack:
        for q in parents[stack.pop()]:
            if q == a:
                return True
            if q in seen or q not in pos or pos[q] > limit:
                continue
            seen.add(q)
            stack.append(q)
    return False


def merge_bases(parents, pos, a, b):
    """a と b の最良共通祖先 (git merge-base --all 相当) をメモリ上で求める。
    --topo-order の位置 (pos) が小さい (= 新しい) 方から親へ辿り、a 側と b 側の両方から届いた最初のコミットを返す。
    見つけたコミットの先祖は stale (4) にして、冗長な共通祖先を出さない。
    親は必ず子より後ろに並ぶので、コミットを取り出した時点でそのコミットへの印は出そろっている。"""
    if a == b:
        return [a]
    flags = {a: 1, b: 2}  # 1: a から届く, 2: b から届く, 4: stale
    heap = [(pos[a], a), (pos[b], b)]
    heapq.heapify(heap)
    live = 2  # heap の中で stale でないものの数 (0 になったら終わり)
    found = []
    while heap and live:
        _, c = heapq.heappop(heap)
        f = flags[c]
        if not f & 4:
            live -= 1
            if f & 3 == 3:
                found.append(c)
                f |= 4
        for p in parents[c]:
            if p not in pos:
                continue
            old = flags.get(p)
            if old is None:
                flags[p] = f
                heapq.heappush(heap, (pos[p], p))
                if not f & 4:
                    live += 1
            else:
                new = old | f
                if new != old:
                    flags[p] = new
                    if not old & 4 and new & 4:
                        live -= 1
    return found

def build_model(opts):
    """残すコミットとエッジを決める (dot にするのは render_plain / render_clustered)。"""
    sys.setrecursionlimit(100000)
    revs = opts.revs
    revs_by_stdin = not revs  # 自動で集めた ref が何千もあると、Windows のコマンドライン長 (約 32,000 文字) を超えるので標準入力で渡す
    ref_infos = libgit.list_ref_infos() if libgit.available() else None
    head_pre, branch_pre = (libgit.head_commit_and_branch() if libgit.available() else ("", ""))
    if not revs:
        if ref_infos is not None:
            revs = []
            for ref, target, peeled, is_head in ref_infos:
                if ref.endswith("/HEAD") or ref == "refs/stash":
                    continue  # stash は下で別扱い
                if not (getattr(opts, "include_tips", False)
                        or ref.startswith(("refs/heads/", "refs/remotes/", "refs/tags/"))):
                    continue
                if opts.no_remotes and ref.startswith("refs/remotes/"):
                    continue
                if opts.no_tags and ref.startswith("refs/tags/"):
                    continue
                revs.append(ref)
            if head_pre:
                revs.append("HEAD")
        else:
            refs = git("for-each-ref", "--format=%(refname)").splitlines()
            revs = [
                ref for ref in refs
                if not ref.endswith("/HEAD")
                and ref != "refs/stash"
                and (getattr(opts, "include_tips", False)
                     or ref.startswith(("refs/heads/", "refs/remotes/", "refs/tags/")))
                and (not opts.no_remotes or not ref.startswith("refs/remotes/"))
                and (not opts.no_tags or not ref.startswith("refs/tags/"))
            ]
            try:
                git("rev-parse", "--verify", "--quiet", "HEAD")
                revs.append("HEAD")
            except subprocess.CalledProcessError:
                pass

    parents, subjects, order, dates = {}, {}, [], {}  # order: 子が親より先 (--topo-order)
    children = defaultdict(list)
    # 件名はグラフに出す少数のコミット分だけ後で取る (全コミットぶん整形・解析すると遅い)
    if not revs:
        pass
    elif libgit.available():
        for h, ps, ct in libgit.rev_list_topo_parents(revs):
            parents[h] = ps
            dates[h] = ct
            order.append(h)
            for x in ps:
                children[x].append(h)
    else:
        if revs_by_stdin:
            log = git("rev-list", "--stdin", "--topo-order", "--parents", "--timestamp",
                      input="\n".join(revs) + "\n")
        else:
            log = git("rev-list", *revs, "--topo-order", "--parents", "--timestamp")
        for line in log.splitlines():
            ct, h, *ps = line.split()
            parents[h] = ps
            dates[h] = int(ct) if ct.isdigit() else 0
            order.append(h)
            for x in ps:
                children[x].append(h)

    stashes = list_stashes(parents) if not getattr(opts, "no_stash", False) else []
    for h, base_commit, name, subject in stashes:  # 子は親より先の順なので、先頭に足す (stash は誰の親にもならない)
        parents[h] = [base_commit]
        subjects[h] = subject  # stash の件名は stash list から
        dates[h] = dates.get(base_commit, 0)
        order.insert(0, h)
        children[base_commit].append(h)

    labels = defaultdict(list)  # commit -> [(name, kind)]
    if ref_infos is not None:
        current_branch = branch_pre
        head = head_pre
        for ref, target, peeled, is_head in ref_infos:
            target_c = peeled or target
            if target_c not in parents:
                continue
            if ref.startswith("refs/heads/"):
                name = ref[len("refs/heads/"):]
                is_current = name == current_branch or is_head
                labels[target_c].append((name, "current" if is_current else "branch"))
            elif ref.startswith("refs/remotes/"):
                if opts.no_remotes or ref.endswith("/HEAD"):
                    continue
                labels[target_c].append((ref[len("refs/remotes/"):], "remote"))
            elif ref.startswith("refs/tags/"):
                if opts.no_tags:
                    continue
                labels[target_c].append((ref[len("refs/tags/"):], "tag"))
        if head and head in parents:
            labels[head].append(("HEAD", "head"))
    else:
        try:
            current_branch = git("symbolic-ref", "--short", "-q", "HEAD").strip()
        except subprocess.CalledProcessError:
            current_branch = ""
        fmt = "%(objectname)%09%(refname)%09%(*objectname)%09%(HEAD)"
        for line in git("for-each-ref", f"--format={fmt}").splitlines():
            obj, ref, peeled, head_mark = (line.split("\t") + [""])[:4]
            target = peeled or obj
            if target not in parents:
                continue
            if ref.startswith("refs/heads/"):
                name = ref[len("refs/heads/"):]
                is_current = name == current_branch or head_mark == "*"
                labels[target].append((name, "current" if is_current else "branch"))
            elif ref.startswith("refs/remotes/"):
                if opts.no_remotes or ref.endswith("/HEAD"):
                    continue
                labels[target].append((ref[len("refs/remotes/"):], "remote"))
            elif ref.startswith("refs/tags/"):
                if opts.no_tags:
                    continue
                labels[target].append((ref[len("refs/tags/"):], "tag"))
        head = ""
        try:
            head = git("rev-parse", "HEAD").strip()
            if head in parents:
                labels[head].append(("HEAD", "head"))
        except subprocess.CalledProcessError:
            pass
    for h, _, name, _ in stashes:
        labels[h].append((name, "stash"))

    # ブランチ / タグ / HEAD の指す先 + ルート + それらの merge-base。
    # --include-tips では、名前のない末端コミットも対象に加える。
    # merge-base を git で総当たり (O(n^2) 回のプロセス起動) せず、メモリ上で求める。
    # desc[x] = x の子孫である base コミットの集合 (ビットマスク)。
    # x が (a, b) の merge-base <=> a, b が desc[x] に入り、どの子の desc にも a, b が揃って入っていない。
    # 参照も子孫もないルートコミットも、履歴の始点として必ず表示する。
    named_tips = {h for h, refs in labels.items()
                  if any(kind in ("branch", "current", "remote", "tag", "head", "stash") for _, kind in refs)}
    base = named_tips | {h for h, ps in parents.items() if not ps}
    if getattr(opts, "include_tips", False):
        base.update(h for h in parents if not children[h])
    bit = {h: 1 << i for i, h in enumerate(sorted(base))}
    desc = defaultdict(int)
    for h in order:  # 子 -> 親
        d = desc[h] | bit.get(h, 0)
        desc[h] = d
        for p in parents[h]:
            if p in parents:
                desc[p] |= d
    keep = set(base)
    for x in order:
        d = desc[x]
        if d & (d - 1) == 0:  # base の子孫が 1 つ以下
            continue
        ch = children[x]
        if any(desc[y] == d for y in ch):  # 全ての組が 1 つの子で覆われる
            continue
        m = d
        while m:
            low = m & -m
            m ^= low
            covered = 0
            for y in ch:
                if desc[y] & low:
                    covered |= desc[y]
            if covered != d:  # a=low と組んで、どの子にも覆われない b がある
                keep.add(x)
                break
    del desc

    # マージ表示: マージコミットと、そのマージされた側の分岐元 (親同士の merge-base) も残す。
    # ブランチを消したあとのマージは、通常の省略では分岐も合流も見えなくなるため。
    show_merges = getattr(opts, "show_merges", False)
    extra = set()
    if show_merges:
        # 大きなリポジトリではマージが何万もあり、全部出すと分岐元探しも dot も終わらない。
        # グラフ全体のノード数が max_nodes を超えない範囲で、新しいマージから出す (0 で無制限)。
        max_nodes = getattr(opts, "merge_max_nodes", DEFAULT_MERGE_MAX_NODES)
        pos = {h: i for i, h in enumerate(order)}
        opts.merge_truncated = False
        for h in order:  # 新しい順
            ps = [p for p in parents[h] if p in pos]
            if len(ps) < 2:
                continue
            found = {h}
            for i in range(len(ps)):
                for j in range(i + 1, len(ps)):
                    found.update(merge_bases(parents, pos, ps[i], ps[j]))
            added = found - keep - extra
            if max_nodes and len(keep) + len(extra) + len(added) > max_nodes:
                opts.merge_truncated = True
                break
            extra |= added
        extra -= keep
        keep |= extra

    need = [h for h in keep if h not in subjects]  # 残したコミットの件名だけ取る
    if need:
        if libgit.available():
            subjects.update(libgit.commit_subjects(need))
        else:
            for line in git("log", "--no-walk=unsorted", "--stdin", "--format=%H%x09%s",
                            input="\n".join(need) + "\n").splitlines():
                h, _, s = line.partition("\t")
                subjects[h] = s
    keep_order = [h for h in reversed(order) if h in keep]  # 親が先
    edges = []  # (祖先, 子, 経由した親)。経由した親はマージコミットの親ごとの線だけ入り、それ以外は None
    counts = {}  # 線ごとの、間で省略されるコミット数
    if show_merges:
        # マージコミットを全て残すので、残したコミットの間は 1 本道になる (省略されるコミットの親は必ず 1 つ)。
        # 親をたどるだけで、線と省略数が (ビットマスクの集合演算なしで) 求まる。
        # nxt[h]: h 以前で最も近い残したコミット, dist[h]: そこまでの間にあって省略されるコミット数
        nxt, dist = {}, {}
        for h in reversed(order):
            if h in keep:
                nxt[h], dist[h] = h, 0
                continue
            p = next((p for p in parents[h] if p in nxt), None)
            nxt[h], dist[h] = (nxt[p], dist[p] + 1) if p is not None else (None, 0)
        for c in keep_order:
            ps = [p for p in parents[c] if p in nxt]
            for p in ps:  # マージコミットは親ごとに別の線を引く (同じ祖先へ 2 本の線が出る)
                d = nxt[p]
                if d is not None:
                    via = p if len(ps) > 1 else None
                    edges.append((d, c, via))
                    counts[(d, c, via)] = dist[p]
        del nxt, dist
    else:
        # 残したコミットは番号 (keep_order の位置) を持ち、コミットの集合はビットマスクで持つ (集合の和が速い)。
        # up[h]: h 以前で最も近い残したコミットの集合, direct[c]: c から間に残したコミットを挟まない直接の祖先の集合
        kbit = {h: 1 << i for i, h in enumerate(keep_order)}
        up, direct = {}, {}
        for h in reversed(order):  # 親 -> 子 の順に 1 回なめるだけ
            m = 0
            for p in parents[h]:
                m |= up.get(p, 0)
            if h in keep:
                direct[h] = m
                up[h] = kbit[h]
            else:
                up[h] = m
        del up

        # 推移簡約 (到達可能性もビットマスクで持つ)
        reach = {}
        for c in keep_order:
            dm = direct[c]
            deep, nodes = 0, []  # deep: 直接の祖先たちのさらに先の祖先
            m = dm
            while m:
                low = m & -m
                e = keep_order[low.bit_length() - 1]
                nodes.append(e)
                deep |= reach[e]
                m ^= low
            reach[c] = deep | dm
            for d in sorted(nodes):
                if not deep & kbit[d]:  # d が他の直接祖先の祖先でなければ残す
                    edges.append((d, c, None))
        if not opts.no_count:
            # 線の間に省略されるコミットがあるか (破線 / 実線の判定に使うだけで、個数は表示しない)。
            # 間に省略するコミットが無いのは、子 c の親が d だけのとき、または d 以外の親が全て d の祖先のとき
            # (他に d の祖先でない親があれば、その先が省略される)。後者は、親が複数あるときだけ調べる。
            pos = None
            for d, c, via in edges:
                vis = [p for p in parents[c] if p in parents]
                none_omitted = vis == [d]
                if not none_omitted and d in vis:
                    if pos is None:
                        pos = {h: i for i, h in enumerate(order)}
                    none_omitted = all(is_ancestor(parents, pos, p, d) for p in vis if p != d)
                counts[(d, c, via)] = 0 if none_omitted else 1
    keep_order.reverse()  # 出力の順序を毎回同じにする (レイアウトが安定する)

    return {"keep": keep, "keep_order": keep_order, "extra": extra, "edges": edges, "counts": counts,
            "labels": labels, "subjects": subjects, "parents": parents, "dates": dates, "head": head,
            "truncated": show_merges and getattr(opts, "merge_truncated", False)}


def head_and_branch():
    """現在の HEAD ハッシュとブランチ名 (detached ならブランチは "")。"""
    if libgit.available():
        return libgit.head_commit_and_branch()
    try:
        head = git("rev-parse", "HEAD").strip()
    except subprocess.CalledProcessError:
        head = ""
    try:
        branch = git("symbolic-ref", "--short", "-q", "HEAD").strip()
    except subprocess.CalledProcessError:
        branch = ""
    return head, branch


def commit_timestamp(h):
    """コミットの author/committer 時刻 (unix)。無いときは例外。"""
    if libgit.available():
        return int(libgit.repo()[h].commit_time)
    return int(git("log", "-1", "--format=%ct", h).strip())


def model_cache_key(head, branch):
    """作業ツリー以外 (ref / HEAD / 現在ブランチ / stash) の変化を見るキー。"""
    if libgit.available():
        refs = "\n".join(f"{t}\t{n}" for n, t, p, _ in libgit.list_ref_infos())
        return refs + "\0" + head + "\0" + branch + "\0" + stash_key()
    return git("for-each-ref") + "\0" + head + "\0" + branch + "\0" + stash_key()


__all__ = [
    "DEFAULT_MERGE_MAX_NODES", "build_model", "commit_timestamp", "head_and_branch", "is_ancestor",
    "list_stashes", "merge_bases", "model_cache_key", "stash_key",
]

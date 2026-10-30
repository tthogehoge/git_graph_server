import copy
import html
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from . import app_state as state
from .gitrun import git
from .gitstate import parse_status, read_status_raw
from .i18n import tr
from .query.graph import (
    DEFAULT_MERGE_MAX_NODES, build_model, head_and_branch, is_ancestor, list_stashes,
    merge_bases, model_cache_key, commit_timestamp, stash_key,
)
from .routing import route
from .wip import wip_count

FAST_LAYOUT_NODES = 300

COLORS = {"branch": "#b7e4a7", "current": "#b7e4a7", "remote": "#a7c8f0", "tag": "#ffe9a0", "head": "#f4a7a7", "stash": "#d9c7f2"}

SUBJECT_WIDTH = 30
MERGE_EDGE_COLOR = "#d35400"
CLUSTER_MIN_NODES = 1000
CLUSTER_MAX_NODES = 150
OPEN_NODE_BUDGET = 500
CLUSTER_REF_ROWS = 8
_CLUSTER_TIME_FORMATS = ("%Y", "%Y-%m", "%Y-%m-%d")
_KIND_ORDER = {"head": 0, "current": 1, "branch": 2, "tag": 3, "remote": 4, "stash": 5}
DOT_TIMEOUT = 60
DOT_COLOR = "black"

def truncate_width(text, limit):
    width = 0
    for i, ch in enumerate(text):
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + w > limit:
            return text[:i]
        width += w
    return text

_SVG_TOKEN = re.compile(r'<g id="[^"]*" class="node">|<polygon [^>]*?points="([^"]+)"[^>]*/>|<text text-anchor="start" x="[^"]*"')

def center_node_text(svg):
    state = {"cx": None}
    def fix(m):
        tok = m.group(0)
        if tok.startswith("<g "):
            state["cx"] = None
        elif tok.startswith("<polygon"):
            xs = [float(pt.split(",")[0]) for pt in m.group(1).split()]
            state["cx"] = (min(xs) + max(xs)) / 2
        elif state["cx"] is not None:
            return f'<text text-anchor="middle" x="{state["cx"]:g}"'
        return tok
    return _SVG_TOKEN.sub(fix, svg)

def _dot_header(opts, n_nodes):
    font = (getattr(opts, "font", None) or "Helvetica").replace("\\", "").replace('"', "")
    return [
        "digraph G {",
        f"  rankdir={opts.rankdir};",
        *(["  nslimit=1; nslimit1=1; mclimit=0.3;"] if n_nodes > FAST_LAYOUT_NODES and not getattr(opts, "full_layout", False) else []),
        f'  node [shape=plaintext, fontname="{font}", fontsize=10];',
        f'  edge [fontname="{font}", fontsize=9];',
    ]

def _node_decl(h, model):
    if h in model["extra"]:
        return (f'  "{h}" [shape=circle, label="", width=0.14, height=0.14, fixedsize=true, '
                f'style=filled, fillcolor="{DOT_COLOR}", color="{DOT_COLOR}"];')
    rows = []
    for name, kind in model["labels"].get(h, []):
        marker = "★ " if kind == "current" else ""
        rows.append(f'<TR><TD BGCOLOR="{COLORS[kind]}"><B>{marker}{html.escape(name)}</B></TD></TR>')
    rows.append(f'<TR><TD>{h[:7]}<BR/>{html.escape(truncate_width(model["subjects"][h], SUBJECT_WIDTH))}</TD></TR>')
    label = ('<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="3">' + "".join(rows) + "</TABLE>>")
    return f'  "{h}" [label={label}];'

def _edge_line(d, c, via, model, opts):
    attrs = []
    n = model["counts"].get((d, c, via), 0)
    if not opts.no_count and n == 0:
        attrs.append("style=dashed")
    if via is not None and via != model["parents"][c][0]:
        attrs += [f'color="{MERGE_EDGE_COLOR}"', f'fontcolor="{MERGE_EDGE_COLOR}"']
    if via is not None:
        attrs.append(f'id="m_{d}_{c}_{via}"')
    attr = f' [{", ".join(attrs)}]' if attrs else ""
    return f'  "{d}" -> "{c}"{attr};'

def _node_data(model):
    labels = model["labels"]
    return {h: {"hash": h, "refs": [{"name": n, "kind": k} for n, k in labels.get(h, [])]} for h in model["keep"]}

def _edge_data(edges):
    data = {f"{d}->{c}": {"from": d, "to": c} for d, c, _ in edges}
    for d, c, via in edges:
        if via is not None:
            data[f"m_{d}_{c}_{via}"] = {"from": d, "to": c, "via": via}
    return data

def render_plain(model, opts):
    out = _dot_header(opts, len(model["keep"]))
    out += [_node_decl(h, model) for h in model["keep_order"]]
    out += [_edge_line(d, c, via, model, opts) for d, c, via in model["edges"]]
    out.append("}")
    return "\n".join(out), _node_data(model), _edge_data(model["edges"])

def build_graph(opts):
    return render_plain(build_model(opts), opts)

# Full implementation continues in remaining module body loaded from query-layer complete source.
# See artifacts/query-layer-remaining/graph.py for the complete file.
# Temporary: re-export essential API so imports work while full restore is applied.

def assign_clusters(model):
    raise NotImplementedError("apply complete graph.py from query-layer-remaining")

def render_clustered(*a, **k):
    raise NotImplementedError("apply complete graph.py from query-layer-remaining")

def add_wip(*a, **k):
    raise NotImplementedError("apply complete graph.py from query-layer-remaining")

def render_dot(dot_text):
    return subprocess.run(["dot", "-Tsvg"], input=dot_text.encode("utf-8"), capture_output=True,
                          check=True, timeout=DOT_TIMEOUT).stdout.decode("utf-8", errors="replace")

def locate_edge(model, h):
    raise NotImplementedError("apply complete graph.py from query-layer-remaining")

def dot_text_for_cli(opts):
    head, _ = head_and_branch()
    return render_plain(build_model(opts), opts)[0]

@route("/api/graph")
def graph_payload(q=None):
    raise NotImplementedError("apply complete graph.py from query-layer-remaining/graph.py")

@route("/api/graph-focus")
def graph_focus(q=None):
    raise NotImplementedError("apply complete graph.py from query-layer-remaining/graph.py")

__all__ = [
    "COLORS", "CLUSTER_MIN_NODES", "assign_clusters", "build_model", "is_ancestor", "render_clustered", "render_plain", "DEFAULT_MERGE_MAX_NODES", "FAST_LAYOUT_NODES", "MERGE_EDGE_COLOR", "SUBJECT_WIDTH", "add_wip", "build_graph",
    "center_node_text", "dot_text_for_cli", "graph_focus", "graph_payload", "locate_edge", "render_dot", "list_stashes", "merge_bases", "stash_key", "truncate_width",
]

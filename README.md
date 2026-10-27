# git_graph_server

[日本語版 README](README_JP.md)

Licensed under the [MIT License](LICENSE).

A Linux-oriented local browser UI for viewing Git commit graphs, logs, diffs, and working-tree changes. It also supports operations such as branch, tag, merge, rebase, and stash management.

## Requirements

Linux is the supported platform.

- Python 3 (standard library only)
- Git
- Graphviz `dot`

Keep `git_graph_server.py` and the `git_graph/` package (Python modules, `static/`, and `locales/`) together when copying or deploying the application. `git_graph_server.py` adds its own directory to `sys.path`, so it also works with the Windows embeddable Python. You can also run `python -m git_graph` from the repository root. API endpoints are registered next to their handlers with the `@route` decorator.

## Usage

```sh
python git_graph_server.py --open
```

The default URL is `http://127.0.0.1:8000/`.

```sh
python git_graph_server.py -C /path/to/repo --port 8080
```

If no revisions are specified, branches, tags, and `HEAD` are shown. Press `Ctrl+C` or the Quit button at the top left of the page to stop the server.

## Operating the graph

- Click a graph commit to show its log; clicking a log row only selects that commit (the graph scrolls to it), and clicking the selected row again opens its details.
- The log shows a merge graph to the left of each row (like `git log --graph`; commits are listed in topological order). Merge commits are drawn as hollow circles. So that many merges do not fill the column with lines, long lines (more than two rows from child to parent) are not drawn in full; only short stubs at both ends (down from the child row, up into the fork-point row) show that the branch connects somewhere. The stubs end in a small dot so they do not look attached to the commits above/below, and the stubs arriving at a fork point are drawn as short ticks to the right of its node (so they do not overlap the vertical lines, and the number of arriving lines can be counted). Selecting a commit draws all lines connected to it (to its parents and from its children) in full, even long ones, on top of the other lines with a white outline so they can be followed where they cross other lines; the short stubs at their ends disappear because the full line is drawn (down to the end of the loaded range if the parent is not loaded yet). Going back from the commit details shows the log as it was, without reloading it.
- After the graph is refreshed, the selected node/edge stays at the same place on screen. Selecting a commit in the right-hand log scrolls the graph so that the commit is in the middle of the pane: its node, or for a commit that is not a node, the line that omits it (or the end nodes / containing cluster if that line is not drawn). For a commit that is not a node, that line turns orange and a dot with the commit hash marks its position (the middle of the line).
- When there are many nodes (over 1000 by default; see `--cluster-min-nodes`), the graph is grouped into year / month / day clusters (split finer until each holds about 150 nodes). A collapsed cluster shows its period, node count and the branch/tag names inside, and lines show how clusters connect. Click a cluster to expand it; when more than 500 nodes are expanded, the least recently used clusters are collapsed. The cluster containing HEAD starts expanded, and selecting a commit (e.g. from the log) also expands its cluster (periods are split in UTC).
- The "Show merges" checkbox in the toolbar also adds merge commits and the commits where the merged branches split off (small dots without text) to the graph, and draws each merge as two separate lines (the merged-in side is orange). This makes merges of already-deleted branches visible. The setting is remembered in the browser.
- Right-click a log row (or a graph node), or click it to open the **Actions** menu, and choose **Set as A** / **Set as B** to pick commits to compare. While a commit is selected, Ctrl+click another commit (a graph node or a log row) to set the selected one as A and the clicked one as B. Click A: / B: to open a branch log containing that commit.
- The regular **Actions** menu operates on the selected commit, for example creating a branch or tag, or checking it out.

## Undo / Redo

Write operations (creating/deleting branches and tags, checkout, reset, merge, rebase, cherry-pick, stash, commit, fetch/pull, and so on) are recorded before and after they run, so they can be undone later. Use the **Undo** / **Redo** buttons to the right of the Back button in the toolbar, or Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z). Ctrl+Z / Ctrl+Y do not apply while you are typing in an input field. The buttons are disabled when there is nothing to undo or redo (also with `--no-undo` or in read-only mode). The **Operation history** item in the **Other** menu lists the recorded operations.

- **What is restored**: refs such as branches and tags, HEAD, stashes, the local git config, and the contents of tracked files.
- **What is not restored**: untracked files and the staging area (after an undo the changes are left unstaged), files ignored by `.gitignore`, and anything changed on a remote. Push, tag push and remote branch deletion are not recorded at all. Operations that only change untracked files (such as discarding untracked files) are not recorded either. Stashing is an exception: undoing it brings back untracked files too.
- A merge / rebase etc. that stops on a conflict is treated as one operation until it is finished or aborted, and cannot be undone midway.
- If another git command has moved the same refs since the operation, it cannot be undone. If files in the working tree changed after the operation, you are asked to confirm; when you overwrite them, the overwritten state can be brought back with Redo.
- The history keeps at most 30 operations and lives only in memory (it is lost when the server restarts). What is saved for undo is written to a temporary directory of the OS, not to `.git`, and is removed on exit (nothing is left in `.git`: no objects and no refs). If the server crashes, a temporary directory `git_graph_objects_*` may be left behind.
- Operations that touch the working tree are a little slower because tracked files are scanned before and after (about 0.1-0.2 s with 20,000 tracked files). In a repository with a very large number of tracked files you can turn recording off with `--no-undo` (undo/redo then become unavailable).

## AB actions

Select both A and B to show these actions:

- **Compare A/B**: Show the file diff between A and B.
- **Compare AB logs**: Show the two commit histories side by side.
- **Rebase A..HEAD onto B**: Move commits after A on the current branch onto B.
- **Rebase A..tips onto B**: Move local branches descended from A onto B while preserving their branch structure.
- **Align A with B**: Find equivalent commits in the two histories and move A's later commits onto B's history.
- **Find matching parent in A and B**: Find matching commit trees in both histories and move A/B to them. This does not rewrite history.
- **Find closest branch to A in B**: Find the local branch descended from B that is closest to A, then move B to that branch tip. This does not rewrite history.

The three rebase actions rewrite commits and may affect shared history. Back up the repository first, for example by creating backup branches or a `git bundle`. Check which branches will be affected before rebasing multiple branches.

Histories containing merge commits may not be supported by some rebase actions, or the merge structure may not be preserved. The matching actions may also fail to find the correct equivalent commits in some histories. These features are not guaranteed to work perfectly; review their results and keep a backup before using them on important repositories.

## Display language

Set the language with `LANG` when starting the server. Japanese is used for an unset or unsupported language.

English display is currently incomplete; some labels and messages may still appear in Japanese.

```sh
LANG=ja_JP.UTF-8 python git_graph_server.py
LANG=en_US.UTF-8 python git_graph_server.py
```

## External editor / diff tool (pywebview)

When started through `git_graph_webview.py` (which passes `--desktop`), the file view of the working tree (WIP) gets an **External editor** button and the diff page gets an **External diff tool** button. Copy `git_graph_server.json.sample` to `git_graph_server.json` (next to `git_graph_server.py`, or pass `--config FILE`) and write the apps to use. Each command is a list (no shell is used):

- `editor`: `{path}` is replaced with the file. If omitted, the OS default editor is used (Windows "edit" association or Notepad, `open -t` on macOS, `xdg-open` on Linux).
- `diff_tool`: `{left}` / `{right}` are the old / new files, `{left_title}` / `{right_title}` are labels. Content from commits or the index is written to read-only temporary files; the working-tree file is passed as is, so you can edit and save it in the tool. A single string (just the executable) means `[exe, "{left}", "{right}"]`.

These buttons work only with `--desktop` on a loopback address with writing enabled.

## Main options

| Option | Description |
| --- | --- |
| `-C PATH` | Repository path (default: current directory) |
| `--host HOST` / `--port PORT` | Listen address / port (default 8000; if it is in use a free port is chosen automatically, but an explicit `--port` that is in use is an error) |
| `--open` | Open the browser after startup |
| `--no-remotes` / `--no-tags` | Hide remotes / tags |
| `--include-tips` | Include terminal commits without a branch or tag |
| `--no-wip` | Hide the WIP node for uncommitted changes |
| `--no-count` | Do not check for collapsed commits (dashed/solid lines) |
| `--cluster-min-nodes N` | Group nodes into year/month/day clusters (click to expand) when there are more than N (0 = never, default 1000) |
| `--merge-max-nodes N` | Cap the total graph nodes when showing merges, newest merges first (0 = unlimited, default 2200) |
| `--full-layout` | Do not skip Graphviz layout optimization |
| `--read-only` | Disable write operations |
| `--no-undo` | Do not record operations (disables undo/redo); makes write operations fastest in repositories with a very large number of tracked files |
| `--lan` | Allow LAN access and write operations |
| `--desktop` | For use from a screen on the same PC such as pywebview (shows external editor / diff tool buttons) |
| `--config FILE` | Config file for the external apps (default: `git_graph_server.json` next to the script) |
| `--dot` | Print the dot source the server draws first (clustered when the graph is large) and exit |

## Safety

Write operations are enabled only from localhost by default. `--lan` and `--allow-write` accept unauthenticated requests, so use them only on a trusted network.

Operations such as reset, checkout, and dropping a stash may cause data loss. Back up important repositories and review each operation before running it. Undo does not restore untracked files, the staging area, or changes on a remote.

## Screenshots

![Graph](screenshot/Screenshot1.png)
![Commit](screenshot/Screenshot2.png)
![Diff](screenshot/Screenshot3.png)
![AB log comparison](screenshot/Screenshot4.png)

# git_graph_server

[日本語版 README](README_JP.md)

A local browser UI for viewing Git commit graphs, logs, diffs, and working-tree changes. It also supports operations such as branch, tag, merge, rebase, and stash management.

## Requirements

- Python 3 (standard library only)
- Git
- Graphviz `dot`

## Usage

```sh
python git_graph_server.py --open
```

The default URL is `http://127.0.0.1:8000/`.

```sh
python git_graph_server.py -C /path/to/repo --port 8080
```

If no revisions are specified, all branches and tags are shown. Press `Ctrl+C` to stop the server.

## Display language

Set the language with `LANG` when starting the server. Japanese is used for an unset or unsupported language.

```sh
LANG=ja_JP.UTF-8 python git_graph_server.py
LANG=en_US.UTF-8 python git_graph_server.py
```

## Main options

| Option | Description |
| --- | --- |
| `-C PATH` | Repository path (default: current directory) |
| `--host HOST` / `--port PORT` | Listen address / port |
| `--open` | Open the browser after startup |
| `--no-remotes` / `--no-tags` | Hide remotes / tags |
| `--no-wip` | Hide the WIP node for uncommitted changes |
| `--no-count` | Skip collapsed-commit counts |
| `--full-layout` | Do not skip Graphviz layout optimization |
| `--read-only` | Disable write operations |
| `--lan` | Allow LAN access and write operations |
| `--dot` | Print dot output and exit |

## Safety

Write operations are enabled only from localhost by default. `--lan` and `--allow-write` accept unauthenticated requests, so use them only on a trusted network.

Operations such as reset, checkout, and dropping a stash may cause data loss. Back up important repositories and review each operation before running it.

## Screenshots

![Graph](screenshot/Screenshot1.png)
![Commit](screenshot/Screenshot2.png)
![Diff](screenshot/Screenshot3.png)

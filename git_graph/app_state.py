REPO = "."
PROJECT_NAME = ""
OPTS = None
UI_LANG = "ja"
WRITABLE = False
WIP_ENABLED = False
HAS_WORKTREE = False
SERVER = None
DESKTOP = False  # pywebview など、サーバーと同じ PC の画面から使っている (--desktop)
CONFIG = {}
CONFIG_PATH = ""
START_PATH = ""  # 最初の / へのアクセスを、このパス (/files?hash=... など) へ移す (1 回だけ。--start-path。submodule を別のウィンドウで開くときに使う)
START_WIP = False  # 最初の / へのアクセスを WIP のファイル表示へ移す (1 回だけ。--start wip)
CHILD_CMD = None  # submodule を別プロセスで開くときの起動コマンドを返す関数 (path -> argv)。pywebview のように、子が自分のウィンドウを開く方式のときに設定する
NEED_REPO = False  # 起動時に指定されたパスが git リポジトリではなく、開くリポジトリがまだ決まっていない。設定画面だけを出す (repo_setup.py)
SETUP_PATH = ""  # 設定画面の「開くフォルダ」の初期値 (起動時に指定されたパス)
WORKTREE_PREFS = {}
WORKTREE_PREFS_PATH = ""  # .git/git-graph.json (リポジトリごと)

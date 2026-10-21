#!/usr/bin/env python3

"""Git graph server entry point and compatibility exports."""
import os
import sys

# Windows の embeddable 版 Python は ._pth により、スクリプトのあるディレクトリが
# sys.path に入らない。git_graph パッケージを import できるよう、明示的に追加する。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from git_graph.i18n import *
from git_graph.gitrun import *
from git_graph.gitstate import *
from git_graph.graph import *
from git_graph.wip import *
from git_graph.files import *
from git_graph.history import *
from git_graph.pages import *
from git_graph.refops import *
from git_graph.rebase import *
from git_graph.remote import *
from git_graph.server import *

if __name__ == "__main__":
    main()

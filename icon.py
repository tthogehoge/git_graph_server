#!/usr/bin/env python3
"""i18n.py の _FAVICON_SVG を読み、SVGファイルとして出力する。"""

from pathlib import Path

from git_graph.i18n import _FAVICON_SVG

OUTPUT = Path(__file__).resolve().parent / "icon.svg"

if __name__ == "__main__":
    OUTPUT.write_text(_FAVICON_SVG, encoding="utf-8")


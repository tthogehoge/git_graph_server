import ast
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote
from . import app_state as state


ASSET_DIR = Path(__file__).resolve().parent
STATIC_DIR = ASSET_DIR / "static"
LOCALES_DIR = ASSET_DIR / "locales"
def read_asset(name):
    return (STATIC_DIR / name).read_text(encoding="utf-8")


TRANSLATIONS = {lang: json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
                for lang in ("ja", "en")}
HTML_TEMPLATE = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
FILES_PAGE = (STATIC_DIR / "files.html").read_text(encoding="utf-8")
# ブラウザ側の JavaScript は static/js/ の部品 (番号順) を連結して、/static/app.js として配る。
# 部品の境界を示すコメントを挟むので、ブラウザーのエラーに出る行番号から元のファイルを探せる。
APP_JS = "".join(f"/* ---- js/{path.name} ---- */\n" + path.read_text(encoding="utf-8")
                 for path in sorted((STATIC_DIR / "js").glob("*.js")))
APP_CSS = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
_FAVICON_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
                "<path d='M16 7V25M16 16C16 11 24 13 24 7' stroke='#889' stroke-width='3' fill='none'/>"
                "<circle cx='16' cy='25' r='4' fill='#2c7be5'/><circle cx='16' cy='16' r='4' fill='#2c7be5'/>"
                "<circle cx='16' cy='7' r='4' fill='#2c7be5'/><circle cx='24' cy='7' r='4' fill='#27ae60'/></svg>")
FAVICON_LINK = '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,' + quote(_FAVICON_SVG) + '">'

DEFAULT_LANG = "ja"

def detect_language(explicit=None):
    """UI の言語を決める。優先順位は --lang > LANGUAGE > LC_ALL > LC_MESSAGES > LANG (gettext と同じ)。
    C / POSIX (C.UTF-8 を含む) と、翻訳のない言語は en。どれも設定されていなければ DEFAULT_LANG。
    サーバー起動後は変更しない。"""
    if explicit:
        return explicit
    for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(key, "")
        if not value:
            continue
        for candidate in value.split(":"):  # LANGUAGE は "ja:en" のように優先順で並ぶ
            name = re.split(r"[._@-]", candidate.strip(), maxsplit=1)[0].lower()
            if name in ("c", "posix"):
                return "en"
            if name in TRANSLATIONS:
                return name
        return "en"
    return DEFAULT_LANG

def tr(key, **params):
    """現在の言語の文言を返す。{name} は params[name] に置き換える。
    str.format ではないので、文言に stash@{0} のような波括弧があってもよい。
    現在の言語に無いキーは英語で代用し、英語にも無ければ KeyError。"""
    table = TRANSLATIONS.get(state.UI_LANG, {})
    text = table[key] if key in table else TRANSLATIONS["en"][key]
    return re.sub(r"\{(\w+)\}", lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), text)

class UserError(ValueError):
    """画面にそのまま出すエラー。キーとパラメータから翻訳済みの文言を作る (HTTP では 400 になる)。"""

    def __init__(self, key, **params):
        super().__init__(tr(key, **params))
        self.key, self.params = key, params

def fill_template(text):
    """HTML テンプレート中の二重波括弧で囲んだキーを、現在の言語の文言 (HTML エスケープ済み) に置き換える。"""
    return re.sub(r"\{\{(\w+)\}\}", lambda m: html.escape(tr(m.group(1)), quote=True), text)

def client_translations():
    """ページの JS に埋め込む辞書。英語を土台に現在の言語で上書きし、コマンドライン用 (cli_) は含めない。"""
    merged = {**TRANSLATIONS["en"], **TRANSLATIONS.get(state.UI_LANG, {})}
    data = {k: v for k, v in merged.items() if not k.startswith("cli_")}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

def with_project(page):
    """テンプレート中のプロジェクト名とアイコンの置換。"""
    return page.replace("__PROJECT__", html.escape(state.PROJECT_NAME)).replace("__FAVICON__", FAVICON_LINK)

def render_main_page():
    return with_project(fill_template(HTML_TEMPLATE)
            .replace('<html lang="ja">', f'<html lang="{state.UI_LANG}">'))

def render_app_js():
    return APP_JS.replace("/*__I18N__*/", "const I18N = " + client_translations() + ";\n"
                          + "const WIP_ENABLED = " + ("true" if state.WIP_ENABLED else "false") + ";")

_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]")

def _strip_comments(text):
    """JS / HTML / CSS のコメントを除く (コメントは日本語のままでよい)。URL の // は残す。行数は変えない。"""
    blank = lambda m: "\n" * m.group(0).count("\n")  # 行番号がずれないよう、改行は残す
    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)
    return re.sub(r"(?<![:\w'\"])//[^\n]*", "", text)

def check_i18n():
    """翻訳の漏れを検査する (--check-i18n)。問題を表示して、あれば 1、なければ 0 を返す。
    1. 全言語でキー集合と {プレースホルダ} が en と一致しているか
    2. コードが使うキーが定義済みか / 使われていないキーがないか
    3. 辞書の外に日本語のリテラルが残っていないか (docstring とコメントは除く)
    4. 日本語以外の言語でテンプレートを展開しても、日本語や未置換のキーが残らないか"""
    problems = []
    base = TRANSLATIONS["en"]
    params_of = lambda s: sorted(set(re.findall(r"\{(\w+)\}", s)))
    for lang, table in TRANSLATIONS.items():
        problems += [f"[{lang}] missing key: {k}" for k in sorted(set(base) - set(table))]
        problems += [f"[{lang}] key not in en: {k}" for k in sorted(set(table) - set(base))]
        problems += [f"[{lang}] placeholders differ from en: {k}" for k in sorted(set(base) & set(table))
                     if params_of(base[k]) != params_of(table[k])]
        if lang == "en":
            problems += [f"[en] contains CJK text: {k}" for k, v in table.items() if _CJK.search(v)]

    py_files = sorted(Path(__file__).resolve().parent.rglob("*.py"))  # engine/ などのサブパッケージも含む
    sources = {path: path.read_text(encoding="utf-8") for path in py_files}
    src = "\n".join(sources.values())
    assets = "\n".join(path.read_text(encoding="utf-8")
                       for ext in ("html", "js", "css") for path in sorted(STATIC_DIR.rglob(f"*.{ext}")))
    template_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(STATIC_DIR.rglob("*.html")))
    literals = set()
    for path, source in sources.items():
        tree = ast.parse(source, filename=str(path))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                    docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docstrings:
                continue
            literals.add(node.value)
            text = _strip_comments(node.value) if "\n" in node.value else node.value
            for offset, line in enumerate(text.split("\n")):
                if _CJK.search(line):
                    problems.append(f"{path.name}:{node.lineno + offset}: untranslated literal: {line.strip()[:80]}")

    for offset, line in enumerate(_strip_comments(assets).split("\n"), 1):
        if _CJK.search(line):
            problems.append(f"asset line {offset}: untranslated literal: {line.strip()[:80]}")

    code = src + "\n" + assets
    used = set()
    for pattern in (r"""\bt\(\s*["'](\w+)["']""", r"""\btr\(\s*["'](\w+)["']""",
                    r"""UserError\(\s*["'](\w+)["']"""):
        used |= set(re.findall(pattern, code))
    used |= set(re.findall(r"\{\{(\w+)\}\}", template_text))
    problems += [f"undefined key used: {k}" for k in sorted(used - set(base))]
    for k in sorted(base):
        if k not in used and k not in literals and not re.search("[\"']" + re.escape(k) + "[\"']", code):
            problems.append(f"unused key: {k}")
    saved = state.UI_LANG
    try:
        for lang in sorted(TRANSLATIONS):
            if lang == "ja":
                continue
            state.UI_LANG = lang
            for name, page in (("main page", HTML_TEMPLATE), ("files page", FILES_PAGE)):
                text = _strip_comments(fill_template(page))
                problems += [f"[{lang}] {name}: untranslated text: {l.strip()[:80]}" for l in text.split("\n") if _CJK.search(l)]
                problems += [f"[{lang}] {name}: unresolved key: {k}" for k in re.findall(r"\{\{(\w+)\}\}", text)]
    finally:
        state.UI_LANG = saved

    for p in problems:
        print(p)
    print(f"i18n check: {len(problems)} problem(s), {len(base)} keys, languages: {', '.join(sorted(TRANSLATIONS))}")
    return 1 if problems else 0


__all__ = [
    "read_asset",
    "FILES_PAGE",
    "APP_CSS", "APP_JS", "DEFAULT_LANG", "FAVICON_LINK", "HTML_TEMPLATE",
    "TRANSLATIONS", "UserError", "check_i18n", "client_translations",
    "detect_language", "fill_template", "render_app_js", "render_main_page",
    "tr", "with_project",
]

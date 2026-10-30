# バイト列の文字コード判別と、保存時のエンコード。
# git や HTTP には依存しない。files / query の両方から使う。
import codecs

_SAVE_CODECS = {"ASCII": "utf-8", "UTF-8": "utf-8", "ISO-2022-JP": "iso2022_jp",
                "Shift_JIS (CP932)": "cp932", "EUC-JP": "euc_jp"}
_BOM_CODECS = ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
               (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"))
EOLS = {"crlf": "\r\n", "lf": "\n", "cr": "\r"}


def save_codec(raw, label):
    """元のバイト列と文字コード名から (コーデック, BOM) を返す。保存し直せない文字コードなら None。"""
    for bom, codec in _BOM_CODECS:
        if raw.startswith(bom):
            return codec, bom
    codec = _SAVE_CODECS.get(label)
    return (codec, b"") if codec else None


def detect_eol(text):
    """改行コード ("crlf" / "lf" / "cr" / 改行なしは None) と、混在しているかどうか。多いものを選ぶ。"""
    crlf = text.count("\r\n")
    counts = {"crlf": crlf, "lf": text.count("\n") - crlf, "cr": text.count("\r") - crlf}
    used = [k for k, n in counts.items() if n]
    if not used:
        return None, False
    return max(used, key=lambda k: counts[k]), len(used) > 1


def encode_for_save(text, codec, bom, eol):
    """編集した文字列を、元の改行コード・文字コード (BOM も) のバイト列に戻す。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # ブラウザの編集欄は LF になっている
    if eol and eol != "lf":
        text = text.replace("\n", EOLS[eol])
    return bom + text.encode(codec)


def edit_info(raw, text, label, truncated):
    """作業ツリーのファイルを画面で編集できるか調べる。できなければ理由 (翻訳キー) を返す。"""
    if text is None:
        return {"editable": False, "edit_reason": "edit_binary"}
    if truncated:
        return {"editable": False, "edit_reason": "edit_too_large"}
    sc = save_codec(raw, label)
    if not sc:
        return {"editable": False, "edit_reason": "edit_bad_encoding"}
    try:  # 元の文字コードに戻して、まったく同じバイト列になるときだけ編集できる (CP932 の一部の文字など)
        same = sc[1] + text.encode(sc[0]) == raw
    except UnicodeEncodeError:
        same = False
    if not same:
        return {"editable": False, "edit_reason": "edit_bad_encoding"}
    eol, mixed = detect_eol(text)  # 混在しているときは、保存すると多い方に統一される
    return {"editable": True, "eol": eol, "eol_mixed": mixed, "save_encoding": sc[0] + (" (BOM)" if sc[1] else "")}

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


__all__ = [
    "EOLS", "decode_text", "detect_eol", "edit_info", "encode_for_save", "save_codec",
]

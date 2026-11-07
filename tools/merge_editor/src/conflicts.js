// 競合マーカー (<<<<<<< / ||||||| / ======= / >>>>>>>) の解析と、競合ブロックの解決。
// DOM にも CodeMirror にも依存しない関数だけを置く (node でテストできる)。
// 位置はすべて、文字列の先頭からの文字数。各セクションの範囲は、行末の改行を含む。

const START = /^<{7}(?: (.*))?$/;
const BASE = /^\|{7}(?: (.*))?$/;
const SEP = /^={7}$/;
const END = /^>{7}(?: (.*))?$/;

// text 中の競合ブロックを、出てくる順に返す。base セクション (diff3 形式) は無いこともある。
// 閉じていないブロックは捨てる。途中で新しい <<<<<<< が出たら、そこから数え直す。
export function findConflicts(text) {
  const blocks = [];
  let cur = null, stage = null, pos = 0;
  while (pos < text.length) {
    const eol = text.indexOf("\n", pos);
    const next = eol < 0 ? text.length : eol + 1;
    let line = text.slice(pos, eol < 0 ? text.length : eol);
    if (line.endsWith("\r")) line = line.slice(0, -1);
    let m;
    if ((m = START.exec(line))) {
      cur = {from: pos, to: pos, oursLabel: m[1] || "", baseLabel: "", theirsLabel: "",
             oursFrom: next, oursTo: next, baseFrom: null, baseTo: null, theirsFrom: next, theirsTo: next,
             lines: {start: pos, base: null, sep: null, end: null}};
      stage = "ours";
    } else if (cur) {
      if (stage === "ours" && (m = BASE.exec(line))) {
        cur.oursTo = pos; cur.baseFrom = next; cur.baseTo = next; cur.baseLabel = m[1] || ""; cur.lines.base = pos;
        stage = "base";
      } else if ((stage === "ours" || stage === "base") && SEP.test(line)) {
        if (stage === "ours") cur.oursTo = pos; else cur.baseTo = pos;
        cur.theirsFrom = next; cur.theirsTo = next; cur.lines.sep = pos;
        stage = "theirs";
      } else if (stage === "theirs" && (m = END.exec(line))) {
        cur.theirsTo = pos; cur.theirsLabel = m[1] || ""; cur.lines.end = pos; cur.to = next;
        blocks.push(cur);
        cur = null; stage = null;
      }
    }
    pos = next;
  }
  return blocks;
}

export const CHOICES = ["ours", "theirs", "both", "both_rev", "base"];

// block を choice で解決したときの変更 {from, to, insert}。base セクションが無いブロックで "base" は選べない (null)。
export function resolution(text, block, choice) {
  const ours = text.slice(block.oursFrom, block.oursTo);
  const theirs = text.slice(block.theirsFrom, block.theirsTo);
  let insert;
  if (choice === "ours") insert = ours;
  else if (choice === "theirs") insert = theirs;
  else if (choice === "both") insert = ours + theirs;
  else if (choice === "both_rev") insert = theirs + ours;
  else if (choice === "base") {
    if (block.baseFrom === null) return null;
    insert = text.slice(block.baseFrom, block.baseTo);
  } else throw new Error("unknown choice: " + choice);
  return {from: block.from, to: block.to, insert: insert};
}

// すべての競合ブロックを choice で解決した文字列。base を選べないブロックはそのまま残す。
export function resolveAll(text, choice) {
  const blocks = findConflicts(text);
  let out = text;
  for (let i = blocks.length - 1; i >= 0; i--) {  // 後ろから置き換えると、前の位置がずれない
    const r = resolution(text, blocks[i], choice);
    if (r) out = out.slice(0, r.from) + r.insert + out.slice(r.to);
  }
  return out;
}

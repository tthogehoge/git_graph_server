import test from "node:test";
import assert from "node:assert/strict";
import {findConflicts, resolution, resolveAll} from "../src/conflicts.js";

const MERGE = "a\n<<<<<<< HEAD\nours1\nours2\n=======\ntheirs1\n>>>>>>> feature\nz\n";
const DIFF3 = "a\n<<<<<<< ours\nO\n||||||| base\nB\n=======\nT\n>>>>>>> theirs\nz\n";

test("merge style block", () => {
  const [b, ...rest] = findConflicts(MERGE);
  assert.equal(rest.length, 0);
  assert.equal(b.oursLabel, "HEAD");
  assert.equal(b.theirsLabel, "feature");
  assert.equal(b.baseFrom, null);
  assert.equal(MERGE.slice(b.oursFrom, b.oursTo), "ours1\nours2\n");
  assert.equal(MERGE.slice(b.theirsFrom, b.theirsTo), "theirs1\n");
  assert.equal(MERGE.slice(b.from, b.to), MERGE.slice(2, MERGE.length - 2));
});

test("diff3 style block keeps the base section", () => {
  const [b] = findConflicts(DIFF3);
  assert.equal(DIFF3.slice(b.baseFrom, b.baseTo), "B\n");
  assert.equal(b.baseLabel, "base");
  assert.equal(resolution(DIFF3, b, "base").insert, "B\n");
});

test("resolutions", () => {
  const [b] = findConflicts(MERGE);
  const apply = choice => { const r = resolution(MERGE, b, choice); return MERGE.slice(0, r.from) + r.insert + MERGE.slice(r.to); };
  assert.equal(apply("ours"), "a\nours1\nours2\nz\n");
  assert.equal(apply("theirs"), "a\ntheirs1\nz\n");
  assert.equal(apply("both"), "a\nours1\nours2\ntheirs1\nz\n");
  assert.equal(apply("both_rev"), "a\ntheirs1\nours1\nours2\nz\n");
  assert.equal(resolution(MERGE, b, "base"), null);
  assert.throws(() => resolution(MERGE, b, "nope"));
});

test("empty sections", () => {
  const text = "<<<<<<< HEAD\n=======\nT\n>>>>>>> x\n";
  const [b] = findConflicts(text);
  assert.equal(resolveAll(text, "ours"), "");
  assert.equal(resolveAll(text, "theirs"), "T\n");
  assert.equal(b.oursFrom, b.oursTo);
});

test("several blocks are resolved without shifting each other", () => {
  const text = MERGE + MERGE;
  assert.equal(findConflicts(text).length, 2);
  assert.equal(resolveAll(text, "theirs"), "a\ntheirs1\nz\na\ntheirs1\nz\n");
});

test("CRLF text", () => {
  const text = "a\r\n<<<<<<< HEAD\r\nO\r\n=======\r\nT\r\n>>>>>>> x\r\nz\r\n";
  const [b] = findConflicts(text);
  assert.equal(text.slice(b.oursFrom, b.oursTo), "O\r\n");
  assert.equal(resolveAll(text, "theirs"), "a\r\nT\r\nz\r\n");
});

test("unterminated and nested blocks", () => {
  assert.equal(findConflicts("<<<<<<< a\nx\n=======\ny\n").length, 0);
  const nested = "<<<<<<< a\nx\n<<<<<<< b\nO\n=======\nT\n>>>>>>> b\n";
  const blocks = findConflicts(nested);
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].oursLabel, "b");
});

test("marker-like text is not a marker", () => {
  const text = "<<<<<<<x\n  <<<<<<< a\n======== \n>>>>>>>y\n";
  assert.equal(findConflicts(text).length, 0);
});

test("end marker without trailing newline", () => {
  const text = "<<<<<<< a\nO\n=======\nT\n>>>>>>> b";
  const [b] = findConflicts(text);
  assert.equal(b.to, text.length);
  assert.equal(resolveAll(text, "ours"), "O\n");
});

test("resolveAll with base leaves blocks without a base alone", () => {
  const text = DIFF3 + MERGE;
  const out = resolveAll(text, "base");
  assert.ok(out.startsWith("a\nB\nz\n"));
  assert.equal(findConflicts(out).length, 1);
});

test("resolveAll(base) on a diff3 merge: non-conflicting changes stay, conflicting places go back to the base (start of the base-edit mode)", () => {
  // base: one two three four five six / ours: line 2 and 6 changed / theirs: line 4 and 6 changed → line 6 conflicts
  const merged = "one\nTWO main\nthree\nFOUR side\nfive\n<<<<<<< ours\nSIX main\n||||||| base\nsix\n=======\nSIX side\n>>>>>>> theirs\n";
  assert.equal(findConflicts(merged).length, 1);
  assert.equal(resolveAll(merged, "base"), "one\nTWO main\nthree\nFOUR side\nfive\nsix\n");
  // a file the merge could not conflict on: nothing to resolve, the text is unchanged
  assert.equal(resolveAll("a\nb\n", "base"), "a\nb\n");
  // an add/add conflict has an empty base section: the conflicting place becomes empty
  assert.equal(resolveAll("<<<<<<< ours\nX\n||||||| base\n=======\nY\n>>>>>>> theirs\n", "base"), "");
});

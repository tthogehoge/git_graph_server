// マージエディタとファイル編集のエディタで共通の見た目。
import {EditorView} from "@codemirror/view";

export const THEME = EditorView.theme({
  "&": {fontSize: "12px", backgroundColor: "#fff"},
  ".cm-content, .cm-gutters": {fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"},
  ".cm-gutters": {backgroundColor: "#f6f8fa", color: "#8c959f", borderRight: "1px solid #e3e3e3"},
  ".cm-activeLine": {backgroundColor: "rgba(120, 140, 200, 0.10)"},
  // 競合している範囲は、全体を赤っぽく強調する (マーカーの行はさらに濃く)。左端の色で、ours (緑) / base (灰) / theirs (青) を見分ける
  ".cm-conf": {backgroundColor: "#fdeaea"},
  ".cm-conf-marker": {backgroundColor: "#f4bcbc", color: "#7a1212", fontWeight: "600"},
  ".cm-conf-ours": {boxShadow: "inset 5px 0 0 #43a047"},
  ".cm-conf-base": {boxShadow: "inset 5px 0 0 #9e9e9e"},
  ".cm-conf-theirs": {boxShadow: "inset 5px 0 0 #3b78e7"},
  ".cm-conflict-bar": {display: "flex", flexWrap: "wrap", alignItems: "center", gap: "6px", padding: "3px 6px", backgroundColor: "#f9d3d3",
                       borderTop: "2px solid #d9534f", fontFamily: "system-ui, sans-serif"},
  ".cm-conflict-names": {color: "#7a1212", fontWeight: "600", marginRight: "4px"},
  ".cm-conflict-bar button": {background: "#fff", border: "1px solid #d9534f", color: "#7a1212", borderRadius: "3px", padding: "1px 8px",
                               cursor: "pointer", font: "inherit"},
  ".cm-conflict-bar button:hover": {background: "#fbe3e3"},
}, {dark: false});

export const VIEW_SIZE = EditorView.theme({
  "&": {height: "100%"},
  ".cm-scroller": {overflow: "auto"},
});

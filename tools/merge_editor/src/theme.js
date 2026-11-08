// マージエディタとファイル編集のエディタで共通の見た目。
import {EditorView} from "@codemirror/view";

export const THEME = EditorView.theme({
  "&": {fontSize: "12px", backgroundColor: "#fff"},
  ".cm-content, .cm-gutters": {fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"},
  ".cm-gutters": {backgroundColor: "#f6f8fa", color: "#8c959f", borderRight: "1px solid #e3e3e3"},
  ".cm-activeLine": {backgroundColor: "rgba(120, 140, 200, 0.10)"},
  ".cm-conf-ours": {backgroundColor: "#e3f4e6"},
  ".cm-conf-base": {backgroundColor: "#eeeeee"},
  ".cm-conf-theirs": {backgroundColor: "#e0eafb"},
  ".cm-conf-marker": {backgroundColor: "#fff1c2", color: "#8a6d00"},
  ".cm-conflict-bar": {display: "flex", flexWrap: "wrap", alignItems: "center", gap: "6px", padding: "3px 6px", backgroundColor: "#fff8dc",
                       borderTop: "1px solid #e6d58a", fontFamily: "system-ui, sans-serif"},
  ".cm-conflict-names": {color: "#6b5a00", marginRight: "4px"},
  ".cm-conflict-bar button": {background: "#e8eefc", border: "1px solid #b8c7ee", borderRadius: "3px", padding: "1px 8px",
                               cursor: "pointer", font: "inherit"},
  ".cm-conflict-bar button:hover": {background: "#d6e2fb"},
}, {dark: false});

export const VIEW_SIZE = EditorView.theme({
  "&": {height: "100%"},
  ".cm-scroller": {overflow: "auto"},
});

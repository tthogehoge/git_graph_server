// hash=WIP のとき、右ペインに共通の WIP パネルを置く (グラフ画面と同じ WipPanel)
if (new URLSearchParams(location.search).get("hash") === "WIP") {
  wipCommitPanel = mountWipPanel(document.getElementById("wipCommitPanel"), {onFiles: wipFilesChanged});
}

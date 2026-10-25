pyinstaller --noconsole --onefile --name git_graph_webview ^
  --paths . ^
  --add-data "git_graph\locales;git_graph\locales" ^
  --add-data "git_graph\static;git_graph\static" ^
  git_graph_webview.py

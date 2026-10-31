set PATH=python;python\Scripts;%PATH%

pyinstaller --noconsole --onefile --name git_graph_webview ^
  --paths . ^
  --clean^
  --add-data "git_graph\locales;git_graph\locales" ^
  --add-data "git_graph\static;git_graph\static" ^
  --collect-all pygit2 ^
  git_graph_webview.py
copy /Y git_graph_server.json.sample dist\git_graph_server.json
copy /Y scripts\win_ctxmenu_set_webview.bat dist\
copy /Y scripts\win_ctxmenu_unset_webview.bat dist\
robocopy graphviz dist\graphviz /E

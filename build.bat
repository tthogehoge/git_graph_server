set PATH=python;python\Scripts;%PATH%

pyinstaller --noconsole --onefile --name git_graph_webview ^
  --paths . ^
  --add-data "git_graph\locales;git_graph\locales" ^
  --add-data "git_graph\static;git_graph\static" ^
  git_graph_webview.py
copy /Y git_graph_server.json.sample dist\git_graph_server.json
copy /Y win_ctxmenu_set_webview.bat dist\
copy /Y win_ctxmenu_unset.bat dist\
robocopy graphviz dist\graphviz /E

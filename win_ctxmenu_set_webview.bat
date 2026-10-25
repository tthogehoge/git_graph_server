set DIR=%~dp0

REG ADD HKCU\SOFTWARE\Classes\Directory\shell\git_graph_server /t REG_SZ /ve /d git_graph_server
REG ADD HKCU\SOFTWARE\Classes\Directory\shell\git_graph_server\command /t REG_EXPAND_SZ /ve /d "%DIR%git_graph_webview.exe \"%%V\""
REG ADD HKCU\SOFTWARE\Classes\Directory\Background\shell\git_graph_server /t REG_SZ /ve /d git_graph_server
REG ADD HKCU\SOFTWARE\Classes\Directory\Background\shell\git_graph_server\command /t REG_EXPAND_SZ /ve /d "%DIR%git_graph_webview.exe \"%%V\""


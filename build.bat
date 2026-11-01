rmdir /S /Q git_graph_server
rmdir /S /Q dist
call scripts\prepare.bat
call scripts\build.bat
robocopy dist git_graph_server /E
rmdir /S /Q git_graph_server\graphviz
mkdir git_graph_server\graphviz

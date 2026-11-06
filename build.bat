rmdir /S /Q git_graph_server
rmdir /S /Q dist
call scripts\prepare.bat
call scripts\build.bat
robocopy dist git_graph_server /E
rmdir /S /Q git_graph_server\graphviz
mkdir git_graph_server\graphviz
copy /Y LICENSE git_graph_server\
copy /Y THIRD_PARTY_NOTICES git_graph_server\
if exist licenses robocopy licenses git_graph_server\licenses /E
copy /Y README.md git_graph_server\
copy /Y README_JP.md git_graph_server\

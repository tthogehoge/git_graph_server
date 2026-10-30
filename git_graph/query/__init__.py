# git からの読み取り (query)。
#
# リポジトリを変更しないデータ取得をここに置く。HTTP の query 文字列や画面用の文言は知らない
# (必要なら引数で受け取り、結果は dict / list で返す)。route 側 (history.py / wip.py など) は
# 引数を読んで query を呼び、JSON を返す薄い層にする。
#
# 書き込み操作は engine/ に置く。読み取りを engine に混ぜない (undo 記録の対象外のため)。

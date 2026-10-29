# Post 是唯一记录，修正是主数据

数据分两层：不能重算的（posts、assets、selection、corrections）和能重算的（ocr、edition）。公告是 Post 的一个角色，不单独建记录；人工修正是人的决策，不放在「派生」目录下。备选方案是保留 store 层（`data/<year>/<id>.json` + revision），被否：它复制 Post 的数据、制造第二个真相，而官方改动已经由 git 的 diff 记录。

影响：消费者读四个主数据目录；edition 是唯一对外文本；以后再改这条边界，要移动几十 MB 的路径。

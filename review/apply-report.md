# 专家裁决应用报告

- 决定记录：`review/decisions.jsonl` 100 条记录，按 latest-wins 归并为 **97 个 issue**（3 个 issue 各有一条被后续改判覆盖的旧记录）。
- 最新 issue：**13 术语 + 84 OCR**。
- 术语：**12 条 same_alias + 1 条 different（补获/捕获）**。
- OCR：**51 条 accept + 33 条 override**。
- 应用结果：**33 条 override 已写入 28 个 `corrections/dashen/*.json`，51 条 accept 保持 medium 文本不变，stale = 0，problems = []**。
- 未修改 `review/decisions.jsonl`、`corrections/`、`edition/`、`dataset/` 的任何内容；本次只做复核、裁决台只读历史修复和本报告。

## 1. 复核结论

33 条 override 逐条核对通过：

| 检查项 | 结果 |
| --- | --- |
| ledger `corrected` == 裁决文本 | 33/33 |
| ledger `original` == 原始 OCR block | 33/33 |
| `confidence = high` | 33/33 |
| `via = expert-review` | 33/33 |
| `review_issue_id` 指回原 issue | 33/33 |
| reason 含 `review desk 专家裁决` 标记 | 33/33 |

51 条 accept 逐条核对通过：对应 correction 仍为 `confidence = medium`，`original` / `corrected` 与父提交 `3da2f88` 完全一致，未被 apply 改写。

28 个 ledger 文件的 diff 只涉及预期的 **33 个 (image_index, block_index) locator**；entry 数不变，`original` 未变，没有无关字段或文件改动。edition 侧只重建了这 28 篇公告的 Markdown（28 files / 33 行变化）。

`review/ocr-decisions.jsonl`：84 行，`applicable = applied = true` 84 条，`stale = 0`。

数据集：`dataset/notices.jsonl` **220 行**，其中 canonical **179**。

## 2. 术语表（context-aware 统计）

计数来自 `TERM_PAIRS` 的模式，不是裸 substring。特别口径：

- `换新/焕新` 只统计「换新款」「大奖换新」「时装焕新」，排除「兑换新增」「更换新的」「伙伴换新装」。
- `无暇/无瑕` 只统计「无暇信念」「无瑕信念」。
- `进发/迸发`、`补获/捕获` 为上下文相关 pair；前者的 12 条裁决按 same_alias 合并，后者裁决为 different。

| 主写法 | 别名 | 主写法（处/篇） | 别名（处/篇） | 合并后检索覆盖（处/篇） |
| --- | --- | ---: | ---: | ---: |
| 登录 | 登陆 | 255 / 126 | 11 / 9 | 266 / 128 |
| 云雾缥缈 | 云雾飘渺 | 15 / 13 | 7 / 5 | 22 / 18 |
| 石礅 | 石墩 | 5 / 5 | 26 / 18 | 31 / 22 |
| 金缎 | 金锻 | 119 / 70 | 5 / 3 | 124 / 71 |
| 焕新 | 换新 | 84 / 41 | 3 / 3 | 87 / 43 |
| 银辉之钥 | 银辉之匙 | 37 / 9 | 3 / 3 | 40 / 11 |
| 迸发 | 进发 | 4 / 4 | 4 / 3 | 8 / 7 |
| 凰吟凤鸣 | 凤吟凰鸣 | 4 / 3 | 1 / 1 | 5 / 3 |
| 戈德里克 | 戈德利克 | 2 / 2 | 1 / 1 | 3 / 2 |
| 等级上限 | 等级上线 | 10 / 6 | 1 / 1 | 11 / 7 |
| 长袍专卖店 | 长跑专卖店 | 49 / 36 | 1 / 1 | 50 / 36 |
| 鲁伯 | 鲁本 | 27 / 24 | 1 / 1 | 28 / 25 |

**不合并的 different pair：**

| 写法 A | 写法 B | 覆盖 | 裁决 |
| --- | --- | --- | --- |
| 补获 1 处 / 1 篇 | 捕获 2 处 / 2 篇 | 共 3 篇公告 | 不同事物，保持区分，不建立 alias |

12 条 alias 已写入 `review/terms.json` 的 `terms`，`补获/捕获` 单独写入 `different`；preferred/aliases 与决定账本逐条一致。

## 3. OCR：33 条应用 / 51 条确认

- 33 条 override 分布在 **28 个 ledger 文件**，全部变成 `high` + `expert-review`，并保留原始 reason 后追加 `review desk 专家裁决`。
- 51 条 accept 不需要写 ledger；对应 medium correction 原样保留。
- 抽查 before → after（原文来自 `derived/ocr` 的 raw block，after 来自 ledger/edition）：

| 公告 | 原始 OCR | 裁决后 |
| --- | --- | --- |
| dashen.63bd7308…（2023-01-11）图1 block42 | 飞碟将飞回巫师身边，并对接触到的移中造伤疲特魔法觉醒 | 飞碟将飞回巫师身边，并对接触到的敌人造成伤害。 |
| dashen.63c6b111…（2023-01-18）图7 block37 | 爆炸南瓜将更容易被击破，爆炸伤害@哈利波特魔法觉醒 | 爆炸南瓜将更容易被击破，爆炸伤害不变。 |
| dashen.660ab22a…（2024-04-02）图2 block74 | 7.“集中效应”——咒语伤害增加效果赢系%觉醒 | 7.“集中效应”——咒语伤害增加效果由60%提高至80%。 |
| dashen.6a4b95e2…（2026-07-08）图2 block21 | 赛季预告开启时间：2026年7月15六神00月22日7：00觉醒 | 赛季预告开启时间：2026年7月15日7:00-7月22日7:00 |
| dashen.687e2b3b…（2025-07-23）图5 block48 | 不影响游戏内其他声音（如背景音神雪效等皮特效果觉醒 | 不影响游戏内其他声音（如背景音乐、音效等）效果。 |

33 条新文本全部能在对应公告的 `edition/dashen/<feed>.md` 和 `dataset/notices.jsonl` 行中找到。

## 4. 审计状态

- `audit-status --check`：**0 queued**，162 个 ledger 全部 up to date。
- 审计 totals：adjudicated 1955，consensus 22966，corrected 1970，dropped 512，punct_rule 600，record 16，watermark 653。
- `verify`：162 个 ledger / 3268 条 correction / 0 problems。
- `edition --check`：0 problems；`dataset --check`：220 rows，up to date。

## 5. 裁决台：97 题全部可见

修复前：33 条 override 应用后变成 high，`build_queue()` 只返回 64 题（13 术语 + 51 medium OCR），33 条已裁决记录从“已裁决”视图消失。

修复后：`build_queue()` 会为“已有决定、但当前不再生成 medium issue”的 OCR 项重建只读 **archived issue**（从 OCR/ledger 读取 original/corrected/reason，字段与正常 OCR 卡片一致，`archived: true`、`applied: true`、`options: []`）。

真实服务 `GET /api/queue`（端口 8769）返回：

```json
{"source": "review", "stats": {"total": 97, "pending": 0, "deferred": 0, "decided": 97, "high": 0, "progress": 1.0}}
```

- 97 = 64 正常题（13 术语 + 51 OCR）+ 33 archived。
- `filter_issues("decided")` = 97；`term = 13`、`ocr = 84`、`high_total = 6`。
- archived 卡片显示“已应用 / 归档”和 original/corrected/reason，不渲染决定按钮、备注框、保存栏或“改回待裁决”；直接 POST 会被 400 拒绝（`这条裁决已应用并归档，不能再次保存`）。

## 6. 门禁结果

| 门禁 | 结果 |
| --- | --- |
| `pytest` | 263 passed |
| `ruff check .` | All checks passed |
| `verify` | 162 ledgers / 3268 corrections / 0 problems |
| `audit-status --check` | 0 queued / 162 ledgers up to date |
| `edition --check` | 0 problems |
| `dataset --check` | 220 rows / up to date |
| `review --apply --dry-run` | ledger_files = []，stale = 0，problems = []，未写盘 |

幂等说明：dry-run 的 OCR 结果为 **applied = 0 / noop = 84**；`term.applied = 13` 是 12 条 alias + 1 条 different 每次重新物化到 `terms.json` 的计数，不代表 ledger 变动（`ledger_files = []`）。真实 OCR ledger 未被二次写入。

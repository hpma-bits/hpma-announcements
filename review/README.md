# 文本裁决台 · 数据层

一次只让专家裁决一个文本问题。本目录的 `decisions.jsonl` 是唯一的裁决主数据
（append-only，latest wins）；`terms.json` 和 `ocr-decisions.jsonl` 是导出产物，
可由 `review.export()` 随时重建。

数据层实现在 [`tools/review.py`](../tools/review.py)，不依赖旧 checkpage。
`export()` 只写本目录；`apply()` 把 OCR 专家裁决写进 `corrections/`（不会
自动改 `edition/`、`dataset/`，它们随后从账本重建）。

## 队列

`build_queue(root=None)` 返回 `{"issues": [...], "stats": {...}}`。术语
（`term_conflict`）在前，按 impact（稀有写法的 `minor_count`、
`minor_notices`）降序再按 id；OCR（`ocr_block`）在后，按 `risk`
（`source_damage` 先）再按 id。OCR 不使用 `high` 严重度，所以高优先筛选只
剩下术语冲突。

### `term_conflict`

同一事物出现两种写法时用于决定是否合并检索。来源是全部 update 公告的
`body + image_text`（图片文字已应用人工修正账本）。`tools/review.py` 的
`TERM_PAIRS` 定义 14 对写法及逐对匹配规则；只有两种写法都实际出现时才生成
issue（例如「无暇/无瑕」只在「无暇信念/无瑕信念」时统计，普通词条不算）。

```json
{
  "id": "term:金锻|金缎",
  "type": "term_conflict",
  "severity": "high",
  "title": "「金锻」与「金缎」",
  "question": "这两种写法指同一个事物吗？检索时是否合并？",
  "why": "两种写法都出现在官方原文；当前精确检索会把它们拆成两组。",
  "match_note": "按字面统计两种写法的全部出现",
  "variants": [
    {"text": "金锻", "count": 5, "notices": 3},
    {"text": "金缎", "count": 119, "notices": 70}
  ],
  "impact": {
    "minor_count": 5,
    "minor_notices": 3,
    "total_notices": 70,
    "search_gain": 5
  },
  "contexts": [
    {
      "variant": "金锻",
      "notice_id": "dashen.xxx",
      "notice_date": "2022-01-26",
      "title": "【正式服更新公告】",
      "source": "dashen",
      "section": "正文",
      "image": null,
      "line": "……金锻……",
      "before": "上一行",
      "after": "下一行"
    }
  ],
  "options": [
    {"value": "same_alias", "label": "同一事物，检索合并", "description": "保留原文，建立 alias"},
    {"value": "different", "label": "不同事物，保持区分", "description": ""},
    {"value": "ocr_error", "label": "其中一个是 OCR 错误", "description": ""},
    {"value": "defer", "label": "稍后决定", "description": ""}
  ],
  "recommendation": "系统建议，仅供参考。"
}
```

- `count` 是按该对匹配口径统计的出现次数，`notices` 是涉及公告数；
  `match_note` 说明统计口径，UI 应原样展示。
- 匹配规则（`TermPair`）：
  - 金锻/金缎、石墩/石礅、登陆/登录、鲁本/鲁伯、云雾飘渺/云雾缥缈、
    凤吟凰鸣/凰吟凤鸣、等级上线/等级上限、银辉之匙/银辉之钥、
    戈德利克/戈德里克、长跑专卖店/长袍专卖店：字面匹配。
  - 换新：只统计「换新款」「大奖换新」「时装换新」，不统计「兑换新增」
    「更换新的」「伙伴换新装」。
  - 焕新：只统计「焕新款」「大奖焕新」「时装焕新」，不统计泛用的「焕新」动词。
  - 无暇/无瑕：只统计「无暇信念」「无瑕信念」；若两边不同时出现，不生成 issue。
  - 进发/迸发、补获/捕获：字面匹配，但标记 `context_dependent: true`。
- 上下文每个写法最多 8 条（`contexts` 合计最多 16 条）；`count`/`notices`
  始终是完整统计。
- `section` 为 `正文` 或 `图片文字`；图片文字上下文带 `image` 序号。
- `impact.search_gain` 是合并检索后稀有写法新增的覆盖处数。
- 严重度：`context_dependent` 的对固定为 `medium`；其余稀有写法（数量少的
  一边）出现 ≥3 处或涉及 ≥5 篇公告为 `high`，否则 `medium`。
- 上下文相关的对在 `recommendation` 里提示按上下文判断词义；其余
  `recommendation` 只是系统提示，不是裁决结果。

### `ocr_block`

来自 `corrections/dashen/*.json` 中 `confidence == "medium"`、非 `drop`、
`corrected` 非空且不是纯页脚装饰的条目。装饰条目的 reason 含
`页脚装饰水印`、`HARRY POTTER` 或 `letter-spaced` 时排除。

```json
{
  "id": "ocr:dashen.xxx:3:31",
  "type": "ocr_block",
  "severity": "medium",
  "risk": "source_damage",
  "title": "dashen.xxx · 图 3 · block 31",
  "question": "这个 block 的源图文字是什么？",
  "why": "confidence=medium；行尾被水印覆盖，当前修正是按可见笔画重建的。",
  "notice": "dashen.xxx",
  "notice_date": "2022-01-26",
  "notice_title": "【正式服更新公告】",
  "image_index": 3,
  "block_index": 31,
  "original": "……",
  "corrected": "……",
  "reason": "……",
  "confidence": "medium",
  "crop": {"notice": "dashen.xxx", "image": 3, "block": 31, "scale": 2},
  "context_before": ["……", "……"],
  "context_after": ["……", "……"],
  "options": [
    {"value": "accept", "label": "接受当前修正", "description": ""},
    {"value": "override", "label": "改为我输入的文字", "description": ""},
    {"value": "keep_original", "label": "保留 OCR 原文", "description": ""},
    {"value": "source_issue", "label": "印刷原样", "description": ""},
    {"value": "drop", "label": "整块不是正文", "description": ""},
    {"value": "defer", "label": "稍后", "description": ""}
  ]
}
```

- `original` / `corrected` 直接来自修正账本条目；`block_index` 是该图片 OCR
  blocks 列表中的位置。
- 上下文取同一图片 OCR blocks 中该块前后各 2 个非空行。
- OCR 的 `severity` 统一为 `medium`，避免 84 条 OCR 淹没高优先队列。
- `risk` 说明不确定性的来源：
  - `source_damage`：reason 含水印/截断/覆盖（含英文 watermark/truncat/covered）。
  - `style`：reason 描述字形、引号、ASCII、排版等样式问题。
  - `other`：其他无法归入以上两类的原因。
  队列内 `source_damage` 排在前面；它只影响 OCR 内部顺序，不改变严重度。

### `stats`

```json
{
  "total": 97,
  "pending": 97,
  "resolved": 0,
  "deferred": 0,
  "high": 6,
  "high_total": 6,
  "term": 13,
  "ocr": 84,
  "term_pending": 13,
  "ocr_pending": 84,
  "source_damage": 75
}
```

- `resolved`：最新决定不是 `defer` 的 issue 数。
- `pending = total - resolved`，`defer` 仍算 pending，同时单独计入 `deferred`。
- `high` 是尚未 resolved 的 high 数；`high_total` 是全部 high 数。当前 high
  只可能来自术语冲突。
- `source_damage` 是 risk 为 `source_damage` 的 OCR issue 数。

## 决策账本

`review/decisions.jsonl`，一行一个决定，append-only；同一 `issue_id` 以文件
中最后一条为准。每行带 `issue_id`、`type`、`decision`、`note`、`decided_at`，
以及该决定需要的 payload。

```json
{"issue_id":"term:金锻|金缎","type":"term_conflict","decision":"same_alias","preferred":"金缎","aliases":["金锻"],"note":"官方两种写法都出现，检索合并","decided_at":"2026-11-13T08:00:00Z"}
{"issue_id":"ocr:dashen.xxx:3:31","type":"ocr_block","decision":"override","corrected":"……","note":"……","decided_at":"2026-11-13T08:01:00Z"}
```

决定含义：

| type | decision | 含义 | payload |
| --- | --- | --- | --- |
| term_conflict | `same_alias` | 同一事物，保留原文并建立检索 alias | `preferred` 必填，`aliases` 可选（默认另一种写法） |
| term_conflict | `different` | 不同事物，保持区分 | - |
| term_conflict | `ocr_error` | 其中一种写法是 OCR 错误，留待 OCR 复核 | - |
| term_conflict | `defer` | 稍后 | - |
| ocr_block | `accept` | 接受账本当前修正 | - |
| ocr_block | `override` | 改为专家输入文字 | `corrected` 必填 |
| ocr_block | `keep_original` | 保留 OCR 原文，当前修正作废 | - |
| ocr_block | `source_issue` | 印刷原样，保留 | - |
| ocr_block | `drop` | 整块不是正文 | - |
| ocr_block | `defer` | 稍后 | - |

Python API：

```python
from tools import review

queue = review.build_queue()                  # issues + stats
review.save_decision(
    "term:金锻|金缎",
    "same_alias",
    {"preferred": "金缎", "aliases": ["金锻"]},
    "官方两种写法都出现，检索合并",
)
latest = review.load_decisions()              # {issue_id: 最新记录}
review.export()                               # 写 terms.json / ocr-decisions.jsonl
review.apply(dry_run=True)                    # 只计算不写
review.apply()                                # 写 corrections/ / terms.json / apply-report.json
```

`save_decision` 会校验 issue id 前缀、决定取值，以及 `same_alias` 的
`preferred`、`override` 的 `corrected`。写入路径默认是本目录的
`decisions.jsonl`；测试应显式传 `path`，不要写真实账本。

## 工作流

1. 启动裁决台：`python -m tools.cli review`（由集成层提供）。
2. 首屏只出现一个 issue；按 severity/type 顺序处理。
3. 选择决定并保存；`defer` 会记录，但 issue 仍留在待裁决队列。
4. 处理完后运行导出（页面按钮或 `review.export()`）。
5. 把 `terms.json` / `ocr-decisions.jsonl` 交给后续检索/OCR 流程。
6. 需要落地 OCR 专家裁决时运行应用：

   ```bash
   python -m tools.cli review --apply --dry-run   # 只报告
   python -m tools.cli review --apply             # 写 corrections/ 和 terms.json
   python -m tools.cli edition                    # 从账本重建可读版
   python -m tools.cli dataset                    # 重建站点数据
   python -m tools.cli review --export            # 刷新 review 导出产物
   ```

## 应用

`review.apply(root=None, dry_run=False)` 读取最新决定并落地：

- 术语：`same_alias` 写入 `terms.json` 的 `terms`；`different` 写入同一文件的
  `different`。`ocr_error` / `defer` 不物化。术语只生成 alias/authority，不改
  原文，也不改 `edition/` 文本。
- OCR `accept`：不改账本，只确认当前修正仍然适用。
- OCR `override`：校验决定对应的 `original` 等于当前 OCR block 文本后，把
  账本里同一 `(image_index, block_index)` 条目的 `corrected` 改成专家文本，
  `confidence` 改为 `high`，`via` 改为 `expert-review`，reason 追加
  `；review desk 专家裁决`，并写入 `review_issue_id`。没有条目时按当前 OCR
  文本创建 `original`/`corrected`/`reason`/`confidence`/`via`/`recrop`。
- OCR `keep_original`：删除该 locator 的修正，回到原始 OCR 文本。
- OCR `source_issue`：不改账本（印刷原样）。
- OCR `drop`：把该 locator 写成 `drop: true`（不带 `corrected`）。
- OCR `defer`：跳过。
- 所有 OCR 决定先校验当前 OCR；`original` 不一致或 block 已不存在时记为
  `stale`，绝不写入。
- 重复运行是幂等的：override 文本已一致、drop 已存在、keep_original 已无
  修正都记为 `noop`，账本字节不变。

账本用临时文件 + `os.replace` 原子替换。`review/apply-report.json` 记录：
`generated_at`、`dry_run`、`term` 各决定计数、`ocr` 各决定计数以及
`applied`/`noop`/`stale`/`skipped`、`stale_items`、`skipped_items`、
`ledger_files`（被修改的账本）、`problems`。`--dry-run` 返回同样的报告但
不写任何文件。

## 导出

### `review/terms.json`

`terms` 是最新决定为 `same_alias` 的术语 issue；`different` 保留专家判定
「不是同一事物」的成对写法，避免该决定在导出中丢失：

```json
{
  "generated_at": "2026-11-13T08:02:00Z",
  "terms": [
    {
      "preferred": "金缎",
      "aliases": ["金锻"],
      "issue_id": "term:金缎|金锻",
      "note": "官方两种写法都出现，检索合并",
      "decided_at": "2026-11-13T08:00:00Z"
    }
  ],
  "different": [
    {
      "terms": ["补获", "捕获"],
      "issue_id": "term:补获|捕获",
      "note": "不同词义",
      "decided_at": "2026-11-13T08:03:00Z"
    }
  ]
}
```

`ocr_error` / `defer` 不进入 terms 导出；`preferred` 本身不会出现在
`aliases` 里。

### `review/ocr-decisions.jsonl`

每条非 `defer` 的 OCR 决定一行（`defer` 仍属待裁决，不导出）。导出时用
`original` 对比当前 `derived/ocr/dashen/` 中的 block 文本：

- 一致：`"applicable": true, "status": "applicable"`。
- 不一致或 block 已不存在：`"applicable": false, "status": "stale"`，并带上
  `current_ocr`；下游必须拒绝应用 stale 记录。

```json
{"issue_id":"ocr:dashen.xxx:3:31","notice":"dashen.xxx","image_index":3,"block_index":31,"decision":"accept","original":"……","corrected":"……","current_ocr":"……","applicable":true,"status":"applicable","applied":true,"note":"","decided_at":"2026-11-13T08:01:00Z"}
```

`keep_original` 导出 `corrected = original`；`drop` 导出空字符串；
`accept`/`source_issue` 导出账本的 `corrected`；`override` 导出专家输入。
`note` 和 `decided_at` 原样保留，便于审计。`applied` 表示账本当前是否已经
体现该决定：`override` 的 `corrected` 与专家文本一致、`accept`/`source_issue`
的修正仍在且原文匹配、`keep_original` 已无修正、`drop` 已是 drop。已应用后
再导出不会因为修正变成 `high` 而从队列消失，`override` 仍会出现在导出里。

如果修正账本与 OCR 已经不一致，`load_fulltext_rows()` 会对该公告退回未修正的
OCR 文本，保证队列仍可打开；相应决定在导出时标记为 `stale`，不会被静默应用。

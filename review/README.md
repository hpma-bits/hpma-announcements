# 文本裁决台 · 数据层

一次只让专家裁决一个文本问题。本目录的 `decisions.jsonl` 是唯一的裁决主数据
（append-only，latest wins）；`terms.json` 和 `ocr-decisions.jsonl` 是导出产物，
可由 `review.export()` 随时重建。

数据层实现在 [`tools/review.py`](../tools/review.py)，不依赖旧 checkpage，也不写
`corrections/`、`edition/`、`dataset/`——裁决结果只进入本目录。

## 队列

`build_queue(root=None)` 返回 `{"issues": [...], "stats": {...}}`。issue 按
`severity`（high → medium）再按 `type`（term → ocr）再按 id 排序。

### `term_conflict`

同一事物出现两种写法时用于决定是否合并检索。来源是全部 update 公告的
`body + image_text`（图片文字已应用人工修正账本）。14 对写法见
`tools/review.py` 的 `TERM_PAIRS`；只有两种写法都实际出现时才生成 issue。

```json
{
  "id": "term:金锻|金缎",
  "type": "term_conflict",
  "severity": "high",
  "title": "「金锻」与「金缎」",
  "question": "这两种写法指同一个事物吗？检索时是否合并？",
  "why": "两种写法都出现在官方原文；当前精确检索会把它们拆成两组。",
  "variants": [
    {"text": "金锻", "count": 5, "notices": 3},
    {"text": "金缎", "count": 119, "notices": 70}
  ],
  "impact": {"minor_count": 5, "total_notices": 70, "search_gain": 5},
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

- `count` 是子串出现次数，`notices` 是涉及公告数。
- 上下文每个写法最多 8 条（`contexts` 合计最多 16 条）；`count`/`notices`
  始终是完整统计。
- `section` 为 `正文` 或 `图片文字`；图片文字上下文带 `image` 序号。
- `impact.search_gain` 是合并检索后稀有写法新增的覆盖处数。
- 严重度：稀有写法（数量少的一边）出现 ≥3 处或涉及 ≥5 篇公告为 `high`，
  否则 `medium`。
- `recommendation` 只是系统提示，不是裁决结果。

### `ocr_block`

来自 `corrections/dashen/*.json` 中 `confidence == "medium"`、非 `drop`、
`corrected` 非空且不是纯页脚装饰的条目。装饰条目的 reason 含
`页脚装饰水印`、`HARRY POTTER` 或 `letter-spaced` 时排除。

```json
{
  "id": "ocr:dashen.xxx:3:31",
  "type": "ocr_block",
  "severity": "high",
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
- 严重度：reason 含水印/截断/覆盖（含英文 watermark/truncat/covered），或
  `corrected` 长度 ≥10 为 `high`，否则 `medium`。

### `stats`

```json
{
  "total": 98,
  "pending": 98,
  "resolved": 0,
  "deferred": 0,
  "high": 90,
  "high_total": 90,
  "term": 14,
  "ocr": 84,
  "term_pending": 14,
  "ocr_pending": 84
}
```

- `resolved`：最新决定不是 `defer` 的 issue 数。
- `pending = total - resolved`，`defer` 仍算 pending，同时单独计入 `deferred`。
- `high` 是尚未 resolved 的 high 数；`high_total` 是全部 high 数。

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
```

`save_decision` 会校验 issue id 前缀、决定取值，以及 `same_alias` 的
`preferred`、`override` 的 `corrected`。写入路径默认是本目录的
`decisions.jsonl`；测试应显式传 `path`，不要写真实账本。

## 工作流

1. 启动裁决台：`python -m tools.cli review`（由集成层提供）。
2. 首屏只出现一个 issue；按 severity/type 顺序处理。
3. 选择决定并保存；`defer` 会记录，但 issue 仍留在待裁决队列。
4. 处理完后运行导出（页面按钮或 `review.export()`）。
5. 把 `terms.json` / `ocr-decisions.jsonl` 交给后续检索/OCR 流程；本数据层
   不会自动改 `corrections/`、`edition/`、`dataset/`。

## 导出

### `review/terms.json`

只包含最新决定为 `same_alias` 的术语 issue：

```json
{
  "generated_at": "2026-11-13T08:02:00Z",
  "terms": [
    {
      "preferred": "金缎",
      "aliases": ["金锻"],
      "issue_id": "term:金锻|金缎",
      "note": "官方两种写法都出现，检索合并",
      "decided_at": "2026-11-13T08:00:00Z"
    }
  ]
}
```

`different` / `ocr_error` / `defer` 不进入 terms 导出；`preferred` 本身不会
出现在 `aliases` 里。

### `review/ocr-decisions.jsonl`

每条非 `defer` 的 OCR 决定一行（`defer` 仍属待裁决，不导出）。导出时用
`original` 对比当前 `derived/ocr/dashen/` 中的 block 文本：

- 一致：`"applicable": true, "status": "applicable"`。
- 不一致或 block 已不存在：`"applicable": false, "status": "stale"`，并带上
  `current_ocr`；下游必须拒绝应用 stale 记录。

```json
{"issue_id":"ocr:dashen.xxx:3:31","notice":"dashen.xxx","image_index":3,"block_index":31,"decision":"accept","original":"……","corrected":"……","current_ocr":"……","applicable":true,"status":"applicable","note":"","decided_at":"2026-11-13T08:01:00Z"}
```

`keep_original` 导出 `corrected = original`；`drop` 导出空字符串；
`accept`/`source_issue` 导出账本的 `corrected`；`override` 导出专家输入。
`note` 和 `decided_at` 原样保留，便于审计。

如果修正账本与 OCR 已经不一致，`load_fulltext_rows()` 会对该公告退回未修正的
OCR 文本，保证队列仍可打开；相应决定在导出时标记为 `stale`，不会被静默应用。

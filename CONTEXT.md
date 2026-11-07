# HPMA update-notice archive

一个官方账号加一个社区索引的公开内容存档：抓取每个来源的帖子，选出其中的更新公告，把图片里的文字识别、修正，最后生成可读的文本版。Dashen 是主源，NGA 索引帖补 2021 年的缺口，并为 2022 年重叠期提供转载的运营方纯文本。

## 两层数据

- **主数据**：抓到的、人判定的、不可重算的——Post、Image、Selection、Correction。
- **派生数据**：能由主数据重算的——OCR result、Edition。

派生层坏了可以重建，主数据坏了就没了。所以人工修正存在账本里，不放在 `derived/` 下；公告只是 Post 的一个角色，不单独建记录；修订历史交给 git，不设 `revision` 字段。

## Language

**Source**：
帖子的来源。`dashen` 是官方账号，`nga` 是社区索引帖；每个 Source 有自己的 corpus、labels 和 edition 目录，ID 用 `<source>.<native_id>`。
_Avoid_: platform、feed、channel

**Post**：
某个 Source 发布的一条内容，是存档里唯一的一等记录。Dashen 的 Post 是一条 feed/article；NGA 的 Post 是一个帖子。
_Avoid_: feed、corpus item、notice record

**Body text**：
Post 自带的文字；type-6 长文的 `long_text`（HTML）也属于 Body text。NGA 的 Body text 是从第一楼 BBCode 还原的公告正文。
_Avoid_: corpus text、原文

**Notice**：
被 Selection 判定为更新公告的 Post。它是 Post 的角色，不单独存记录。
_Avoid_: notice record、公告实体

**Selection**：
对 Post 的判定：哪条是公告、属于哪个家族、公告日期是什么。是人的决策，不是可重算的结果。每个 Source 一份账本：`study/labels.jsonl`（Dashen）、`study/labels-nga.jsonl`（NGA）。
_Avoid_: labels 文件、classifier output

**Image**：
Dashen Post 的附件，按 index 排序；身份是 `(post_id, index)`，字节用 sha256 校验。NGA 的图床图片不入库，只保留正文里的原始引用。
_Avoid_: asset、media

**OCR result**：
引擎对 Dashen Image 的输出：blocks（box / text / score）加引擎指纹。派生数据，可重建，但重建依赖引擎版本。
_Avoid_: raw text、derived text

**Correction**：
人工判定某个 OCR block 应读作什么的记录：original / corrected / reason / confidence / recrop；或 `drop: true` 表示该 block 是重叠切片等非正文残片，整块丢弃。是决策，不是派生数据；`original` 与当前 OCR 对不上就拒绝应用，不允许静默改字。
_Avoid_: fix、patch、corrected text

**Edition**：
由 Post（Dashen 另加 OCR result + Correction）生成的阅读文本：每篇一份 Markdown，另加一个 jsonl 索引。派生数据。NGA 的 Edition 只有正文，没有 OCR 段；`edition/notices.jsonl` 合并两个 Source，按 `published_at` 排序。
_Avoid_: corrected layer、output

**Canonical**：
同一公告日期的优先阅读版本。有 NGA 纯文本时选 NGA，否则选 Dashen；只影响 `edition/notices.jsonl` 的 `canonical` 标记，不删除另一来源的 Edition 或图片记录。
_Avoid_: preferred、dedupe

**Repost**：
NGA 帖是对官方公告的社区转载，不是官方原文。Edition 用 `source: "nga"` 和 `source_author_uid` 标明。
_Avoid_: original、official text

**Audit**：
用第二个引擎（v6）独立重读全部图片，与 v4 记录逐块对齐、差异逐条核图的审校过程；每块最终状态（consensus / corrected / adjudicated / punct_rule / record / source_issue / watermark / dropped）记在 `derived/audit/dashen/`。质量门是 `queued == 0`。
_Avoid_: 二次 OCR、recheck

**Insert**：
Correction 的一种：`insert: true` + `after_block` + `text`，记录引擎整行漏识、经 v6 审计与 crop 核验的印刷行；Edition 把它插在锚点块之后。不是派生数据。
_Avoid_: 补行、append

**Dataset**：
面向网站投影的 JSONL：每篇一条，含 canonical、全文、分图行与 audit 质量摘要。派生数据，由 `python -m tools.cli dataset` 生成。
_Avoid_: API、export

**Watermark**：
平台给 Dashen 长图打的「大神@哈利波特魔法觉醒」。它不是公告内容，Edition 里整块丢弃，混在正文行尾的截掉。
_Avoid_: logo、角标

**Study**：
选择方法的离线证据：标注 prompt、评估报告、一致性数字。不是管线的一站。
_Avoid_: corpus study

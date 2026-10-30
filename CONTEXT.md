# HPMA update-notice archive

一个官方账号的公开内容存档：抓取它发的每条帖子，选出其中的更新公告，把图片里的文字识别、修正，最后生成可读的文本版。

## 两层数据

- **主数据**：抓到的、人判定的、不可重算的——Post、Image、Selection、Correction。
- **派生数据**：能由主数据重算的——OCR result、Edition。

派生层坏了可以重建，主数据坏了就没了。所以人工修正存在账本里，不放在 `derived/` 下；公告只是 Post 的一个角色，不单独建记录；修订历史交给 git，不设 `revision` 字段。

## Language

**Post**：
账号在 Dashen 发布的一条内容，是存档里唯一的一等记录。
_Avoid_: feed、corpus item、notice record

**Body text**：
Post 自带的文字；type-6 长文的 `long_text`（HTML）也属于 Body text。
_Avoid_: corpus text、原文

**Notice**：
被 Selection 判定为更新公告的 Post。它是 Post 的角色，不单独存记录。
_Avoid_: notice record、公告实体

**Selection**：
对 Post 的判定：哪条是公告、属于哪个家族、公告日期是什么。是人的决策，不是可重算的结果。
_Avoid_: labels 文件、classifier output

**Image**：
Post 的附件，按 index 排序；身份是 `(post_id, index)`，字节用 sha256 校验。
_Avoid_: asset、media

**OCR result**：
引擎对 Image 的输出：blocks（box / text / score）加引擎指纹。派生数据，可重建，但重建依赖引擎版本。
_Avoid_: raw text、derived text

**Correction**：
人工判定某个 block 应读作什么的记录：original / corrected / reason / confidence / recrop。是决策，不是派生数据；`original` 与当前 OCR 对不上就拒绝应用，不允许静默改字。
_Avoid_: fix、patch、corrected text

**Edition**：
由 Post + OCR result + Correction 生成的阅读文本：每篇一份 Markdown，另加一个 jsonl 索引。派生数据。
_Avoid_: corrected layer、output

**Watermark**：
平台给长图打的「大神@哈利波特魔法觉醒」。它不是公告内容，Edition 里整块丢弃，混在正文行尾的截掉。
_Avoid_: logo、角标

**Study**：
选择方法的离线证据：标注 prompt、评估报告、一致性数字。不是管线的一站。
_Avoid_: corpus study

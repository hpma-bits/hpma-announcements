# Labeling guide (prompt v1, 2026-10-29)

You are labeling posts from the verified official *Harry Potter: Magic
Awakened* (哈利波特：魔法觉醒) account on NetEase Dashen, spanning 2019-12 to
2026-10. Post fields: `id`, `date` (published_at, +08:00), `type` (2 = text
post, 3 = video, 6 = long article, others rare), `title` (extracted:
`body.title` → `body.subTitle` → first line of body text), `text` (body text,
truncated), `long_text_head` (type 6 only, HTML head), `n` (attached image
count).

## Labels — exactly one per post

- **update**: periodic official update/patch announcement for the live game —
  e.g. `【M月D日更新公告】`, `【正式服更新公告】` — announcing a maintenance
  window and the changes going live. The detailed changes are usually in the
  attached long images, so *short text + several images + a 更新公告 title* is
  the expected shape of an `update` post.
- **maintenance**: one-off server maintenance / downtime / delay notice
  (停服、维护、延后、补偿) that is not the periodic update series.
- **version**: major version material — 大版本更新公告, 预下载公告, 赛季更新
  内容预览, 版本前瞻.
- **test_server**: 体验服 (test server) update notices.
- **event**: event/fashion/gacha/activity promos and gameplay teasers
  (✦…✦ style), including `限时玩法更新` promos and 场景/玩法 previews.
- **other**: community posts, birthdays, lore, merchandise, tips, greetings.
- **ambiguous**: cannot decide from the given text.

## Rules

1. Judge content, not title alone — but the title/first line is the strongest
   single signal.
2. Do not assume one fixed format. Formats changed between 2019 and 2026;
   older posts may say 正式服/体验服 instead of a date.
3. `notice_date`: if the post clearly names an effective date for an update or
   maintenance (e.g. `10月28日`), output `YYYY-MM-DD`, inferring the year from
   the post date (choose the candidate within ±6 days). Otherwise `null`.
4. `evidence`: a verbatim quote of at most 40 characters that justifies the
   label.
5. `confidence`: `high` / `medium` / `low`.

## Output

One JSON object per line, JSONL, in the same order as the input batch:

```json
{"id": "...", "label": "update", "confidence": "high", "evidence": "【10月28日更新公告】", "notice_date": "2026-10-28"}
```

Write the file with the Write tool. Do not paste the labels into your reply —
reply only with the batch name and the number of posts you labeled.

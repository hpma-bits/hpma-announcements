# Selection study: official update notices on Dashen (MVP-3 synthesis)

Corpus: 2,324 posts, 2019-12-24..2026-10-28, account `哈利波特魔法觉醒`
(uid `29c10cd0344f462e82e16a516001f0dd`). Labels: event 1362, other 519,
version 183, update 162, test_server 73, maintenance 25; ambiguous 0.
All 443 positives read in full (`study/positives.json`); hard negatives
grepped in `corpus/dashen/posts.jsonl`. The section-4 procedure was executed
against all 2,324 posts; section-5 numbers are measured, not estimated.

## 1. Label quality

Inconsistencies found while reading; "should be" = the section-4 procedure's
output, with borderline calls quarantined rather than relabeled.

- `64c79c5924f5` 【8月1日维护公告】 labeled **update** (high): pure maintenance
  window, shape identical to `619d1550b4e5` (maintenance); it only fills the
  2023-08-01 weekly slot. Should be **maintenance**. Similarly `63c02c94849e`
  (activity adjustment at copyright request), `65a009c3c8bc` (mode closure +
  compensation), `631814267442` (reward compensation) are labeled
  **maintenance** with no maintenance action → **other**.
- `62b574c10c8d` vs `62fc93f1849e`: same opener (各位亲爱的巫师，), same
  post-maintenance bugfix content, but one is **other**, one
  **maintenance**. Pick one (procedure says maintenance); `62b574c10c8d` is
  the suspected error.
- Launch-era twins, same title, split labels: `611f13ad3408` (version) vs
  `612089367fe2`, `6128e5cb3408`, `61304981c5a3`, `61330b653408`,
  `61345ce5849e`, `6135cc18849ee2` (event/other), all announcing
  公测定档9月9日; `61396bf4c3e1` (version) vs `61396eb8c3e1` (event) share the
  title #霍格沃茨开学季#【哈利波特游戏上线】 on the same day. Either all are
  version notices or only the canonical ones; currently under-labeled.
- Season-serial family ✦X·剧情序章✦ / ✦X赛季·奖励总览✦ is split by batch, not by
  shape. Labeled **version**: `652e3f06b2de`, `652e460f348c`, `6576dd6aa937`,
  `65c06b5910a2`, `660b869ddd42`, `66544b96dacc`, `689af485708b`,
  `68a2eeb43274`, `68eb43e54d87`, `68ee031f865a`, `692fdff55390`,
  `69368587a341`, `697af76545b8`, `6a5f18f325ab`, `6aa8ed4a8e71` (龙研究/
  霍格莫德/草药学/魔咒/黑湖/于群星之间/徘徊夜的幽语/光与影的抉择/黑龙缚影之谜/
  绯红华章/定格的旧时光). Labeled **event/other** for identical shapes:
  `66a08a55d6a8`, `677ce0354c9f`, `6777ca227b9d`, `67c6c1153855`,
  `66e823cfc1be`, `6731fc81fb64`, `67c802453218`, `681078f73dd6`,
  `685a565658da`, `6a140f92ce95`, `69ca2d822f9f` (洲际巫师杯/黑魔王往事/
  月下高布石之影/黯影与微光/冬夜故事谈/高布石/霜结燃烧火刑架/集结！英雄远行/
  飞跃云端/费金疑案).
- **Kept as-is:** `6189d895c470` (feature delay) and
  `62026e71c3e1`/`624a77763408` (【更新延后公告】) as maintenance;
  `62f3b68a3408` (emergency card-balance hotfix) as update; `61ef7afc7442`
  (雪日奇旅 preview) as version. Confidence: 2071/2324 high overall; among
  positives only irregular titles are medium (46 version, 8 maintenance,
  1 update) — matching the real ambiguity.

## 2. Notice-series timeline

| series | n | first | last | cadence |
|---|---|---|---|---|
| update | 162 | 2022-01-26 | 2026-10-28 | weekly Tue → biweekly Wed |
| maintenance | 25 | 2021-09-22 | 2024-02-20 | irregular, as needed |
| test_server | 73 | 2020-09-29 | 2023-06-13 | paired with update |
| version | 183 | 2021-08-20 | 2026-10-10 | per season, ~monthly |

- **update**, era A 2022-01-26..2023-06-13 (68 posts): fixed title
  【正式服更新公告】, weekly Tuesdays, date only in body ("服务器将于1月26日(周三)
  早上6:00-9:00进行停服维护"), numbered content list, 1-7 images; ex
  `61f009df849e` … `64871caf0595`.
- **update**, era B 2023-06-27..2026-10-28 (94 posts): dated title
  【M月D日更新公告】 (ex `6499a761c309`, `6adf31ceee00`); biweekly from 2023-06;
  weekday drifts Tue→Wed in 2024; window becomes "全球统一时间…上午6:00-9:00"
  from 2024-09 (`66f4dc4890b2`); 4-6 images (52 posts have exactly 6). Gaps
  are holidays (CNY 2024-02-06..03-05 28d, 2023-10 21d; the 2023-08-01 slot
  is `64c79c5924f5`, section 1).
- **maintenance**, 25 posts in six sub-families: scheduled-window-only
  (`6149f09bcb2d`…`619d1550b4e5`, window-extension `63e2faecc5a3`),
  postponement (`62026e71c3e1`, `624a77763408`), post-maintenance incident /
  hotfix openers (`62fc93f1849e`, `62fe1faa0c8d`, `6373652d0c8d`,
  `63c753787442`, `63eb98f90c8d`, `648847d3f2ff`, `65b8f5f46132`,
  `64bfa55847a0`), full-day shutdown (`638d42957442`), feature delay
  (`6189d895c470`), and delivery/closure/compensation notices (`64391dec8577`,
  `644650344988`, `642699cbf163`, `63c02c94849e`, `65a009c3c8bc`,
  `631814267442`, no-maintenance-this-week `65d32bb01a1a`). None after
  2024-02-20 — incident notices stop on this account.
- **test_server**: 2020-09..2021-09 five sporadic test-cycle posts
  (`5f72da02517f`, `5feb007012b8`, `600975188ec3`, `60d5c549f7fe`,
  `614c9a987fe2`); 2022-01-26..2023-06-13 the 【体验服更新公告】 series (66
  posts), published the same day as the 正式服 notice (66/73 share its
  notice_date); variants 【体验服公告】 `621ccc523408`, 【体验服维护公告】
  `6477500fed0e`.
- **version** by era: launch 2021-08/09 上线/预下载 notices (`611f13ad3408`,
  `6135b6273408`, `6136ff0f3408`, `61396bf4c3e1`); numbered seasons
  2021-09..2023-05 as ✦MA第N赛季前瞻✦ / ✦魔法觉醒第N赛季前瞻✦ (ex `617e9374cb2d`,
  `641ac680e7ee`) plus 开启 notices (`619fa102cb2d`, `61c9a389c5a3`); named
  seasons 2023-06..2024-02 (相逢霍格沃茨/魁地奇/龙研究/霍格莫德, ex
  `64a6c032909e`, `65251296b2de`); 2024-03.. ✦SN赛季·主题预告✦ (ex
  `6600fdd6cf6b`) plus serial 剧情序章 / 奖励总览 / 赛季预告（第一&二章）（第三&四章）/
  剧情完结纪念 / 发布会情报. One preview ~1-2 weeks before each month-start
  season date, chapter posts mid-season.

## 3. Distinguishing features

- **Title shapes (positives).** update: `^【正式服更新公告】$` or
  `^【\d{1,2}月\d{1,2}日更新公告】$`. test_server: `^【体验服(更新公告|维护公告|公告)】$`
  or 体验服 in title. maintenance: no fixed title — greeting openers
  (`^(各位)?亲爱的(巫师|同学们|玩家)[们]?[，：:]$`), self-describing window
  titles ("…服务器将于9月22日(周三)早上2:00-7:00进行停服维护…", `6149f09bcb2d`),
  or 【更新延后公告】/【本周暂停维护公告】/…停服公告. version: ✦-wrapped season
  titles with 赛季/版本 plus 前瞻|主题预告|内容预告|开启|上线|序章|总览|完结纪念.
- **Body markers.** The window sentence `服务器(和抢先体验服)?将于…(停服)?(维护|更新)`
  occurs in 159/162 update posts and 6/25 maintenance posts — necessary, not
  sufficient. "如未能按时完成，则开服时间将会顺延" + "维护期间将暂时无法进入服务器
  进行游戏" with no content list marks a window-only maintenance post; an
  update adds "以下为本次…更新的部分内容" or carries changes in images.
  Incident openers use 异常|闪退|修复|推迟|补偿; test_server bodies say
  抢先体验服. `#哈利波特新情报#` appears on positives and promos alike —
  not a signal.
- **type / image_count.** update is always type 2 (图文) or 6 (长文), never
  video; era A carries 1 long-article image, era B 4-6. maintenance is
  type 1/2/4/6 with 0-1 images (11 of 25 none). test_server is type 2 (71/73),
  1-3 images. version has 20 type-3 videos (season teasers), so type 3 alone
  does not exclude version; but unmatched videos are negatives per intent.
- **Hard negatives that fool a naive 更新/公告/维护 keyword match** (all
  event/other; keyword-only matching would select ~465 of the 1,881
  negatives). Title-level: ✦变身大乱斗·限时玩法更新✦ `69df45861139`,
  `69f1ac781fcf`; ✦活动更新速递✦ `62b2f7613408`/`62d761a5c3e1`;
  【场景更新预览-对角巷】 `60c1b5394ebd` (+5 siblings); ✦游戏图标更新提示✦
  `65bb7f265446`; ✦小组件更新…✦ `6914307f6af2`; 《…小米渠道角色资产找回公告》
  `69606128b595`; ✦致谢维护魔法世界原创权益的巫师✦ `693636cb5390`;
  【心动轮盘投票结束时间提示】 `6643078df3dd`. Body-level: 白色季节 event
  "将于12月1日维护后上线" `619fa0933408`; new-server "维护后-维护前开启预约"
  `62ce201dc3e1`; ✦全新服务器「戈德里克山谷」·即将开启✦ `68552afdd8c1`;
  赛季限定卡池 teasers `69c7718b6c8e` ff. (赛季 + 更新 in body); the ~60-post
  ✦学院杯·X今日开启✦ series (赛季积分 in body).

## 4. Decision procedure

Inputs per post: `type`, `title`, `text` (for type-6 stubs use the long-text
body, HTML tags stripped), `image_count`, `published_at`. Ordered, first match
wins; unmatched notice-shaped posts go to quarantine, never silently dropped.

```
T = title.strip(); B = stripped body; D = published_at date

R1  if T matches 卡池|时装|发型|配饰|礼盒|小组件|游戏图标|限时玩法|玩法上线|
       全新玩法|活动更新速递|月签到界面|场景更新预览|投票|返场|周边|生日|周年|
       抽签|转盘|图鉴|相机|赛事|活动预告|活动即将|今日开启|现已开启   -> event
R2  if T == 【正式服更新公告】 or T =~ ^【\d{1,2}月\d{1,2}日更新公告】$  -> update
R3  if T =~ ^【体验服(更新公告|维护公告|公告)】$ or T contains 体验服  -> test_server
    if D < 2022-01-01 and T is a greeting or contains 指引|下载
       and T+B[:150] contains 删档|预备测试|秘密测试|测试资格|测试结束|计费测试
       and B[:150] has no 抽奖|获奖|名单                              -> test_server
R4  if T in {【更新延后公告】,【本周暂停维护公告】} or 停服公告 in T
       or T contains 服务器将于…(停服)?(维护|更新) within 40 chars    -> maintenance
R4b if T contains 上线】 or 预下载:            # 2021 launch family
       B has 公测|全平台|定档|预下载 -> version
       B has 活动时间|礼包          -> event
       else                         -> quarantine
    if T matches 端(正式|N日)上线    -> version          # PC launch 2024-02
R5a if T contains 赛季 or 版本, and T matches 前瞻|主题预告|赛季预告|内容预告|
       即将开启|正式开启|明日开启|赛季开启|剧情完结纪念|即将上线|预习|怪谈|
       进行中|预览|情报|公布|放送:
       if T has 前瞻 and B[:80] has 礼盒|爆料 -> event   # single-item reveal
       else                                    -> version
R5b if T starts with ✦ and T has 内容预告|主题预告|前瞻
       and B[:300] has 赛季前瞻 or 新赛季                -> version
    if T has 发布会 or 情报                              -> quarantine  # split-label twins
R6  if B matches 服务器(和抢先体验服)?将于…(停服)?(维护|更新):
       if B has 如未能按时完成 and no 以下为本次|更新内容|登录游戏后生效
                                                -> maintenance   # window only
       else                                     -> update
R7  if T is a greeting opener (亲爱的巫师/同学们/玩家/…) or 写给巫师伙伴们的一封信:
       B has 处罚|公示|名单|获奖                             -> other
       B has 异常|闪退|修复|推迟|暂时关闭|补偿|延迟上线|顺延   -> maintenance
       B has 重新登录…生效|更新后生效|登录游戏后生效           -> update
       else                                              -> quarantine
R5c if T does not start with ✦✧【# and has no 公告
       and B[:1200] has 第N赛季 or 新赛季
       and B[:1200] has 开启|上线|前瞻|预告              -> version
R8  if T starts with 【                                   -> quarantine
    if T starts with ✦ or ✧:
       T has 剧情序章|奖励总览|徽章展示|内容预告|主题预告|赛季预告|剧情完结纪念|
       预习|怪谈|进行中|即将开启|正式开启                 -> quarantine   # season-serial, Q2
       else                                              -> event
    if 公告 in T or B has 服务器将于|停服|顺延|无法进入服务器|维护期间|
       紧急|补偿|补发|闪退|回档|删档                      -> quarantine
    if B[:150] has 全新系统|全新玩法|全新剧情|全新魔咒|赛季|情报|预告
       and B[:80] has no R1 word                         -> quarantine
    else                                                  -> other
```

R1 must run first: it kills every promo title before the season/notice rules
can fire. R2/R3 are exact-title rules and are error-free on this corpus.
Anything matched only by body keywords lands in quarantine, not in a positive
class.

## 5. Validation plan

Run the section-4 procedure over `corpus/dashen/posts.jsonl`, diff against
`study/labels.jsonl`. Measured 2026-10-28; re-runs must reproduce exactly:

- **Agreement: 405/443 positives (91.4%).** update 161/162 (`64c79c5924f5`
  intentionally maintenance, section 1); test_server 72/73 + 1 deferred
  (`5f72da02517f`); maintenance 21/25 + 4 deferred (`631814267442`,
  `63c02c94849e`, `65a009c3c8bc`, `642699cbf163`); version 151/183, 28
  deferred (the section-1 序章/总览 split, #哈利波特新情报# `61c9a389c5a3`/
  `61cdb2df`, letter `6465e275eba0`, 发布会情报 twins) and 4 misrouted
  (`61ef7afc7442`, `64966a4a2842`, `6609180cdd42` → event; `634e8e9b7442` →
  test_server).
- **False positives: 9/1881 negatives.** 8 are the launch-era 上线 twins
  (section 1, materially identical to positives `611f13ad3408`/`61396bf4c3e1`
  — suspected under-labeling, Q7); 1 is `62b574c10c8d` (suspected mislabel).
  Ruling the twins negative (gate R4b on published_at < 2021-10) drops FP
  to 1.
- **Selected set: 416 posts** (update 161, maintenance 23, version 159,
  test_server 73); **quarantine 226 (9.7%)**; no event/other post enters a
  positive class except the 9 above.
- **Must remain positives** (assert selected): update `61f009df849e`,
  `64871caf0595`, `6499a761c309`, `6adf31ceee00`; maintenance `6149f09bcb2d`,
  `62026e71c3e1`, `65d32bb01a1a`, `62fc93f1849e`; test_server `61effc933408`,
  `64871b4d7925`, `621ccc523408`, `6477500fed0e`; version `617e9374cb2d`,
  `6212486a0c8d`, `6600fdd6cf6b`, `6aca0cb0d96a`.
- **Must stay excluded** (assert event/other): `619fa0933408`, `62ce201dc3e1`,
  `69606128b595`, `69df45861139`, `60c1b5394ebd`, `693636cb5390`,
  `6643078df3dd`, `62b2f7613408`, `69c7718b6c8e`, `68552afdd8c1`, `61396eb8c3e1`,
  `6401dc891108`.
- **Series-integrity check:** update notice_dates must form the weekly→
  biweekly Tue/Wed sequence with only the known holiday gaps (2022-02 15d,
  2023-10 21d, 2024-02 28d); any new gap must land in quarantine.
- **Quarantine budget:** 9.7% of the corpus, human-reviewable once; target
  zero notice-shaped posts (【…公告】 titles, window sentences) outside
  selected+quarantine. After accepted section-1 label fixes, agreement rises
  to ~424/443 (95.7%).

## 6. Open questions for the human

1. **test_server**: keep the 73 体验服/测试 posts (series dead since
   2023-06) or drop from MVP selection? The scope note at the time listed only
   update/maintenance/version families as positives.
2. **Season-serial teasers** (✦X·剧情序章✦, ✦X赛季·奖励总览✦, 发布会情报; ~35
   posts 2023-10..2026-10): identical shape, labeler split across version and
   event. Include as "version previews" (procedure quarantines them) or
   exclude as promo? A yes converts 28 quarantined positives into version.
3. **【更新延后公告】 / feature-delay notices** (`62026e71c3e1`, `624a77763408`,
   `6189d895c470`): maintenance-family (current) or update-family?
4. **Window-less hotfixes** (`62f3b68a3408` card-balance emergency update):
   keep as update (current) or quarantine since they break the series shape?
5. **Ops notices without maintenance** (`63c02c94849e`, `65a009c3c8bc`,
   `631814267442`): confirm exclusion from the archive.
6. **【场景更新预览-*】** (`60c1b5394ebd` + 5, pre-OBT videos, labeled event):
   does intent's "version previews" include them?
7. **Launch-era twins** (9月9日上线 family, section 1): select all 10 or only
   the canonical 4? Affects FP count, not the series.
8. **Post-2024-02 incident notices**: the maintenance series simply stops;
   confirm the account stopped publishing them (monitor R7 quarantine inflow).

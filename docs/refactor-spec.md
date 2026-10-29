# 重构 spec：收敛到一条管线

状态：待执行（2026-10-30）
依据：[intent.md](intent.md)、[ADR-0002](adr/0002-primary-derived-data-boundary.md)、[CONTEXT.md](../CONTEXT.md)

## 1. 目标

仓库只保留一条线：**抓帖子 → OCR → 人工修正 → 生成文本版**。

MVP 范围冻结在 update 家族（162 篇 / 650 张图）。maintenance、version、test_server 已标注、后置；选择器和定时任务后置。

完成后：

- 活的目录只有 `posts / assets / ocr / selection / corrections / edition / study`；
- 命令只有五个：`posts`、`ocr`、`crop`、`verify`、`edition`；
- Python 从约 3,600 行降到约 2,100 行；
- CI 能证明：账本没坏、文本版可重建、已确认的错误字形为 0。

## 2. 领域模型

| 实体 | 是什么 | 主数据 | 位置 |
|---|---|---|---|
| Post | 账号发的一条帖子，唯一的一等记录 | 是 | `posts/dashen.jsonl` |
| Image | Post 的附件，身份 `(post_id, index)` | 是 | `assets/dashen/<id>/NN.ext` |
| Selection | 哪些 Post 是公告的判定 | 是 | `selection/dashen.jsonl` |
| Correction | 某个 block 应读作什么的判定 | 是 | `corrections/dashen/<id>.json` |
| OCR result | 引擎对 Image 的输出，带引擎指纹 | 否 | `ocr/dashen/<id>.json` |
| Edition | 阅读用文本 | 否 | `edition/` |
| Study | 选择方法的离线证据 | — | `study/` |

不变式：

- 派生层可以重建，主数据不行；
- 公告不是记录，是 Post 的角色；
- git 是修订历史，没有 revision 字段。

## 3. 目标目录与包

```
posts/
  dashen.jsonl              # 全部帖子，一行一条
  dashen.manifest.json      # 最后一次抓取
assets/
  dashen/<feed_id>/01.jpg   # 原始字节
selection/
  dashen.jsonl              # post_id → label / notice_date / confidence / evidence
  dashen.manifest.json      # 标注 prompt 版本、批次数、一致性数字
ocr/
  dashen/<feed_id>.json     # engine + images[].blocks[]
corrections/
  README.md                 # 账本格式 + 18 词审计清单
  dashen/<feed_id>.json     # 修正记录
edition/
  notices.jsonl             # 索引
  dashen/<feed_id>.md       # 每篇文本
study/
  prompt-label.md
  report.md
  positives.json
hpma/
  paths.py                  # REPO_ROOT + 各层路径
  http.py
  posts.py                  # 原 collectors/dashen.py
  ocr.py
  corrections.py            # 原 ocr_review.py 的 crop / verify
  edition.py                # 新增
  cli.py
tests/
docs/
CONTEXT.md
README.md
```

命令：

| 命令 | 作用 |
|---|---|
| `python -m hpma.cli posts` | 抓取/更新帖子流 |
| `python -m hpma.cli ocr --family update` | 下载图片 + OCR |
| `python -m hpma.cli crop <notice> <img> <block> [--scale 2]` | 生成证据图 |
| `python -m hpma.cli verify` | 校验账本 |
| `python -m hpma.cli edition [--check]` | 生成文本版 |

## 4. 删除清单

| 路径 | 规模 | 理由 |
|---|---|---|
| `collectors/model.py` | 70 行 | 没有任何代码产出 Notice 对象 |
| `collectors/store.py` | 184 行 | `data/` 从未存在 |
| `schema/` | 2.4 KB | 只服务 store |
| `tests/test_store.py` | 132 行 | 同上 |
| `tests/test_cli.py` | 23 行 | 测 index/validate |
| CLI `index` / `validate` | ~25 行 | 同上 |
| `jsonschema` 依赖 | 1 行 | 只被 store 用 |
| `derived/ocr/corrected/` | 153 文件 | 可由 ocr + corrections 重建 |
| `ocr_review.py apply` | ~40 行 | 同上 |
| `queue` / `batches` / `sheet` / `candidates` / `grep` | ~250 行 | 那轮 LLM 复核的脚手架，已完成 |
| `study/aggregate.py` + `make_batches.py` | 181 行 | 输入 `study/labels/` 已 gitignore，全新 clone 跑不起来；产出已提交 |
| `docs/mvp-plan.md` | 161 行 | 描述的是不存在的 `data/`、`collect.yml`、`validate --check-assets` |
| `checkpage/` 生成物 | 6.8 MB | 构建产物，加进 `.gitignore` |
| 两个复核页面 | ~3,000 行 | 移到 `legacy/`，冻结；职能边界待定 |

## 5. 改名

| 现在 | 之后 |
|---|---|
| `corpus/dashen/posts.jsonl` | `posts/dashen.jsonl` |
| `corpus/dashen/manifest.json` | `posts/dashen.manifest.json` |
| `derived/ocr/dashen/` | `ocr/dashen/` |
| `derived/ocr/corrections/` | `corrections/` |
| `study/labels.jsonl` | `selection/dashen.jsonl` |
| `study/labels-manifest.json` | `selection/dashen.manifest.json` |
| `collectors/dashen.py` | `hpma/posts.py` |
| `collectors/http.py` | `hpma/http.py` |
| `collectors/ocr.py` | `hpma/ocr.py` |
| `tools/ocr_review.py`（crop/verify 部分） | `hpma/corrections.py` |
| `tests/test_dashen.py` | `tests/test_posts.py` |
| `tests/test_ocr_review.py` | `tests/test_corrections.py` |

全部用 `git mv`，保留历史。

## 6. 数据修复

前提：OCR 会把稀有字认成常用字（驺→骆、缎→锻、嗅→噢、礅→墩），上一轮把这种偏差当成了「源图错字」写进账本。修复全部落在账本里，不直接改 `ocr/`。

### A 类：已确认，脚本全量修

| 错字 | 正确 | raw block | 涉及篇数 | 既有账本条目 |
|---|---|---|---|---|
| 骆吾 | 驺吾 | 49 | 19 | 7 条要改 |
| 噢幻草 | 嗅幻草 | 2 | 2 | 2 条要改 |

做法：扫 raw block，含错字的生成一条 correction（`original` = 原文，`corrected` = 替换后，`reason` = "OCR 稀有字误识"，`confidence` = high，`recrop` = 对应 crop 命令）；已有条目就地改。

验收：edition 文本里这两个错字计数为 0。

### B 类：未确认，crop 复核

| 词 | raw block | 既有账本条目 |
|---|---|---|
| 石墩 / 石礅 | 6 | 12 条（把「石出动」补成「石墩出动」的） |
| 金锻 / 金缎 | 6 | 1 条 |
| 银辉之匙 / 钥 | 3 | 0 |
| 子立 / 孑立 | 5 | 0 |
| 戈德利克 / 德里克 | 1 | 0 |
| 鲁本 / 鲁伯 | 1 | 0 |
| 长跑 / 长袍 | 1 | 0 |
| 云雾飘渺 / 缥缈 | 4 | 0 |
| 凤吟凰鸣 / 凰吟凤鸣 | 1 | 0 |
| 讽爽英姿 / 飒爽英姿 | 1 | 0 |
| 进发出 / 迸发出 | 3 | 0 |
| 换新款 / 焕新款 | 1 | 0 |
| 等级上线 / 上限 | 1 | 0 |
| 补获 / 捕获 | 1 | 0 |
| 无暇信念 / 无瑕信念 | 1 | 0 |
| 登陆 / 登录 | 4 | 0 |

做法：每个 block 用 `crop --scale 2`（或 `--target-width 2000`）出图，看图定字，改账本。石墩是合法词，可能混着真的石墩；登陆/登录可能是官方原文的异体，也可能只是 OCR 偏差——都以图为准。

验收：edition 文本里每个词的两种写法计数与审计结论一致；结论写进 `corrections/README.md`。

注意：18 词清单现在只存在于 `build_correction_checkpage.py` 的 `TERMS` 里。页面冻结前先把清单搬进 `corrections/README.md`，否则清单会随页面一起被冻结。

## 7. 文本版格式

`edition/dashen/<feed_id>.md`：

```md
---
id: dashen.<feed_id>
title: 【10月28日更新公告】
published_at: 2026-10-26T18:56:00+08:00
notice_date: 2026-10-28
url: https://ds.163.com/feed/<feed_id>/
images:
  - assets/dashen/<feed_id>/01.jpg
  - assets/dashen/<feed_id>/02.jpg
---

## 正文

（Post 的 body text；没有就省略这一节）

## 图片文字

### 图 1

（校正后的 block 文本，一行一个）
```

`edition/notices.jsonl` 一行一条：

```json
{"id":"dashen.<feed_id>","title":"...","published_at":"...","notice_date":"...","url":"...","images":["assets/..."],"text":"..."}
```

规则：按 `published_at` 排序；无时间戳；`--check` 比较提交版本。只生成 `selection` 里 `label == update` 的篇目。

## 8. 执行顺序

每步一个提交，每步结束 `pytest -q` 绿。

1. **文档层**（不碰代码）：CONTEXT.md、ADR-0002、本 spec、intent 改写（Data model / Acceptance / MVP 范围）。
2. **布局**：建 `hpma/` 包，`git mv` 目录，删 store / model / schema / corrected / apply，测试改 import，页面移到 `legacy/`。
3. **文本版**：写 `hpma/edition.py` + 测试，生成 `edition/`。
4. **数据修复**：A 脚本 + B crop 复核，改账本，重新生成 edition。
5. **清理**：一次性命令、study 脚本、mvp-plan、`checkpage/` 进 `.gitignore`、README 重写。
6. **CI**：加 verify + edition --check + 断言。

## 9. CI 门禁

```
pip install -r requirements.txt   # requests, pytest
pytest -q
python -m hpma.cli verify
python -m hpma.cli edition --check
```

测试覆盖：

- `test_posts`：页面解析、标题回退、分页去重、重跑零 diff；
- `test_ocr`：下载、缓存、失败隔离、幂等；
- `test_corrections`：verify 拒绝过期 original / 重复 locator；crop 几何；
- `test_edition`：格式、确定性、错误字形断言；
- `test_selection`：每个选中 id 在 posts 里存在；每篇公告有 ocr 和图片。

整体验收：

- 全新 clone 不需要网络、不需要 OCR 引擎，能跑 pytest + verify + edition --check；
- 162 篇 edition 生成，`--check` 零 diff；
- 账本 verify 通过；
- A 类错误字形在 edition 里为 0；
- Python ≤ 2,200 行。

## 10. 延后事项

- **复核页面的职能边界**（用户明确后置）：页面在 `legacy/` 冻结，重构后不可运行是预期；要复核时用 `crop`。
- **选择器**：update-only 的 R2/R6 规则在 report 里已验证，约 80 行；等自动化边界定了再做。
- **Actions / 增量采集**。
- **maintenance / version / test_server 家族**。
- **术语归一化**：搜索站的事，不进 archive。
- **terms.json 映射层**：撤回，不做。

## 11. 风险

- B 类审计可能发现更多 OCR 混淆；清单不封闭，发现新词就加进去，不要为了「按计划完成」放过。
- `git mv` 保留历史；写文件统一 `newline="\n"`。
- `edition --check` 只读 posts / ocr / corrections，不读图片，CI 里不受 351 MB assets 影响。
- 删除 `study` 脚本后，`labels.jsonl` 的来源只剩 `report.md` + `prompt-label.md` + `positives.json` 三份证据。

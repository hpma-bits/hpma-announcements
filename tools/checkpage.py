"""Static checkpage: every update notice's full text, searchable and sortable.

The page is generated from primary data, not from ``dataset/notices.jsonl``: a
Dashen row shows the post's own body **and** the image text (OCR + correction
ledger), so a search covers the complete notice. NGA rows show the repost's
body. The page is a local review artifact, gitignored; rebuild it with
``python -m tools.cli checkpage``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import archive, projection, text

CHECKPAGE_PATH = Path("checkpage/index.html")
DATA_PLACEHOLDER = "__CHECKPAGE_DATA__"

Record = dict[str, Any]


def _rows(root: Path | None = None) -> list[Record]:
    """Every update notice with body, per-image text and canonical flags."""
    base = Path(root) if root else archive.REPO_ROOT
    rows: list[Record] = []
    for source in archive.SOURCES.values():
        if not (base / source.corpus).is_file() or not (base / source.selection).is_file():
            continue
        for post, selection in archive.selected_posts(base, "update", source.name):
            feed = post["id"]
            notice = archive.load_ocr(feed, base) if source.ocr else None
            ledger = archive.load_ledger(feed, base) if source.ocr else None
            sections = projection.effective_sections(notice, ledger) if notice else []
            body = text.normalize_body(post.get("text") or "")
            image_text = "\n".join(line for _, lines in sections for line in lines)
            rows.append(
                {
                    "id": archive.notice_id(source.name, feed),
                    "source": source.name,
                    "title": post.get("title") or "",
                    "notice_date": selection.get("notice_date") or "",
                    "published_at": post.get("published_at") or "",
                    "url": archive.post_url(post),
                    "body": body,
                    "image_text": image_text,
                    "sections": [
                        {"image": index, "lines": lines} for index, lines in sections
                    ],
                    "images": [
                        image["path"]
                        for image in (notice or {}).get("images", [])
                        if image.get("path")
                    ],
                }
            )
    rows.sort(key=lambda row: (row["published_at"], row["id"]))
    projection.assign_canonical(rows)
    return rows


def build(root: str | Path | None = None) -> str:
    """The complete checkpage HTML, with the rows embedded as JSON."""
    payload = json.dumps(_rows(Path(root) if root else None), ensure_ascii=False, separators=(",", ":"))
    payload = (
        payload.replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return TEMPLATE.replace(DATA_PLACEHOLDER, payload)


def check(root: str | Path | None = None, out: str | Path | None = None) -> list[str]:
    """Problems that mean the generated page is missing or stale."""
    base = Path(root) if root else archive.REPO_ROOT
    target = Path(out) if out else base / CHECKPAGE_PATH
    if not target.is_file():
        return [f"missing {target.as_posix()}"]
    if target.read_text(encoding="utf-8") != build(base):
        return [f"stale {target.as_posix()}"]
    return []


def write(root: str | Path | None = None, out: str | Path | None = None) -> Path:
    """Write the page; return its path."""
    base = Path(root) if root else archive.REPO_ROOT
    target = Path(out) if out else base / CHECKPAGE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build(base), encoding="utf-8", newline="\n")
    return target


TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HPMA 更新公告全文 checkpage</title>
<style>
:root {
  --bg: #f6f4ef;
  --paper: #fffdf9;
  --ink: #22201c;
  --muted: #6b665d;
  --line: #ded8cc;
  --accent: #6b4f2a;
  --mark: #ffe58a;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
}
.wrap { max-width: 1100px; margin: 0 auto; padding: 0 20px; }
.top {
  position: sticky;
  top: 0;
  z-index: 20;
  background: rgba(246, 244, 239, .94);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line);
}
.top .wrap { padding-top: 18px; padding-bottom: 12px; }
h1 { margin: 0; font-size: 22px; letter-spacing: .01em; }
h1 .accent { color: var(--accent); }
.lede { margin: 8px 0 0; color: var(--muted); font-size: 13px; line-height: 1.6; }
.lede b { color: var(--ink); }
.stats { margin-top: 10px; color: var(--muted); font-size: 12px; }
.stats span { display: inline-block; margin-right: 14px; }
.stats b { color: var(--ink); font-variant-numeric: tabular-nums; }
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 10px;
}
label { font-size: 12px; color: var(--muted); }
input[type="search"], select, button {
  font: inherit;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  color: var(--ink);
  padding: 7px 10px;
}
input[type="search"] { width: min(430px, 70vw); font-size: 14px; }
select, button { font-size: 13px; }
button { cursor: pointer; }
button:hover { border-color: var(--accent); color: var(--accent); }
.check { display: inline-flex; align-items: center; gap: 5px; }
.hint { margin-top: 6px; color: var(--muted); font-size: 12px; }
.list { padding: 16px 0 40px; }
.notice {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 16px 18px;
  margin: 14px 0;
  box-shadow: 0 1px 2px rgba(0, 0, 0, .03);
}
.head { display: flex; gap: 14px; align-items: flex-start; }
.date {
  min-width: 104px;
  font-size: 16px;
  font-weight: 700;
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}
.titles { flex: 1; min-width: 0; }
.titles h2 { margin: 0 0 6px; font-size: 17px; line-height: 1.4; }
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  align-items: center;
  color: var(--muted);
  font-size: 12px;
}
.id { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; }
.badge {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 999px;
  border: 1px solid;
  font-size: 11px;
  line-height: 1.5;
}
.badge.dashen { color: #1d4ed8; border-color: #bfdbfe; background: #eff6ff; }
.badge.nga { color: #b45309; border-color: #fde68a; background: #fffbeb; }
.badge.canonical { color: #166534; border-color: #bbf7d0; background: #f0fdf4; }
a { color: var(--accent); }
details { margin-top: 10px; }
summary {
  cursor: pointer;
  color: var(--accent);
  font-size: 12px;
  user-select: none;
}
section h3 {
  margin: 14px 0 6px;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: .08em;
  text-transform: uppercase;
}
section h4 { margin: 10px 0 4px; color: var(--muted); font-size: 12px; }
.text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: "Songti SC", "SimSun", "Noto Serif SC", serif;
  font-size: 15px;
  line-height: 1.8;
}
.empty-text { color: var(--muted); font-style: italic; }
mark { background: var(--mark); color: inherit; padding: 0 1px; border-radius: 2px; }
.empty { padding: 48px 0; text-align: center; color: var(--muted); }
.foot { color: var(--muted); font-size: 12px; line-height: 1.6; padding: 0 0 60px; }
.foot code { font-family: ui-monospace, Consolas, monospace; }
@media (max-width: 640px) {
  .head { flex-direction: column; gap: 2px; }
  .date { min-width: 0; }
  input[type="search"] { width: 100%; }
}
@media print {
  .top { position: static; }
  .controls, .hint, .stats { display: none; }
  .notice { break-inside: avoid; box-shadow: none; }
}
</style>
</head>
<body>
<header class="top">
  <div class="wrap">
    <h1>HPMA 更新公告全文 <span class="accent">checkpage</span></h1>
    <p class="lede">全部文本形式的更新公告。Dashen 行是 <b>post 正文 + 图片文字（OCR + 人工账本）</b>；NGA 行是转载正文。检索为客户端子串匹配，排序按公告日期或发布时间。</p>
    <div id="stats" class="stats"></div>
    <div class="controls">
      <label>检索
        <input id="q" type="search" placeholder="任意字符串，如：停服维护、光辉、2026-10-28" autocomplete="off" spellcheck="false">
      </label>
      <label>排序
        <select id="sort">
          <option value="date-desc">公告日期 ↓（新→旧）</option>
          <option value="date-asc">公告日期 ↑（旧→新）</option>
          <option value="pub-desc">发布时间 ↓</option>
          <option value="pub-asc">发布时间 ↑</option>
        </select>
      </label>
      <label>来源
        <select id="source">
          <option value="all">全部</option>
          <option value="dashen">Dashen</option>
          <option value="nga">NGA</option>
        </select>
      </label>
      <label class="check"><input id="canonical" type="checkbox"> 只看 canonical</label>
      <button id="toggle" type="button">折叠全部</button>
      <button id="clear" type="button">清空</button>
    </div>
    <div class="hint"><span id="count"></span> · 子串匹配，不区分英文大小写 · <span id="range"></span></div>
  </div>
</header>
<main class="wrap">
  <div id="list" class="list"></div>
  <p id="empty" class="empty" hidden>没有匹配的公告。</p>
  <p class="foot">数据来自 <code>corpus/</code> + <code>corrections/</code> + <code>derived/ocr/</code>，由 <code>python -m tools.cli checkpage</code> 生成。41 行是同日期另一来源，保留以便对照；勾选 canonical 只看优先阅读版本。</p>
</main>
<script>
const NOTICES = __CHECKPAGE_DATA__;
const $ = (id) => document.getElementById(id);
const list = $('list');
const q = $('q');
const sort = $('sort');
const source = $('source');
const canonical = $('canonical');
const count = $('count');
const range = $('range');
const empty = $('empty');
const toggle = $('toggle');
const clear = $('clear');

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ESC[ch]);
const hay = (row) => [
  row.id, row.source, row.title, row.notice_date, row.published_at, row.url,
  row.body, row.image_text,
].join('\\n').toLowerCase();
for (const row of NOTICES) row._hay = hay(row);

function highlight(value, needle) {
  const raw = String(value ?? '');
  if (!needle) return esc(raw);
  const lower = raw.toLowerCase();
  let out = '';
  let index = 0;
  while (index < raw.length) {
    const found = lower.indexOf(needle, index);
    if (found < 0) {
      out += esc(raw.slice(index));
      break;
    }
    out += esc(raw.slice(index, found));
    out += '<mark>' + esc(raw.slice(found, found + needle.length)) + '</mark>';
    index = found + needle.length;
  }
  return out || esc(raw);
}

function textBlock(value, needle) {
  const raw = String(value ?? '');
  if (!raw) return '<div class="text empty-text">（无）</div>';
  return '<div class="text">' + highlight(raw, needle) + '</div>';
}

function sortRows(rows, mode) {
  const byPublished = mode.startsWith('pub');
  const descending = mode.endsWith('desc');
  rows.sort((a, b) => {
    const keyA = byPublished
      ? a.published_at + '\\u0000' + a.notice_date + '\\u0000' + a.id
      : a.notice_date + '\\u0000' + a.published_at + '\\u0000' + a.id;
    const keyB = byPublished
      ? b.published_at + '\\u0000' + b.notice_date + '\\u0000' + b.id
      : b.notice_date + '\\u0000' + b.published_at + '\\u0000' + b.id;
    const order = keyA < keyB ? -1 : keyA > keyB ? 1 : 0;
    return descending ? -order : order;
  });
  return rows;
}

function rowHtml(row, needle) {
  const badges = '<span class="badge ' + esc(row.source) + '">' + esc(row.source) + '</span>'
    + (row.canonical ? '<span class="badge canonical">canonical</span>' : '');
  const images = row.images && row.images.length
    ? '<span>图片 ' + row.images.length + ' 张</span>'
    : '';
  const body = row.body
    ? '<section><h3>正文</h3>' + textBlock(row.body, needle) + '</section>'
    : '';
  let imageText = '';
  if (row.sections && row.sections.length) {
    imageText += '<section><h3>图片文字</h3>';
    for (const section of row.sections) {
      imageText += '<h4>图 ' + esc(section.image) + '</h4>'
        + textBlock((section.lines || []).join('\\n'), needle);
    }
    imageText += '</section>';
  }
  const link = row.url
    ? '<a href="' + esc(row.url) + '" target="_blank" rel="noopener">原文 ↗</a>'
    : '';
  return '<article class="notice">'
    + '<div class="head">'
    + '<div class="date">' + esc(row.notice_date || '—') + '</div>'
    + '<div class="titles">'
    + '<h2>' + highlight(row.title, needle) + '</h2>'
    + '<div class="meta">' + badges
    + '<span class="id">' + esc(row.id) + '</span>'
    + '<span>发布 ' + esc(row.published_at || '—') + '</span>'
    + images + link
    + '</div></div></div>'
    + '<details open><summary>' + (row.source === 'dashen' ? '正文 + 图片文字' : '正文')
    + '</summary>' + body + imageText + '</details>'
    + '</article>';
}

function stats() {
  const dashen = NOTICES.filter((row) => row.source === 'dashen').length;
  const nga = NOTICES.filter((row) => row.source === 'nga').length;
  const canonicalCount = NOTICES.filter((row) => row.canonical).length;
  const chars = NOTICES.reduce((sum, row) => sum + (row.body || '').length + (row.image_text || '').length, 0);
  $('stats').innerHTML = '<span><b>' + NOTICES.length + '</b> 篇</span>'
    + '<span>Dashen <b>' + dashen + '</b></span>'
    + '<span>NGA <b>' + nga + '</b></span>'
    + '<span>canonical <b>' + canonicalCount + '</b></span>'
    + '<span><b>' + chars.toLocaleString('zh-CN') + '</b> 字符</span>';
  const dates = NOTICES.map((row) => row.notice_date).filter(Boolean).sort();
  if (dates.length) {
    range.textContent = '公告日期 ' + dates[0] + ' 至 ' + dates[dates.length - 1];
  }
}

let collapsed = false;

function render() {
  const needle = q.value.trim().toLowerCase();
  const selectedSource = source.value;
  const onlyCanonical = canonical.checked;
  let rows = NOTICES.filter((row) => {
    if (selectedSource !== 'all' && row.source !== selectedSource) return false;
    if (onlyCanonical && !row.canonical) return false;
    if (needle && !row._hay.includes(needle)) return false;
    return true;
  });
  rows = sortRows(rows, sort.value);
  list.innerHTML = rows.map((row) => rowHtml(row, needle)).join('');
  empty.hidden = rows.length !== 0;
  count.textContent = '显示 ' + rows.length + ' / ' + NOTICES.length + ' 篇';
  toggle.textContent = collapsed ? '展开全部' : '折叠全部';
  if (collapsed) {
    for (const details of list.querySelectorAll('details')) details.open = false;
  }
}

let timer = null;
q.addEventListener('input', () => {
  clearTimeout(timer);
  timer = setTimeout(render, 120);
});
sort.addEventListener('change', render);
source.addEventListener('change', render);
canonical.addEventListener('change', render);
toggle.addEventListener('click', () => {
  collapsed = !collapsed;
  for (const details of list.querySelectorAll('details')) details.open = !collapsed;
  toggle.textContent = collapsed ? '展开全部' : '折叠全部';
});
clear.addEventListener('click', () => {
  q.value = '';
  source.value = 'all';
  canonical.checked = false;
  sort.value = 'date-desc';
  render();
  q.focus();
});
document.addEventListener('keydown', (event) => {
  if (event.key === '/' && document.activeElement !== q) {
    event.preventDefault();
    q.focus();
  }
});

stats();
render();
</script>
</body>
</html>
"""

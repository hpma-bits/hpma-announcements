/* Correction-review checkpage: term decisions + item verification. */
(() => {
  'use strict';

  const DATA = window.CORRECTION_REVIEW_DATA;
  const cardsEl = document.getElementById('cards');
  const emptyEl = document.getElementById('empty');
  const searchEl = document.getElementById('search');
  const toastEl = document.getElementById('toast');
  const importFileEl = document.getElementById('import-file');
  const STORAGE_KEY = 'hpma-correction-review-v1';
  const PAGE_SIZE = 80;

  if (!DATA || !Array.isArray(DATA.terms) || !Array.isArray(DATA.items)) {
    document.getElementById('view-title').textContent = '没有可复核的数据';
    document.getElementById('view-desc').textContent = '请先运行 python tools/build_correction_checkpage.py';
    return;
  }

  const VIEWS = {
    terms: {
      title: '术语对照',
      desc: '源图与干净 corpus 写法不一致；逐项决定保留源图、统一为 corpus，或写其他形式。',
    },
    low: {
      title: '低置信修正',
      desc: 'medium 置信度的逐条修正；每一条都附源图局部，请对照图片确认。',
    },
    watermark: {
      title: '水印重建',
      desc: '正文与「大神@哈利波特魔法觉醒」水印合并的 OCR 行，按源图重建正文。',
    },
    rewrite: {
      title: '整行重写',
      desc: '修正前后长度变化 ≥ 8 字的整行重写；风险最高，建议逐条看。',
    },
    all: {
      title: '全部修正',
      desc: '本轮已接受的全部 OCR 修正，用于抽查或按公告检索。',
    },
    reviewed: {
      title: '已确认',
      desc: '已经做出决定的术语与逐条修正。',
    },
  };
  const FILTERS = Object.keys(VIEWS);
  const PRIORITY = { high: '高优先', medium: '中优先', low: '低优先' };
  const RECOMMEND = { corpus: '建议按 corpus', source: '建议保留源图', review: '需人工判断' };
  const RECOMMEND_CLASS = { corpus: 'gold', source: 'ok', review: 'low' };
  const TERM_DECISIONS = {
    keep_source: '保留源图',
    use_corpus: '按 corpus',
    other: '其他',
  };
  const ITEM_DECISIONS = {
    accept: '接受修正',
    keep: '保留原文',
    other: '其他',
  };

  const state = { filter: 'terms', query: '', limit: PAGE_SIZE };
  if (new URLSearchParams(location.search).has('reset')) {
    try { localStorage.removeItem(STORAGE_KEY); } catch { /* storage unavailable */ }
    try { history.replaceState(null, '', location.pathname); } catch { /* file:// */ }
  }
  let review = loadReview();
  let saveTimer = null;

  /* ---------- DOM helpers ---------- */

  function h(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'style') node.style.cssText = value;
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on') && typeof value === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children) {
      if (child == null || child === false) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const fmt = new Intl.NumberFormat('zh-CN');
  const fmtDate = (iso) => (iso || '').slice(0, 10);
  const pad2 = (value) => String(value).padStart(2, '0');
  const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  function toast(message) {
    toastEl.textContent = message;
    toastEl.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toastEl.classList.remove('show'), 2200);
  }

  /* ---------- persistence ---------- */

  function blankReview() {
    return { version: DATA.version || 1, terms: {}, items: {} };
  }

  function loadReview() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return blankReview();
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return blankReview();
      return {
        version: parsed.version || 1,
        terms: parsed.terms && typeof parsed.terms === 'object' ? parsed.terms : {},
        items: parsed.items && typeof parsed.items === 'object' ? parsed.items : {},
      };
    } catch {
      return blankReview();
    }
  }

  function persist(immediate = false) {
    clearTimeout(saveTimer);
    const write = () => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(review));
      } catch {
        toast('浏览器禁止本地存储，请用「导出复核结果」保存');
      }
    };
    if (immediate) write();
    else saveTimer = setTimeout(write, 300);
  }

  function ensureTerm(id) {
    return review.terms[id] || (review.terms[id] = { decision: '', text: '', note: '', status: 'pending' });
  }

  function ensureItem(key) {
    return review.items[key] || (review.items[key] = { decision: '', text: '', note: '', status: 'pending' });
  }

  function isReviewed(entry) {
    return !!(entry && entry.status === 'confirmed' && entry.decision);
  }

  function termReviewed(term) {
    return isReviewed(review.terms[term.id]);
  }

  function itemReviewed(item) {
    return isReviewed(review.items[item.key]);
  }

  function reviewedCount() {
    let count = 0;
    for (const term of DATA.terms) if (termReviewed(term)) count += 1;
    const seen = new Set();
    for (const item of DATA.items) {
      if (!seen.has(item.key) && flagged(item) && itemReviewed(item)) count += 1;
      seen.add(item.key);
    }
    return count;
  }

  function reviewableCount() {
    let count = DATA.terms.length;
    const seen = new Set();
    for (const item of DATA.items) {
      if (!flagged(item)) continue;
      if (!seen.has(item.key)) count += 1;
      seen.add(item.key);
    }
    return count;
  }

  function flagged(item) {
    return item.categories.includes('low') || item.categories.includes('watermark') || item.categories.includes('rewrite');
  }

  /* ---------- text helpers ---------- */

  function bbox(box) {
    if (!Array.isArray(box) || !box.length) return null;
    const xs = box.map((point) => Number(point[0]));
    const ys = box.map((point) => Number(point[1]));
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  function cropNode(item, eager = false) {
    const box = bbox(item.box);
    if (!box || !item.width || !item.height) {
      return h('div', { class: 'crop' }, h('img', { src: `../${item.imagePath || ''}`, alt: '', loading: eager ? 'eager' : 'lazy' }));
    }
    const padX = 40;
    const padY = 30;
    const left = Math.max(0, box[0] - padX);
    const top = Math.max(0, box[1] - padY);
    const right = Math.min(item.width, box[2] + padX);
    const bottom = Math.min(item.height, box[3] + padY);
    const cw = Math.max(1, right - left);
    const ch = Math.max(1, bottom - top);
    const img = h('img', { src: `../${item.imagePath}`, alt: '', loading: eager ? 'eager' : 'lazy' });
    img.style.width = `${(item.width / cw) * 100}%`;
    img.style.left = `${-(left / cw) * 100}%`;
    img.style.top = `${-(top / ch) * 100}%`;
    return h('div', { class: 'crop', style: `aspect-ratio:${cw}/${ch}` }, img);
  }

  function commonParts(a, b) {
    let prefix = 0;
    while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix += 1;
    let suffix = 0;
    while (
      suffix < a.length - prefix &&
      suffix < b.length - prefix &&
      a[a.length - 1 - suffix] === b[b.length - 1 - suffix]
    ) suffix += 1;
    return {
      pre: a.slice(0, prefix),
      mid: a.slice(prefix, a.length - suffix),
      suf: a.slice(a.length - suffix),
    };
  }

  function markedNode(text, other) {
    const parts = commonParts(text, other);
    const span = h('span');
    span.append(parts.pre);
    if (parts.mid) span.append(h('mark', {}, parts.mid));
    span.append(parts.suf);
    return span;
  }

  function highlightNode(text, terms) {
    const span = h('span');
    let rest = text || '';
    while (rest) {
      let earliest = -1;
      let match = '';
      for (const term of terms) {
        if (!term) continue;
        const index = rest.indexOf(term);
        if (index >= 0 && (earliest < 0 || index < earliest)) {
          earliest = index;
          match = term;
        }
      }
      if (earliest < 0) {
        span.append(rest);
        break;
      }
      span.append(rest.slice(0, earliest));
      span.append(h('mark', {}, match));
      rest = rest.slice(earliest + match.length);
    }
    return span;
  }

  /* ---------- filters ---------- */

  function matchesTerm(term) {
    const query = state.query.trim().toLowerCase();
    if (!query) return true;
    return [
      term.label,
      term.sourceForm,
      term.targetForm,
      term.note,
      ...term.examples.map((example) => `${example.title}\n${example.original}\n${example.corrected}`),
    ].join('\n').toLowerCase().includes(query);
  }

  function matchesItem(item) {
    const query = state.query.trim().toLowerCase();
    if (!query) return true;
    return [
      item.title,
      item.noticeId,
      item.original,
      item.corrected,
      item.reason,
      item.publishedAt,
    ].join('\n').toLowerCase().includes(query);
  }

  function reviewedCards() {
    const cards = [];
    for (const term of DATA.terms) {
      if (termReviewed(term) && matchesTerm(term)) cards.push({ kind: 'term', data: term });
    }
    for (const item of DATA.items) {
      if (flagged(item) && itemReviewed(item) && matchesItem(item)) cards.push({ kind: 'item', data: item });
    }
    return cards;
  }

  function currentCards() {
    if (state.filter === 'terms') {
      return DATA.terms.filter(matchesTerm).map((term) => ({ kind: 'term', data: term }));
    }
    if (state.filter === 'reviewed') return reviewedCards();
    return DATA.items
      .filter((item) => item.categories.includes(state.filter) && matchesItem(item))
      .map((item) => ({ kind: 'item', data: item }));
  }

  /* ---------- rendering ---------- */

  function updateProgress() {
    const total = reviewableCount();
    const done = reviewedCount();
    const pct = total ? Math.round((done / total) * 100) : 0;
    document.getElementById('progress-label').textContent = `已确认 ${done} / ${total}`;
    document.getElementById('progress-pct').textContent = `${pct}%`;
    const bar = document.querySelector('.progress-bar');
    bar.setAttribute('aria-valuenow', String(pct));
    document.getElementById('progress-fill').style.width = `${pct}%`;
    document.querySelector('[data-count="reviewed"]').textContent = fmt.format(done);
  }

  function updateCounts() {
    const counts = {
      terms: DATA.terms.length,
      low: DATA.items.filter((item) => item.categories.includes('low')).length,
      watermark: DATA.items.filter((item) => item.categories.includes('watermark')).length,
      rewrite: DATA.items.filter((item) => item.categories.includes('rewrite')).length,
      all: DATA.items.length,
    };
    for (const [key, value] of Object.entries(counts)) {
      const el = document.querySelector(`[data-count="${key}"]`);
      if (el) el.textContent = fmt.format(value);
    }
    updateProgress();
  }

  function renderTermCard(term) {
    const entry = review.terms[term.id];
    const card = h('article', { class: 'card', dataset: { termId: term.id } });
    const head = h('div', { class: 'card-head' },
      h('div', {},
        h('div', { class: 'card-title' }, term.label),
        h('div', { class: 'card-sub' },
          `源图/修正层 ${fmt.format(term.sourceBlocks)} 块 · ${fmt.format(term.sourceNotices)} 篇`,
        ),
      ),
      h('div', { class: 'badges' },
        h('span', { class: `badge ${RECOMMEND_CLASS[term.recommendation] || ''}` }, RECOMMEND[term.recommendation] || '需人工判断'),
        h('span', { class: 'badge' }, PRIORITY[term.priority] || term.priority),
      ),
    );
    card.append(head);
    card.append(h('div', { class: 'term-stats' },
      h('span', {}, 'corpus：'),
      h('b', {}, term.targetForm), ` ${fmt.format(term.corpusTarget)} 处`,
      h('span', { class: 'muted' }, ' · '),
      h('b', {}, term.sourceForm), ` ${fmt.format(term.corpusSource)} 处`,
    ));
    card.append(h('p', { class: 'term-note' }, term.note));

    if (term.examples.length) {
      const examples = h('div', { class: 'examples' });
      for (const example of term.examples) {
        const box = h('div', { class: 'example' });
        box.append(cropNode(example, true));
        box.append(h('div', { class: 'example-meta' },
          `${fmtDate(example.publishedAt)} · ${example.title} · img${example.imageIndex} b${example.blockIndex}`,
        ));
        box.append(h('div', { class: 'example-text' },
          example.corrected,
          example.original !== example.corrected
            ? h('div', { class: 'orig' }, `OCR：${example.original}`)
            : null,
        ));
        examples.append(box);
      }
      card.append(examples);
    }

    if (term.corpusSnippets.length) {
      const snippets = h('div', { class: 'snippets' });
      snippets.append(h('div', { class: 'line-label' }, '干净 corpus 用例'));
      for (const snippet of term.corpusSnippets) {
        snippets.append(h('p', {}, highlightNode(snippet, [term.targetForm, term.sourceForm])));
      }
      card.append(snippets);
    } else {
      card.append(h('div', { class: 'snippets' }, '干净 corpus 未出现该词；该写法仅来自源图。'));
    }

    const decision = h('div', { class: 'decision' });
    for (const [value, label] of Object.entries(TERM_DECISIONS)) {
      const button = h('button', { type: 'button', class: entry && entry.decision === value ? 'active' : '' }, label);
      button.addEventListener('click', () => setDecision('term', term.id, value));
      decision.append(button);
    }
    const other = h('input', {
      type: 'text',
      placeholder: '其他写法…',
      value: entry && entry.text ? entry.text : '',
      hidden: !(entry && entry.decision === 'other'),
    });
    other.addEventListener('input', () => updateText('term', term.id, other.value));
    decision.append(other);
    decision.append(h('span', { class: `status${entry && isReviewed(entry) ? ' confirmed' : ''}` },
      entry && isReviewed(entry) ? '已确认' : '待定',
    ));
    card.append(decision);
    card.append(h('div', { class: 'note' },
      h('textarea', {
        placeholder: '备注：为什么保留 / 为什么统一…',
        oninput: (event) => updateNote('term', term.id, event.target.value),
      }, entry && entry.note ? entry.note : ''),
    ));
    return card;
  }

  function renderItemCard(item) {
    const entry = review.items[item.key];
    const notice = DATA.notices[item.noticeId] || { title: item.title, publishedAt: item.publishedAt, corpusText: '' };
    const card = h('article', { class: 'card', dataset: { key: item.key } });
    const badges = h('div', { class: 'badges' });
    if (item.categories.includes('low')) badges.append(h('span', { class: 'badge low' }, '低置信'));
    if (item.categories.includes('watermark')) badges.append(h('span', { class: 'badge water' }, '水印重建'));
    if (item.categories.includes('rewrite')) badges.append(h('span', { class: 'badge rewrite' }, '整行重写'));
    for (const termId of item.termRefs) {
      const term = DATA.terms.find((candidate) => candidate.id === termId);
      if (term) badges.append(h('span', { class: 'badge gold' }, term.label));
    }
    const head = h('div', { class: 'card-head' },
      h('div', {},
        h('div', { class: 'card-title' }, notice.title || item.title),
        h('div', { class: 'card-sub' },
          `${fmtDate(item.publishedAt)} · img${item.imageIndex} b${item.blockIndex} · ${item.confidence || '—'}`,
        ),
      ),
      badges,
    );
    card.append(head);

    const body = h('div', { class: 'item-body' });
    body.append(cropNode(item));
    const text = h('div', { class: 'item-text' });
    text.append(h('div', { class: 'line' },
      h('span', { class: 'line-label' }, 'OCR 原文'),
      markedNode(item.original, item.corrected),
    ));
    text.append(h('div', { class: 'line' },
      h('span', { class: 'line-label' }, '修正文本'),
      markedNode(item.corrected, item.original),
    ));
    text.append(h('div', { class: 'reason' }, item.reason));
    if (item.context && (item.context.before || item.context.after)) {
      text.append(h('div', { class: 'reason muted', style: 'margin-top:6px' },
        `上下文：${item.context.before || '…'} ／ ${item.context.after || '…'}`,
      ));
    }
    if (item.termRefs.length) {
      const refs = h('div', { class: 'term-refs' }, '涉及术语：');
      for (const termId of item.termRefs) {
        const term = DATA.terms.find((candidate) => candidate.id === termId);
        if (!term) continue;
        const link = h('button', { type: 'button', class: 'chip', style: 'margin:0 4px 0 0;padding:2px 7px' }, term.label);
        link.addEventListener('click', () => focusTerm(term.id));
        refs.append(link);
      }
      text.append(refs);
    }
    body.append(text);
    card.append(body);

    const corpus = h('details', { class: 'corpus' },
      h('summary', {}, '干净 corpus 正文（对照用）'),
      h('pre', {}, notice.corpusText || '（该公告正文为空）'),
    );
    card.append(corpus);

    const decision = h('div', { class: 'decision' });
    for (const [value, label] of Object.entries(ITEM_DECISIONS)) {
      const button = h('button', {
        type: 'button',
        class: `${entry && entry.decision === value ? 'active' : ''}${value === 'keep' ? ' reject' : ''}`,
      }, label);
      button.addEventListener('click', () => setDecision('item', item.key, value));
      decision.append(button);
    }
    const other = h('input', {
      type: 'text',
      placeholder: '其他文本…',
      value: entry && entry.text ? entry.text : '',
      hidden: !(entry && entry.decision === 'other'),
    });
    other.addEventListener('input', () => updateText('item', item.key, other.value));
    decision.append(other);
    decision.append(h('span', { class: `status${entry && isReviewed(entry) ? ' confirmed' : ''}` },
      entry && isReviewed(entry) ? '已确认' : '待定',
    ));
    card.append(decision);
    card.append(h('div', { class: 'note' },
      h('textarea', {
        placeholder: '备注：源图哪里不清楚、为什么改…',
        oninput: (event) => updateNote('item', item.key, event.target.value),
      }, entry && entry.note ? entry.note : ''),
    ));
    return card;
  }

  function render() {
    updateCounts();
    const view = VIEWS[state.filter] || VIEWS.terms;
    document.getElementById('view-title').textContent = view.title;
    document.getElementById('view-desc').textContent = view.desc;
    const cards = currentCards();
    const shown = cards.slice(0, state.limit);
    document.getElementById('view-count').textContent = cards.length
      ? `显示 ${fmt.format(shown.length)} / ${fmt.format(cards.length)}`
      : '';
    cardsEl.textContent = '';
    for (const card of shown) {
      cardsEl.append(card.kind === 'term' ? renderTermCard(card.data) : renderItemCard(card.data));
    }
    if (cards.length > shown.length) {
      const more = h('button', { type: 'button', class: 'btn ghost', style: 'align-self:center' },
        `显示更多（还有 ${fmt.format(cards.length - shown.length)} 条）`);
      more.addEventListener('click', () => {
        state.limit += PAGE_SIZE;
        render();
      });
      cardsEl.append(more);
    }
    emptyEl.hidden = cards.length !== 0;
    for (const chip of document.querySelectorAll('#filters .chip')) {
      const active = chip.dataset.filter === state.filter;
      chip.classList.toggle('active', active);
      chip.setAttribute('aria-pressed', String(active));
    }
  }

  /* ---------- state updates ---------- */

  function setDecision(kind, id, decision) {
    const entry = kind === 'term' ? ensureTerm(id) : ensureItem(id);
    entry.decision = decision;
    entry.status = 'confirmed';
    if (decision !== 'other') entry.text = '';
    persist();
    render();
  }

  function updateText(kind, id, value) {
    const entry = kind === 'term' ? ensureTerm(id) : ensureItem(id);
    entry.text = value;
    entry.status = 'confirmed';
    persist();
  }

  function updateNote(kind, id, value) {
    const entry = kind === 'term' ? ensureTerm(id) : ensureItem(id);
    entry.note = value;
    if (entry.decision) entry.status = 'confirmed';
    persist();
  }

  function focusTerm(id) {
    state.filter = 'terms';
    state.query = '';
    state.limit = PAGE_SIZE;
    searchEl.value = '';
    render();
    const card = document.querySelector(`[data-term-id="${CSS.escape(id)}"]`);
    if (card) {
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.style.outline = '2px solid var(--gold)';
      setTimeout(() => { card.style.outline = ''; }, 1400);
    }
  }

  /* ---------- export / import ---------- */

  function exportReview() {
    const payload = {
      version: 1,
      kind: 'hpma-correction-review',
      exportedAt: new Date().toISOString(),
      terms: review.terms,
      items: review.items,
      snapshot: {
        terms: Object.fromEntries(DATA.terms.map((term) => [
          term.id,
          { label: term.label, sourceForm: term.sourceForm, targetForm: term.targetForm },
        ])),
        items: Object.fromEntries(DATA.items.map((item) => [
          item.key,
          { noticeId: item.noticeId, imageIndex: item.imageIndex, blockIndex: item.blockIndex, original: item.original, corrected: item.corrected },
        ])),
      },
    };
    const now = new Date();
    const name = `correction-review-${now.getFullYear()}${pad2(now.getMonth() + 1)}${pad2(now.getDate())}.json`;
    const blob = new Blob([JSON.stringify(payload, null, 2) + '\n'], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = h('a', { href: url, download: name });
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast(`已导出 ${name}`);
  }

  function importReview(file) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result));
        if (!parsed || typeof parsed !== 'object') throw new Error('not an object');
        const merge = (target, source) => {
          if (!source || typeof source !== 'object') return;
          for (const [key, value] of Object.entries(source)) {
            if (!value || typeof value !== 'object') continue;
            target[key] = {
              decision: typeof value.decision === 'string' ? value.decision : '',
              text: typeof value.text === 'string' ? value.text : '',
              note: typeof value.note === 'string' ? value.note : '',
              status: value.status === 'confirmed' ? 'confirmed' : 'pending',
            };
          }
        };
        merge(review.terms, parsed.terms);
        merge(review.items, parsed.items);
        persist(true);
        render();
        toast('已导入复核记录');
      } catch {
        toast('导入失败：不是有效的复核 JSON');
      }
    };
    reader.readAsText(file, 'utf-8');
  }

  /* ---------- events ---------- */

  document.getElementById('filters').addEventListener('click', (event) => {
    const chip = event.target.closest('.chip');
    if (!chip) return;
    state.filter = chip.dataset.filter;
    state.limit = PAGE_SIZE;
    render();
  });

  searchEl.addEventListener('input', () => {
    state.query = searchEl.value;
    state.limit = PAGE_SIZE;
    render();
  });

  document.getElementById('export-btn').addEventListener('click', exportReview);
  document.getElementById('import-btn').addEventListener('click', () => importFileEl.click());
  importFileEl.addEventListener('change', () => {
    const file = importFileEl.files && importFileEl.files[0];
    if (file) importReview(file);
    importFileEl.value = '';
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === '/' && document.activeElement !== searchEl && document.activeElement.tagName !== 'TEXTAREA' && document.activeElement.tagName !== 'INPUT') {
      event.preventDefault();
      searchEl.focus();
    }
    if (event.key === 'Escape' && document.activeElement === searchEl) {
      searchEl.value = '';
      state.query = '';
      render();
    }
  });

  window.addEventListener('beforeunload', () => persist(true));

  render();
})();

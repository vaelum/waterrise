// The planning pages. One shell, three views, no inline script (the CSP forbids it).
// Everything the user says goes to the service one entry at a time; the item's
// file is never written from here.
(function () {
  'use strict';

  const HEADER = { 'X-Waterrise': '1' };
  const main = document.getElementById('main');
  const crumbs = document.getElementById('crumbs');
  const live = document.getElementById('live');
  const here = document.getElementById('here');
  const bar = document.getElementById('bar');
  const toastEl = document.getElementById('toast');
  let timer = null;
  let stream = null;
  // The version this page's script was served as: /static/<version>/app.js.
  const VERSION = ((document.currentScript && document.currentScript.src) || '').split('/static/')[1]?.split('/')[0] || '';

  // ---- small helpers ------------------------------------------------------- //

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, String(v));
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  let markedReady = false;
  function md(text, cls) {
    const div = h('div', { class: 'prose' + (cls ? ' ' + cls : '') });
    if (window.marked && !markedReady) {
      // Raw HTML in a plan is shown as text, never rendered: this page records approvals.
      window.marked.use({ gfm: true, breaks: false,
        renderer: { html(t) { return esc(typeof t === 'string' ? t : (t.raw || t.text || '')); } } });
      markedReady = true;
    }
    if (!text) return div;
    div.innerHTML = window.marked ? window.marked.parse(String(text)) : esc(text);
    div.querySelectorAll('a[href]').forEach(a => {
      const href = a.getAttribute('href');
      if (/^\s*(javascript|data|vbscript):/i.test(href)) a.removeAttribute('href');
      else if (/^https?:/i.test(href)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    });
    return div;
  }

  // One line of Markdown (a title, a label): code and emphasis, no blocks.
  function mdi(text) {
    const span = h('span', { class: 'prose' });
    if (!text) return span;
    md('', null);  // configures marked once
    span.innerHTML = window.marked ? window.marked.parseInline(String(text)) : esc(text);
    span.querySelectorAll('a[href]').forEach(a => { if (/^\s*(javascript|data|vbscript):/i.test(a.getAttribute('href'))) a.removeAttribute('href'); });
    return span;
  }

  function toast(msg) {
    toastEl.textContent = msg; toastEl.classList.add('on');
    clearTimeout(toast.t); toast.t = setTimeout(() => toastEl.classList.remove('on'), 2500);
  }

  // The pill in the header: what is being saved, or that the page is live.
  function say(msg, err) { live.textContent = msg || 'live · ' + location.host; live.parentElement.classList.toggle('err', !!err); }

  async function api(method, path, body) {
    const opt = { method, cache: 'no-store', headers: Object.assign({}, HEADER) };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    const r = await fetch(path, opt);
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) throw Object.assign(new Error((data && data.error) || ('HTTP ' + r.status)), { status: r.status });
    return data;
  }

  function badge(text, cls) { return h('span', { class: 'badge ' + (cls || '') }, text); }

  // The last part is where the page is; it is marked, not linked. An item's
  // kind and state go in the bubble in the middle of the header instead: the
  // folder for a plan, decision or review, the file's `state` for a note or report.
  const STATE = { draft: 'muted', todo: 'open', 'in-progress': 'warn', done: 'ok', finished: 'ok' };
  function setCrumbs(parts, item) {
    crumbs.replaceChildren(...parts.map((p, i) => h('li', {}, i === parts.length - 1
      ? h('span', { 'aria-current': 'page' }, p.text) : p.href ? h('a', { href: p.href }, p.text) : h('span', {}, p.text))));
    here.replaceChildren(...(item ? [badge(item.kind, 'muted'), badge(item.state, STATE[item.state] || 'muted')] : []));
    here.hidden = !item;
  }

  function back(href) { return h('a', { class: 'back', href }, 'Back'); }

  function poll(fn, ms) {
    clearInterval(timer);
    timer = setInterval(() => { if (document.visibilityState === 'visible') fn(); }, ms);
  }

  // Reload the moment a file behind this page changes: the service watches them
  // and says so over Server-Sent Events. The timer in poll() stays as a slow
  // fallback, for a stream that is down or a proxy that buffers it.
  //
  // Every tab shares one stream through a SharedWorker (events.js): a browser
  // opens six connections per host, and a stream per tab left the sixth tab
  // none for its own requests. Where there is no SharedWorker, a tab opens its
  // own stream for just its page.
  let watcher = null;  // { fn, scope, slug }: what this page reloads, on which changes
  let shared = null;   // this tab's port to the shared worker

  function heard(msg) {
    if (msg.type === 'hello') {
      // The service was upgraded under this page: load the new script too.
      if (msg.version && VERSION && msg.version !== VERSION) location.reload();
    } else if (msg.type === 'change') {
      if (!watcher) return;
      if (watcher.scope === 'repo' ? (msg.slugs || []).includes(watcher.slug) : msg.index) watcher.fn();
    } else if (msg.type === 'error') {
      say('live updates paused; retrying', true);
    } else if (msg.type === 'open') {
      say('');
    }
  }

  function ownStream() {
    if (stream) stream.close();
    stream = null;
    if (!window.EventSource || !watcher) return;
    const q = new URLSearchParams({ scope: watcher.scope });
    if (watcher.slug) q.set('slug', watcher.slug);
    stream = new EventSource('/api/events?' + q);
    stream.addEventListener('hello', e => {
      let v = '';
      try { v = JSON.parse(e.data).version; } catch (err) { v = ''; }
      heard({ type: 'hello', version: v });
    });
    stream.addEventListener('change', () => watcher && watcher.fn());
    stream.onerror = () => heard({ type: 'error' });
    stream.onopen = () => heard({ type: 'open' });
  }

  function watch(fn, scope, slug) {
    watcher = { fn, scope, slug };
    if (shared) return;
    if (!window.SharedWorker || !VERSION) return ownStream();
    try {
      const w = new SharedWorker(`/static/${VERSION}/events.js`, { name: 'planning-events' });
      w.onerror = () => { shared = null; ownStream(); };
      shared = w.port;
      shared.onmessage = (e) => heard(e.data || {});
      shared.start();
    } catch (e) {
      shared = null;
      ownStream();
    }
  }

  // Leaving the page (or going into the back-forward cache) gives this tab's
  // share back, so the worker can drop the stream once no tab wants it.
  window.addEventListener('pagehide', () => {
    if (shared) { try { shared.postMessage('bye'); } catch (e) { /* gone already */ } shared = null; }
    if (stream) { stream.close(); stream = null; }
  });
  window.addEventListener('pageshow', (e) => {
    if (e.persisted && watcher) watch(watcher.fn, watcher.scope, watcher.slug);
  });

  function typing() {
    const a = document.activeElement;
    return a && (a.tagName === 'TEXTAREA' || (a.tagName === 'INPUT' && a.type === 'text'));
  }

  // ---- index --------------------------------------------------------------- //

  async function viewIndex() {
    setCrumbs([]);
    document.title = 'waterrise';
    bar.hidden = true;
    // The project the waiting list shows ('' for all). Remembered per browser.
    let only = '';
    try { only = localStorage.getItem('planning-waiting-only') || ''; } catch (e) { /* private window */ }
    async function load() {
      let data;
      try { data = await api('GET', '/api/repos'); say(''); } catch (e) { say('the service does not answer: ' + e.message, true); return; }
      // Not under an open filter: a redraw would close it.
      if (typing() || (document.activeElement && document.activeElement.closest('.picker'))) return;
      const repos = data.repos || [];
      const rows = repos.flatMap(r => (r.waiting || []).map(w => ({ r, w })));
      const first = rows.length ? `/r/${rows[0].r.slug}/i/${rows[0].w.id}` : null;
      const n = repos.length;
      const intro = h('div', {}, h('h1', {}, 'waterrise'),
        h('p', { class: 'lede' }, 'Typed plans your agent writes, and the pages where you answer them.',
          first ? [' ', h('a', { class: 'quiet', href: first }, 'Answer the first →')] : null),
        n ? h('p', { class: 'muted' }, `${n === 1 ? 'One repository is' : n + ' repositories are'} registered. `,
              'Pick an item to answer its decisions and approve its gated steps; nothing leaves this machine.')
          : h('p', { class: 'muted' }, 'No repository is registered. Run ', h('code', {}, 'waterrise serve'), ' in one.'));
      const side = h('nav', { 'aria-label': 'Waiting on you' });
      if (rows.length) {
        const projects = repos.filter(r => (r.waiting || []).length);
        if (!projects.some(r => r.slug === only)) only = '';
        const label = h('div', { class: 'label' });
        const list = h('div', { class: 'rows waiting' });
        const draw = () => {
          const shown = only ? rows.filter(x => x.r.slug === only) : rows;
          label.textContent = `Waiting on you · ${shown.length}` + (shown.length < rows.length ? ` of ${rows.length}` : '');
          list.replaceChildren(...shown.map(({ r, w }) => h('a', { class: 'row-link', href: `/r/${r.slug}/i/${w.id}` },
            h('span', { class: 'repo' }, r.name),
            h('span', { class: 'what' }, h('span', { class: 't' }, mdi(w.title)),
              h('span', { class: 'tags' }, h('span', { class: 'id' }, w.id), waitTags(w.for_user).map(t => badge(t, 'open')))))));
          list.scrollTop = 0;
        };
        const filter = projects.length > 1 ? picker('Show the items of one project',
          [{ value: '', text: 'All projects', n: rows.length }].concat(projects.map(r => ({ value: r.slug, text: r.name, n: r.waiting.length }))),
          only, value => {
            only = value;
            try { localStorage.setItem('planning-waiting-only', only); } catch (err) { /* private window */ }
            draw();
          }) : null;
        draw();
        side.append(h('div', { class: 'wait-head' }, label, filter), list);
      } else if (n) {
        side.append(h('p', { class: 'lead' }, 'Nothing waits on you.'));
      }
      const out = [h('section', { class: 'intro' }, h('div', { class: 'in' }, intro, side))];
      const grid = h('div', { class: 'grid' });
      for (const r of repos) grid.append(h('div', { class: 'card repo-card' }, repoLine(r)));
      if (n) out.push(h('section', { class: 'repos' }, h('h2', {}, 'Repositories'), grid));
      main.replaceChildren(...out);
    }
    // What a waiting item asks for, counted: "2 decisions", "1 gate".
    function waitTags(list) {
      const names = { decision: ['decision', 'decisions'], approval: ['gate', 'gates'], 'plan-approval': ['plan approval', 'plan approvals'],
                      step: ['step', 'steps'], disapproved: ['disapproved', 'disapproved'] };
      const counts = new Map();
      for (const x of list || []) counts.set(x.what, (counts.get(x.what) || 0) + 1);
      return [...counts].map(([what, c]) => { const nm = names[what] || [what, what]; return `${c} ${c === 1 ? nm[0] : nm[1]}`; });
    }
    function repoLine(r) {
      const counts = {};
      for (const it of r.items || []) counts[it.folder] = (counts[it.folder] || 0) + 1;
      const line = h('div', { class: 'name-line' }, h('a', { class: 'name', href: `/r/${r.slug}/` }, r.name),
        r.branch ? h('span', { class: 'muted small' }, r.branch) : null,
        r.home === 'central' ? badge('plans centrally', 'muted') : null);
      const meta = h('div', { class: 'meta' }, h('div', { class: 'path' }, r.path));
      if (r.missing) {
        meta.append(badge('missing', 'warn'), ' ',
          h('button', { class: 'link', onclick: async () => {
            try { await api('DELETE', `/api/repos/${r.slug}`); viewIndex(); } catch (e) { toast(e.message); }
          } }, 'Forget'));
      } else {
        const parts = ['draft', 'todo', 'in-progress', 'done', 'notes'].filter(k => counts[k]).map(k => `${k} ${counts[k]}`);
        if (!r.typed) parts.push('not typed' + ((r.legacy || []).length ? ` · ${r.legacy.length} Markdown` : ''));
        if (r.worktree_count) parts.push(`${r.worktree_count} worktree${r.worktree_count > 1 ? 's' : ''}`);
        if (parts.length) meta.append(h('div', { class: 'counts' }, parts.join(' · ')));
      }
      return h('div', {}, line, meta);
    }
    await load();
    watch(load, 'index');
    poll(load, 30000);
  }

  // A drop-down in the page's own look: a pill that opens a list. The
  // keyboard works as on a native one: arrows, Home/End, Enter, Escape.
  function picker(label, choices, value, onpick) {
    const name = c => [h('span', { class: 'pick-name' }, c.text), h('span', { class: 'pick-n' }, String(c.n))];
    const current = () => choices.find(c => c.value === value) || choices[0];
    const btn = h('button', { type: 'button', class: 'picker-btn', 'aria-haspopup': 'listbox', 'aria-expanded': 'false', 'aria-label': label });
    const list = h('ul', { class: 'picker-list', role: 'listbox', 'aria-label': label, tabindex: '-1', hidden: true });
    const wrap = h('div', { class: 'picker' }, btn, list);
    let at = 0;
    const opts = choices.map((c, i) => h('li', { role: 'option', id: `pick-${i}`, 'data-value': c.value,
      onmousemove: () => mark(i), onclick: () => choose(i) }, h('span', { class: 'pick-check', 'aria-hidden': 'true' }, '✓'), ...name(c)));
    list.append(...opts);
    function paint() {
      btn.replaceChildren(...name(current()), h('span', { class: 'pick-caret', 'aria-hidden': 'true' }));
      opts.forEach((o, i) => o.setAttribute('aria-selected', String(choices[i].value === value)));
    }
    function mark(i) {
      at = Math.max(0, Math.min(opts.length - 1, i));
      opts.forEach((o, j) => o.classList.toggle('at', j === at));
      list.setAttribute('aria-activedescendant', opts[at].id);
      opts[at].scrollIntoView({ block: 'nearest' });
    }
    function open() {
      list.classList.remove('from-left');
      list.hidden = false; btn.setAttribute('aria-expanded', 'true');
      if (list.getBoundingClientRect().left < 8) list.classList.add('from-left');
      mark(Math.max(0, choices.findIndex(c => c.value === value)));
      list.focus();
      document.addEventListener('pointerdown', outside, true);
    }
    function close(refocus) {
      if (list.hidden) return;
      list.hidden = true; btn.setAttribute('aria-expanded', 'false');
      document.removeEventListener('pointerdown', outside, true);
      if (refocus) btn.focus();
    }
    function outside(e) { if (!wrap.contains(e.target)) close(false); }
    function choose(i) { value = choices[i].value; paint(); close(true); onpick(value); }
    btn.addEventListener('click', () => (list.hidden ? open() : close(true)));
    btn.addEventListener('keydown', e => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); open(); } });
    list.addEventListener('keydown', e => {
      const keys = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: opts.length - 1, PageDown: at + 5, PageUp: at - 5 };
      if (e.key in keys) { e.preventDefault(); mark(keys[e.key]); }
      else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(at); }
      else if (e.key === 'Escape') { e.preventDefault(); close(true); }
      else if (e.key === 'Tab') close(false);
    });
    list.addEventListener('focusout', e => { if (!wrap.contains(e.relatedTarget)) close(false); });
    paint();
    return wrap;
  }

  // ---- one repository ------------------------------------------------------ //

  const LANES = ['draft', 'todo', 'in-progress', 'done'];

  async function viewRepo(slug) {
    bar.hidden = true;
    let byId = new Map();
    let drag = null;  // a drag in progress: no redraw under it
    let onlyWorktree = '';  // the lanes' filter
    async function load() {
      let r;
      try { r = await api('GET', `/api/r/${slug}`); say(''); } catch (e) { say(e.message, true); return; }
      if (typing() || drag) return;
      document.title = r.name + ' · waterrise';
      setCrumbs([{ text: r.name }]);
      const out = [back('/'), h('h1', {}, r.name), h('p', { class: 'where' }, r.path + (r.branch ? ' (' + r.branch + ')' : ''))];
      if (r.home === 'central') out.push(h('p', { class: 'muted small' }, 'Plans centrally, in ', h('span', { class: 'mono' }, r.planning), '.'));
      if (r.missing) { out.push(h('p', {}, 'This repository is no longer on disk.')); main.replaceChildren(...out); return; }
      if (!r.typed) {
        out.push(h('div', { class: 'problems warn' }, 'Not a typed planning folder: ',
          h('code', {}, 'waterrise init'), ' makes one. Markdown plans are shown read-only.'));
      } else if (r.errors || r.warnings) {
        out.push(h('div', { class: 'problems' + (r.errors ? '' : ' warn') },
          `planning check: ${r.errors} error${r.errors === 1 ? '' : 's'}, ${r.warnings} warning${r.warnings === 1 ? '' : 's'}. `,
          h('code', {}, 'waterrise check'), ' says which.'));
      }
      const all = r.items || [];
      byId = new Map(all.filter(i => i.id).map(i => [i.id, i]));
      if (all.some(i => i.folder !== 'notes' && i.folder !== 'reports')) {
        const trees = r.worktrees || [];
        const assigned = all.filter(i => i.worktree);
        const gone = [...new Set(assigned.filter(i => i.worktree.gone).map(i => i.worktree.name))];
        if (assigned.length || all.some(i => i.home)) {
          const names = trees.map(w => w.name).concat(gone);
          if (!names.includes(onlyWorktree)) onlyWorktree = '';
          out.push(h('div', { class: 'lane-filter' }, picker('Show the plans of one worktree',
            [{ value: '', text: 'Every worktree', n: all.filter(i => LANES.includes(i.folder)).length }]
              .concat(names.map(name => ({ value: name, text: gone.includes(name) ? `${name} (gone)` : name, n: all.filter(i => belongsTo(i, name)).length }))),
            onlyWorktree, value => { onlyWorktree = value; filterLanes(); })));
        }
        const lanes = h('div', { class: 'lanes' });
        for (const folder of LANES) {
          const items = all.filter(i => i.folder === folder);
          const list = h('ol', { class: 'lane-list', 'data-folder': folder });
          for (const it of items) list.append(itemCard(slug, it, true));
          if (!items.length) list.append(h('li', { class: 'lane-empty muted small' }, 'empty'));
          lanes.append(h('section', { class: 'lane', 'data-folder': folder },
            h('div', { class: 'lane-head' }, h('h2', {}, folder + '/'), h('span', { class: 'count' }, String(items.length))),
            h('div', { class: 'lane-why small', hidden: true }), list));
          dropTarget(list);
        }
        out.push(lanes, h('p', { class: 'muted small lane-hint' },
          'Drag a card, or use its ⠿ handle with the arrow keys, to order a lane or move it to another. ',
          'A move is ', h('code', {}, 'planning move'), ': a lane whose rules the item does not meet says why and takes no drop.'));
        if (trees.length > 1 || gone.length) out.push(h('h2', {}, 'Worktrees'), worktreeList(slug, trees, gone, assigned, r.worktree_folder));
      }
      const notes = all.filter(i => i.folder === 'notes');
      if (notes.length) {
        out.push(h('h2', {}, 'notes/'), h('div', { class: 'rows notes' }, notes.map(it => it.broken
          ? h('div', { class: 'row-link' }, h('span', { class: 'id' }, it.file), h('span', {}, badge('cannot be read', 'warn')))
          : h('a', { class: 'row-link', href: `/r/${slug}/i/${it.id}` }, h('span', { class: 'id' }, it.id), h('span', {}, mdi(it.title))))));
      }
      // The project's reports first, then those about a plan (each also on its plan's page).
      const reports = all.filter(i => i.folder === 'reports').sort((a, b) => (a.about ? 1 : 0) - (b.about ? 1 : 0));
      if (reports.length) {
        out.push(h('h2', {}, 'reports/'));
        const grid = h('div', { class: 'grid' });
        for (const it of reports) {
          const card = itemCard(slug, it, false);
          const plan = it.about && byId.get(it.about);
          if (it.about) card.append(h('div', { class: 'muted small' }, 'about ', plan ? h('a', { href: `/r/${slug}/i/${plan.id}` }, plan.title) : h('code', {}, it.about)));
          grid.append(card);
        }
        out.push(grid);
      }
      const legacy = (r.legacy || []).concat(r.forms || []);
      if (legacy.length) {
        const d = h('details', {}, h('summary', {}, `Older files (${legacy.length}), read-only`));
        const ul = h('ul');
        for (const p of r.legacy || []) ul.append(h('li', {}, h('a', { href: `/r/${slug}/m/${p}` }, p)));
        for (const p of r.forms || []) ul.append(h('li', {}, h('a', { href: `/r/${slug}/f/${p}` }, p)));
        d.append(ul);
        out.push(h('h2', {}, 'Other files'), d);
      }
      // A redraw (after a move, or when a file changes) keeps the keyboard
      // on the handle it was on.
      const a = document.activeElement;
      const keep = a && a.classList.contains('handle') ? a.closest('.item-card').dataset.id : null;
      main.replaceChildren(...out);
      if (keep) { const hd = main.querySelector(`.item-card[data-id="${keep}"] .handle`); if (hd) hd.focus(); }
      filterLanes();
    }

    // Hidden, not left out: a saved order still has to name the whole lane.
    function filterLanes() {
      for (const card of main.querySelectorAll('.lane .item-card[data-id]')) {
        const it = byId.get(card.dataset.id);
        card.hidden = Boolean(onlyWorktree) && !(it && belongsTo(it, onlyWorktree));
      }
    }

    // A plan belongs to its assigned worktree, or to the one it lives in.
    function belongsTo(it, name) {
      return Boolean((it.worktree && it.worktree.name === name) || (it.home && it.home.name === name));
    }
    function worktreeList(slug, trees, gone, assigned, folder) {
      const plansOf = ids => ids.length ? ids.flatMap((id, i) => [i ? ', ' : '', h('a', { href: `/r/${slug}/i/${id}` }, id)]) : [h('span', { class: 'muted' }, 'no plans')];
      return h('div', { class: 'rows worktrees' },
        trees.map(w => h('div', { class: 'worktree-row' },
          h('span', { class: 'wt-name' }, w.name),
          h('span', { class: 'wt-state' }, w.branch || 'detached', ' ',
            w.clean === null || w.clean === undefined ? badge('unknown', 'muted')
              : badge(w.clean === false ? 'dirty' : 'clean', w.clean === false ? 'warn' : 'ok'),
            w.ahead ? ` +${w.ahead}` : '', w.behind ? ` -${w.behind}` : ''),
          h('span', { class: 'wt-plans' }, ...plansOf(w.plans)),
          h('span', { class: 'path wt-path' }, w.path))),
        gone.map(name => h('div', { class: 'worktree-row' },
          h('span', { class: 'wt-name' }, name),
          h('span', { class: 'wt-state' }, badge('gone', 'warn')),
          h('span', { class: 'wt-plans' }, ...plansOf(assigned.filter(i => i.worktree.name === name).map(i => i.id))),
          h('span', { class: 'path wt-path muted' }, assigned.find(i => i.worktree.name === name).worktree.path))),
        folder ? h('div', { class: 'muted wt-folder' }, 'Worktree folder: ', h('span', { class: 'path' }, folder.rel + '/'),
          ` (${folder.source === 'default' ? 'the default' : folder.source === 'registry' ? 'set on this machine' : 'set in planning.toml'})`) : null);
    }

    // ---- order: drag within a lane, or the handle and the arrow keys ---- //

    function laneIds(list) {
      return [...list.querySelectorAll(':scope > .item-card[data-id]')].map(c => c.dataset.id);
    }
    async function saveOrder(list, before) {
      const ids = laneIds(list);
      if (ids.join() === before.join()) return;
      say('saving…');
      try { await api('PUT', `/api/r/${slug}/order/${list.dataset.folder}`, { ids }); say('order saved ' + new Date().toLocaleTimeString()); }
      catch (e) { say('not saved: ' + e.message, true); toast('Order not saved: ' + e.message); }
      load();
    }
    // Why `it` may not go to `folder` (empty: it may). The service says, per
    // lane, from the same rules `planning move` checks.
    function refusal(it, folder) {
      if (folder === it.folder) return [];
      return (it.moves || {})[folder] || ['an item in ' + it.folder + '/ does not move between lanes'];
    }
    async function moveTo(it, list) {
      const folder = list.dataset.folder;
      say('moving…');
      try {
        await api('PUT', `/api/r/${slug}/move/${it.id}`, { folder, ids: laneIds(list) });
        say(`${it.id} moved to ${folder}/ ` + new Date().toLocaleTimeString());
      } catch (e) { say('not moved: ' + e.message, true); toast('Not moved: ' + e.message); }
      load();
    }
    // While a card is dragged, each other lane shows whether it takes it.
    function showLanes(it, on) {
      for (const lane of main.querySelectorAll('.lane[data-folder]')) {
        const why = on ? refusal(it, lane.dataset.folder) : [];
        const list = lane.querySelector('.lane-list'), note = lane.querySelector('.lane-why');
        list.classList.toggle('no-drop', why.length > 0);
        list.classList.toggle('can-drop', on && !why.length && lane.dataset.folder !== it.folder);
        note.hidden = !why.length;
        note.textContent = why.length ? 'Cannot move here: ' + why.join('; ') : '';
      }
    }
    // The whole lane, not only its cards: a short lane's empty part takes a drop too.
    function dropTarget(list) {
      const lane = list.closest('.lane');
      lane.addEventListener('dragover', e => {
        if (!drag) return;
        if (refusal(drag.it, list.dataset.folder).length) return;  // no preventDefault: no drop here
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        const after = [...list.querySelectorAll(':scope > .item-card[data-id]:not(.dragging)')]
          .find(c => { const b = c.getBoundingClientRect(); return e.clientY < b.top + b.height / 2; });
        if (after) { if (after.previousElementSibling !== drag.el) list.insertBefore(drag.el, after); }
        else if (list.lastElementChild !== drag.el) list.append(drag.el);
      });
      lane.addEventListener('drop', e => { if (drag && !refusal(drag.it, list.dataset.folder).length) { e.preventDefault(); drag.dropped = true; } });
    }
    function draggable(card, it) {
      card.draggable = true;
      card.addEventListener('dragstart', e => {
        const list = card.parentElement;
        drag = { el: card, it, list, before: laneIds(list), dropped: false };
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', it.id);
        requestAnimationFrame(() => { card.classList.add('dragging'); showLanes(it, true); });
      });
      card.addEventListener('dragend', () => {
        const d = drag; drag = null;
        card.classList.remove('dragging');
        showLanes(it, false);
        const into = card.parentElement;
        if (d && d.dropped && into !== d.list) moveTo(it, into);
        else if (d && d.dropped) saveOrder(d.list, d.before);
        else load();  // cancelled: put the cards back where the files say
      });
    }
    function handle(it) {
      return h('button', { class: 'handle', type: 'button', title: 'Drag, or use the arrow keys: up and down within the lane, left and right to the next lane',
                           'aria-label': `Move ${it.title}: arrow up or down within the lane, left or right to another`,
                           onkeydown: e => {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          e.preventDefault();
          const to = LANES[LANES.indexOf(it.folder) + (e.key === 'ArrowLeft' ? -1 : 1)];
          if (!to) return;
          const why = refusal(it, to);
          if (why.length) { toast(`Cannot move to ${to}/: ` + why.join('; ')); return; }
          const list = main.querySelector(`.lane-list[data-folder="${to}"]`), card = e.currentTarget.closest('.item-card');
          list.append(card);
          e.currentTarget.focus();
          moveTo(it, list);
          return;
        }
        if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
        e.preventDefault();
        const card = e.currentTarget.closest('.item-card'), list = card.parentElement, before = laneIds(list);
        const sib = e.key === 'ArrowUp' ? card.previousElementSibling : card.nextElementSibling;
        if (!sib || !sib.dataset.id) return;
        const hd = e.currentTarget;
        if (e.key === 'ArrowUp') list.insertBefore(card, sib); else list.insertBefore(sib, card);
        hd.focus();
        saveOrder(list, before);
      } }, '⠿');
    }

    // ---- dependencies: what a plan needs first, and what waits on it ---- //

    function depChip(id, ref, done) {
      const t = byId.get(id);
      const label = t ? t.title : ref;
      const where = t ? t.folder + '/' : 'not found';
      return h('li', { class: 'dep' + (done ? ' done' : '') },
        h('span', { class: 'dep-mark', 'aria-hidden': 'true' }, done ? '✓' : '○'), ' ',
        t ? h('a', { href: `/r/${slug}/i/${id}`, draggable: 'false' }, mdi(label)) : h('code', {}, ref),
        ref !== id && t ? h('span', { class: 'mono small muted' }, ' ' + ref.split('/')[1]) : null,
        h('span', { class: 'muted small' }, ' · ' + where + (done && where !== 'done/' ? ' · done' : '')));
    }
    function deps(it) {
      const needs = it.needs || [], by = it.needed_by || [];
      if (!needs.length && !by.length) return null;
      const box = h('div', { class: 'deps' });
      if (needs.length) {
        box.append(h('div', { class: 'deps-h' }, 'Needs first'),
          h('ul', { class: 'dep-list' }, needs.map(n => depChip(n.id, n.ref, n.done))));
      }
      if (by.length) {
        box.append(h('div', { class: 'deps-h' }, 'Needed by'),
          h('ul', { class: 'dep-list' }, by.map(id => depChip(id, id, (byId.get(id) || {}).folder === 'done'))));
      }
      return box;
    }
    // Pointing at a card lights up what it needs and what needs it.
    function relate(card, it, on) {
      const mark = (id, cls) => { const c = main.querySelector(`.item-card[data-id="${id}"]`); if (c) c.classList.toggle(cls, on); };
      card.classList.toggle('rel-self', on);
      for (const n of it.needs || []) if (n.id) mark(n.id, 'rel-need');
      for (const id of it.needed_by || []) mark(id, 'rel-dep');
    }

    function itemCard(slug, it, inLane) {
      if (it.broken) {
        return h(inLane ? 'li' : 'div', { class: 'card item-card' }, h('div', {}, it.file), h('div', { class: 'meta' }, badge('cannot be read', 'warn'), ' see planning check'));
      }
      const pct = it.steps.total ? Math.round(100 * it.steps.done / it.steps.total) : 0;
      const pausable = it.kind === 'plan' && (it.folder === 'todo' || it.folder === 'in-progress');
      const toggle = pausable ? h('button', { class: 'card-pause' + (it.paused ? ' on' : ''), draggable: 'false',
        title: it.paused ? 'Paused: let the agent carry on' : 'Pause: the agent finishes the step it is on, then waits',
        'aria-label': it.paused ? 'Continue' : 'Pause',
        onclick: async (e) => {
          e.preventDefault(); e.stopPropagation();
          try { await api('PUT', `/api/r/${slug}/pause/${it.id}`, { paused: !it.paused }); } catch (err) { toast(err.message); }
          load();
        } }, it.paused ? '▶' : '⏸') : null;
      const card = h(inLane ? 'li' : 'div', { class: 'card item-card' + (it.paused ? ' paused' : ''), 'data-id': it.id },
        h('div', { class: 'card-head' }, inLane ? handle(it) : null, badge(it.kind, 'muted'), h('span', { class: 'id' }, it.id), toggle),
        h('a', { class: 'title', href: `/r/${slug}/i/${it.id}`, draggable: 'false' }, mdi(it.title)));
      if (it.steps.total) {
        card.append(h('div', { class: 'progress-line' }, h('div', { class: 'progress' }, h('span', { style: null })),
          h('span', { class: 'steps-n' }, `${it.steps.done}/${it.steps.total} steps`)));
        card.querySelector('.progress > span').style.width = pct + '%';
      }
      const b = h('div', { class: 'badges' });
      if (it.worktree || it.home) {
        const name = it.worktree ? (it.worktree.gone ? `${it.worktree.name} (gone)` : it.worktree.name) : '';
        const on = it.home ? it.home.branch || it.home.name : '';
        const branch = on && !(it.worktree && it.worktree.name === on) ? `on ${on}` : '';
        const chip = badge([name, branch].filter(Boolean).join(' '), it.worktree && it.worktree.gone ? 'warn' : 'muted');
        chip.classList.add('worktree-chip');
        chip.title = it.home ? it.home.path : it.worktree.path;
        b.append(chip);
      }
      if (it.paused) b.append(badge('paused', 'warn'));
      if (it.for_user.length) b.append(badge(`${it.for_user.length} for you`, 'open'));
      if (it.for_agent.length) b.append(badge(`${it.for_agent.length} for the agent`, 'agent'));
      const open = (it.needs || []).filter(n => !n.done);
      if (it.folder !== 'done' && open.length) b.append(badge(`waits on ${open.length}`, 'warn'));
      else if ((it.folder === 'draft' || it.folder === 'todo') && (it.needs || []).length) b.append(badge('ready to start', 'ok'));
      if (b.childNodes.length) card.append(b);
      // Placed above a plan it needs, in the same lane: the order says "this
      // first", the needs say it cannot start yet.
      if (inLane) {
        const lane = [...byId.values()].filter(x => x.folder === it.folder).map(x => x.id);
        const later = open.filter(n => n.id && lane.indexOf(n.id) > lane.indexOf(it.id));
        if (later.length) card.append(h('div', { class: 'order-warn small' },
          '↓ placed before ', ...later.map((n, i) => [i ? ', ' : '', mdi((byId.get(n.id) || {}).title || n.id)]), ', which it needs first'));
      }
      const dep = deps(it);
      if (dep) card.append(dep);
      if (inLane) {
        draggable(card, it);
        card.addEventListener('mouseenter', () => relate(card, it, true));
        card.addEventListener('mouseleave', () => relate(card, it, false));
        card.addEventListener('focusin', () => relate(card, it, true));
        card.addEventListener('focusout', () => relate(card, it, false));
      }
      return card;
    }
    await load();
    watch(load, 'repo', slug);
    poll(load, 30000);
  }

  // ---- a Markdown file, read-only ------------------------------------------ //

  async function viewMarkdown(slug, path) {
    bar.hidden = true;
    clearInterval(timer);
    setCrumbs([{ text: slug, href: `/r/${slug}/` }, { text: path }]);
    async function load() {
      try {
        const f = await api('GET', `/api/r/${slug}/file/${path}`);
        main.replaceChildren(back(`/r/${slug}/`), h('p', { class: 'lead' }, badge('read-only', 'muted'), ' a Markdown plan from before format 1'), md(f.text));
      } catch (e) { main.replaceChildren(h('p', {}, e.message)); }
    }
    await load();
    watch(load, 'repo', slug);
  }

  // ---- one item ------------------------------------------------------------ //

  async function viewItem(slug, id) {
    let etag = null, view = null, pendingRender = false;
    // What the user has typed and not yet saved, by box. A re-render (after a
    // save, or when the agent changes the file) rebuilds every box from the
    // server; without this, anything unsent in them would be wiped.
    const drafts = new Map();
    function draftBox(el, key, saved) {
      el.value = drafts.has(key) ? drafts.get(key) : (saved || '');
      el.addEventListener('input', () => drafts.set(key, el.value));
      return el;
    }
    const base = `/api/r/${slug}`;

    async function load(force) {
      let r;
      try {
        r = await fetch(`${base}/item/${id}`, { cache: 'no-store', headers: etag && !force ? { 'If-None-Match': etag } : {} });
      } catch (e) { say('the service does not answer', true); return; }
      if (r.status === 304) { say(''); return; }
      if (!r.ok) { say('HTTP ' + r.status, true); if (r.status === 404) main.replaceChildren(h('p', {}, 'No such item. It may have been deleted.')); return; }
      etag = r.headers.get('ETag');
      view = await r.json();
      say('');
      if (typing()) { pendingRender = true; return; }
      render();
    }
    document.addEventListener('focusout', (e) => {
      // Leaving a text box for a button: let the click land first, or the
      // re-render would replace the button under the pointer.
      const wait = e.relatedTarget && e.relatedTarget.tagName === 'BUTTON' ? 600 : 50;
      setTimeout(() => { if (pendingRender && !typing()) { pendingRender = false; render(); } }, wait);
    });

    async function save(path, body, what, draftKey) {
      say('saving…');
      try {
        const { __draft, ...payload } = body;  // __draft: what the box held when this was sent
        await api('PUT', `${base}/${path}`, payload);
        if (draftKey && drafts.get(draftKey) === __draft) drafts.delete(draftKey);
        say('saved ' + new Date().toLocaleTimeString()); etag = null; if (what !== 'quiet') load(true);
      }
      catch (e) {
        say('not saved: ' + e.message, true); toast('Not saved: ' + e.message);
        // Refused as outdated: show the newest text; the note stays in `drafts`.
        if (e.status === 409) { review.acted = false; etag = null; load(true); }
      }
    }

    function render() {
      const v = view, d = v.data, ans = v.answers || {};
      document.title = d.title + ' · waterrise';
      const state = v.kind === 'note' || v.kind === 'report' ? d.state || 'finished' : v.folder;
      setCrumbs([{ text: v.repo.name, href: `/r/${slug}/` }, { text: v.id }], { kind: v.kind, state });
      const out = [back(`/r/${slug}/`)];
      out.push(h('h1', {}, d.title));
      if (d.summary) out.push(h('p', { class: 'lede' }, mdi(d.summary)));
      // The plan's reports, first after the summary (where, amos 2026-10-24):
      // every one of its own, broken and missing ones as such, never left out.
      if (v.kind === 'plan' && (v.reports || []).length) out.push(reportsBlock(v.reports));

      const probs = (v.problems || []);
      if (probs.length) {
        const errs = probs.filter(p => p.level === 'error');
        out.push(h('div', { class: 'problems' + (errs.length ? '' : ' warn') }, h('strong', {}, 'planning check'),
          h('ul', {}, probs.map(p => h('li', {}, (p.line ? `line ${p.line}: ` : '') + p.message)))));
      }

      const kv = [];
      for (const key of ['needs', 'context', 'related']) {
        // A decision's `context` is prose, shown on its card; only lists are references.
        if (Array.isArray(d[key]) && d[key].length) kv.push(h('div', { class: 'kv' }, key + ': ', ...d[key].map((x, i) => [i ? ', ' : '', h('code', {}, x)])));
      }
      if ((d.touches || []).length) kv.push(h('div', { class: 'kv' }, 'touches: ', ...d.touches.map((x, i) => [i ? ', ' : '', h('code', {}, x)])));
      if (d.created) kv.push(h('div', { class: 'kv' }, `created ${d.created} · updated ${d.updated}`));
      if (kv.length) out.push(h('div', { class: 'kvs' }, kv));
      if (v.kind === 'plan' && (v.worktrees || []).length) out.push(worktreePicker(v));

      if (v.kind === 'plan' && v.plan_approval_required) out.push(planApproval(v));

      if (v.kind === 'report') {
        if (v.about) out.push(h('p', { class: 'kv' }, 'about ', h('a', { href: `/r/${slug}/i/${v.about.id}` }, v.about.title)));
        else out.push(h('p', { class: 'kv muted' }, 'a report on the project'));
        // Its own scripts run, with an opaque origin: the frame cannot reach
        // this page, the service's API or its token (see REPORT_CSP).
        out.push(h('iframe', { class: 'report', src: v.document, sandbox: 'allow-scripts', title: d.title,
          referrerpolicy: 'no-referrer' }));
        out.push(h('p', { class: 'muted small' }, h('a', { href: v.document, target: '_blank', rel: 'noopener' }, 'Open the report on its own'),
          ' · read-only here; comment below'));
      }

      if (d.why) out.push(h('h2', { class: 'reading' }, 'Why'), md(d.why, 'reading'));
      if (d.subject) out.push(h('h2', { class: 'reading' }, 'Subject'), md(d.subject, 'reading'));
      if (d.method) out.push(h('h2', { class: 'reading' }, 'Method'), md(d.method, 'reading'));
      if (d.scope && ((d.scope.in || []).length || (d.scope.out || []).length)) {
        out.push(h('h2', { class: 'reading' }, 'Scope'));
        if ((d.scope.in || []).length) out.push(h('h3', { class: 'reading' }, 'In'), h('ul', { class: 'reading' }, d.scope.in.map(x => h('li', {}, md(x)))));
        if ((d.scope.out || []).length) out.push(h('h3', { class: 'reading' }, 'Out'), h('ul', { class: 'reading' }, d.scope.out.map(x => h('li', {}, md(x)))));
      }
      if ((d.acceptance || []).length) out.push(h('h2', { class: 'reading' }, 'Acceptance'), h('ul', { class: 'reading' }, d.acceptance.map(x => h('li', {}, md(x)))));

      if ((d.section || []).length) {
        out.push(h('h2', { class: v.kind === 'note' ? '' : 'reading' }, v.kind === 'note' ? 'Sections' : 'Design'));
        d.section.forEach((s, i) => {
          const det = h('details', { class: 'section card' + (v.kind === 'note' ? '' : ' reading'), id: s.id || null, open: v.kind === 'note' || null },
            h('summary', {}, s.title), md(s.body), s.id ? comments(s.id) : null);
          out.push(det);
        });
      }

      const decisions = v.kind === 'decision' ? [Object.assign({}, d, { id: v.id })] : (d.decision || []);
      if (decisions.length) {
        const open = decisions.filter(x => !(x.resolved || []).length);
        out.push(v.kind === 'decision' ? h('h2', {}, 'The decision')
          : h('h2', {}, 'Decisions ', h('span', { class: 'h-meta' }, `${open.length} open of ${decisions.length}`)));
        for (const dec of decisions) out.push(decisionCard(dec, ans));
      }

      for (const ph of d.phase || []) {
        const steps = ph.step || [];
        const done = steps.filter(s => s.state === 'done' || s.state === 'dropped').length;
        const fin = steps.length && done === steps.length ? ' phase-done' : '';
        out.push(h('h2', { class: 'phase' + fin, id: ph.id }, ph.title, ' ', h('span', { class: 'h-meta mono' }, `${ph.id} · ${done}/${steps.length}`)));
        if (ph.goal) out.push(h('p', { class: 'goal' + fin }, ph.goal));
        const pc = comments(ph.id, true); if (fin) pc.classList.add('phase-done');
        out.push(pc);
        out.push(h('ul', { class: 'steps' + fin }, steps.map(s => stepCard(s, ans))));
      }

      if ((d.finding || []).length) {
        out.push(h('h2', {}, `Findings (${d.finding.filter(f => (f.state || 'open') === 'open').length} open)`));
        for (const f of d.finding) {
          out.push(h('div', { class: 'card', id: f.id },
            h('h3', {}, badge(f.severity, f.severity === 'critical' || f.severity === 'high' ? 'gate' : 'muted'), badge(f.state || 'open', (f.state || 'open') === 'open' ? 'open' : 'ok'), mdi(f.title)),
            h('div', { class: 'kv' }, f.id + (f.area ? ' · ' + f.area : '') + (f.to ? ' · to ' + f.to : '')),
            f.details ? md(f.details) : null,
            f.evidence ? h('details', {}, h('summary', { class: 'small' }, 'Evidence'), md(f.evidence)) : null,
            f.reason ? h('p', { class: 'small muted' }, 'Reason: ' + f.reason) : null,
            comments(f.id)));
        }
      }

      if ((d.risk || []).length) {
        out.push(h('h2', { class: 'reading' }, 'Risks'), h('ul', { class: 'reading' }, d.risk.map(r => h('li', {}, md(r.what), r.mitigation ? h('div', { class: 'muted small' }, md(r.mitigation)) : null))));
      }
      if ((v.timeline || []).length) out.push(...timelineBlock(v));
      else if ((d.log || []).length) {
        out.push(h('h2', { class: 'reading' }, 'Log'), h('ul', { class: 'reading' }, d.log.map(l => h('li', {}, h('span', { class: 'mono small' }, l.on), ' ',
          l.step ? h('code', {}, l.step) : null, ' ', mdi(l.text), l.commit ? h('code', {}, ' ' + l.commit) : null))));
      }

      // A plan's whole-item comments are its chat, beside it; other items keep them here.
      if (v.kind !== 'plan') out.push(h('h2', {}, 'Comments on the whole item'), comments('', true, true));
      // The to-do sits beside the article, and wraps under it when narrow.
      main.replaceChildren(h('article', { class: 'article' }, out), h('aside', { class: 'todo-aside' }, todoPanel(v)));
      document.body.classList.toggle('focus', focusOn());
      renderBar(v, decisions, ans);
      if (review.el.open) drawReview();
      if (location.hash && !render.scrolled) { const t = document.getElementById(location.hash.slice(1)); if (t) t.scrollIntoView(); render.scrolled = true; }
    }

    // What the user has to do, and what is not done yet, in one place at the
    // top: nobody should have to read the whole plan to find their part.
    const WHAT = { decision: 'Answer', approval: 'Approve or disapprove', 'plan-approval': 'Approve the plan',
                   step: 'Do', disapproved: 'Disapproved' };
    function jump(local) {
      return (e) => {
        e.preventDefault();
        if (staleApproval(view, local)) return openReview();
        const t = document.getElementById(local || 'plan-approval');
        if (!t) return;
        if (t.tagName === 'DETAILS') t.open = true;
        t.scrollIntoView({ behavior: 'smooth', block: 'center' });
        t.classList.remove('flash'); void t.offsetWidth; t.classList.add('flash');
      };
    }
    function todoPanel(v) {
      const s = v.summary, user = v.user || 'you';
      const now = s.for_user || [];
      const open = s.open || [];
      const later = open.filter(x => x.by === 'user' && !x.ready && !x.ticked);
      const panel = h('section', { class: 'todo card', id: 'todo' });
      if (v.kind === 'plan' && v.folder !== 'done') panel.append(pauseRow(v));
      panel.append(h('h2', {}, now.length ? `For you now (${now.length})` : 'Nothing waits on you right now'));
      if (now.length) {
        panel.append(h('ul', { class: 'todo-list' }, now.map(x => h('li', {},
          h('a', { class: 'todo-now', href: '#' + (x.local || 'plan-approval'), onclick: jump(x.local) },
            h('span', { class: 'todo-what' }, badge(WHAT[x.what] || x.what, x.what === 'step' ? 'user' : x.what === 'decision' ? 'open' : 'gate'),
              x.local ? h('span', { class: 'id' }, x.local) : null),
            mdi(x.text))))));
      }
      if (later.length) {
        panel.append(h('h3', {}, `Coming up for you (${later.length})`),
          h('ul', { class: 'todo-list' }, later.map(x => h('li', {},
            h('a', { href: '#' + x.id, onclick: jump(x.id) }, h('span', { class: 'mono small' }, x.id)), ' ', mdi(x.title),
            h('span', { class: 'muted small' }, ' — waits on ' + x.waiting_on.join(', '))))));
      }
      if ((v.data.phase || []).length) panel.append(progress(v));
      if (v.kind === 'plan') panel.append(chatPanel(v));
      else if (s.steps.total && !open.length) panel.append(h('p', { class: 'muted' }, 'Every step is done or dropped.'));
      return panel;
    }

    // Progress beside the plan (p-pw7ft8 s7-1): each phase with done/total and a
    // bar, its steps with their state and a link to each, the active one marked.
    // Folded at phone width, where the panel sits above the plan.
    function progress(v) {
      const open = new Map((v.summary.open || []).map(x => [x.id, x]));
      const phases = v.data.phase || [];
      let done = 0, total = 0;
      const groups = phases.map(ph => {
        const steps = ph.step || [];
        const fin = steps.filter(s => s.state === 'done' || s.state === 'dropped').length;
        done += fin; total += steps.length;
        const active = steps.some(s => s.state === 'active');
        const det = h('details', { class: 'prog-phase' + (active ? ' has-active' : '') + (fin === steps.length ? ' finished' : '') },
          h('summary', {},
            h('span', { class: 'prog-title' }, h('span', { class: 'mono small muted' }, ph.id + ' '), mdi(ph.title)),
            h('span', { class: 'prog-count mono small' }, `${fin}/${steps.length}`),
            h('span', { class: 'prog-bar', 'aria-hidden': 'true' },
              h('span', { style: `width:${steps.length ? Math.round(100 * fin / steps.length) : 0}%` }))),
          h('ul', { class: 'prog-steps' }, steps.map(s => {
            const o = open.get(s.id) || {};
            const state = o.ticked && s.state !== 'done' ? 'ticked' : (s.state || 'open');
            return h('li', { class: 'prog-step ' + state },
              h('span', { class: 'prog-dot', title: state, 'aria-label': state }),
              h('a', { href: '#' + s.id, onclick: jump(s.id) }, h('span', { class: 'mono small' }, s.id), ' ', mdi(s.title)),
              s.state === 'active' ? badge('active', 'open') : null,
              s.by === 'user' && s.state !== 'done' && s.state !== 'dropped' ? badge('yours', 'user') : null);
          })));
        // Open what is being worked on and what is next; finished phases stay folded.
        if (active || (fin < steps.length && !phases.some(x => (x.step || []).some(s => s.state === 'active')))) det.open = true;
        return det;
      });
      const wrap = h('details', { class: 'prog', id: 'wr-progress' },
        h('summary', {}, h('h3', {}, 'Progress ', h('span', { class: 'mono small muted' }, `${done}/${total} steps`))),
        ...groups);
      wrap.open = !window.matchMedia('(max-width: 900px)').matches;
      return wrap;
    }

    // The chat beside a plan (p-pw7ft8, decision `chat`): its whole-plan comments
    // as a conversation, the agent's replies and what it did under each. The
    // agent reads it while it works the plan with /work-plan.
    function localTime(s) {
      const d = new Date(s || '');
      return isNaN(d) ? (s || '') : d.toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
    }

    function chatPanel(v) {
      const list = ((v.answers || {}).comments || []).filter(c => !c.on);
      const log = h('div', { class: 'chat-log' });
      for (const c of list) {
        log.append(h('div', { class: 'chat-msg mine' }, h('div', { class: 'chat-meta mono small' }, `${c.id} · ${localTime(c.at)}`), md(c.text)));
        for (const r of c.replies || []) {
          log.append(h('div', { class: 'chat-msg agent' }, h('div', { class: 'chat-meta mono small' }, `agent · ${localTime(r.at)}`), md(r.text)));
        }
        if (c.resolved) {
          log.append(h('div', { class: 'chat-msg agent done' }, h('div', { class: 'chat-meta mono small' }, `done · ${localTime(c.resolved.at)}`), md(c.resolved.note || '')));
        } else if (!(c.replies || []).length) {
          log.append(h('div', { class: 'chat-wait muted small' }, 'waiting for the agent'));
        }
      }
      const key = 'chat:' + v.id;
      const ta = draftBox(h('textarea', { rows: 2, placeholder: 'Ask the agent to change the plan, or ask it something…' }), key, '');
      const send = async () => {
        if (!ta.value.trim()) return;
        try { await api('POST', `${base}/comment/${v.id}`, { on: '', text: ta.value }); ta.value = ''; drafts.delete(key); etag = null; load(true); }
        catch (e) { toast('Not sent: ' + e.message); }
      };
      ta.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } });
      const box = h('section', { class: 'chat', id: 'wr-chat' },
        h('h3', {}, 'Chat with the agent'),
        list.length ? log : h('p', { class: 'muted small' }, 'Ask for a change to this plan. The agent answers while it works the plan with /work-plan.'),
        ta, h('div', { class: 'row chat-send' }, h('span', { class: 'muted small' }, 'Ctrl+Enter sends'), h('button', { class: 'primary', onclick: send }, 'Send')));
      requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; });
      return box;
    }

    // Pause and Continue (p-pw7ft8, decision `pause`): the agent working the plan
    // finishes the step it is on, then waits until the plan is continued.
    function pauseButton(v) {
      const paused = Boolean(v.summary.paused);
      return h('button', { class: 'pause-btn' + (paused ? ' primary' : ''), title: paused ? 'Let the agent carry on' : 'The agent finishes the step it is on, then waits',
        onclick: async () => {
          try { await api('PUT', `${base}/pause/${v.id}`, { paused: !paused }); } catch (e) { toast(e.message); }
          load(true);
        } }, paused ? '▶ Continue' : '⏸ Pause');
    }

    function pauseRow(v) {
      const p = v.summary.paused;
      if (p) {
        return h('div', { class: 'pause-row paused' },
          h('div', {}, badge('paused', 'warn'), ' ',
            h('span', { class: 'small' }, `since ${new Date(p.at).toLocaleString()}. The agent finishes the step it is on, then waits.`)),
          pauseButton(v));
      }
      return h('div', { class: 'pause-row' },
        h('span', { class: 'muted small' }, 'The agent working this plan carries on until you pause it.'),
        pauseButton(v));
    }

    function planApproval(v) {
      const pa = v.plan_approval || { status: 'none' };
      const box = h('div', { class: 'gatebox card ' + (pa.status === 'approved' ? 'approved' : pa.status === 'disapproved' ? 'disapproved' : ''), id: 'plan-approval' });
      const tag = pa.status === 'approved' ? badge('approved', 'ok') : pa.status === 'disapproved' ? badge('disapproved', 'gate') : badge('gate', 'gate');
      box.append(h('div', { class: 'box-head' }, h('h3', {}, 'The plan as a whole'), tag));
      box.append(h('p', { class: 'status' }, statusText(pa, 'these phases and steps')));
      if (pa.status === 'stale') box.append(reviewButton());
      else box.append(approveRow(pa, 'approval/' + v.id, 'Approve plan'));
      return box;
    }

    function statusText(a, what) {
      if (a.status === 'approved') return `You approved ${what} at ${a.at}.` + (a.note ? ` Note: ${a.note}` : '');
      if (a.status === 'disapproved') return `You disapproved ${what} at ${a.at}.` + (a.note ? ` Note: ${a.note}` : '');
      if (a.status === 'stale') {
        const c = a.changes || {};
        return `You ${a.verdict} ${what} at ${a.at}. ` + (c.available ? `Since then: ${c.summary}.` : `It has changed since. ${c.summary || ''}.`);
      }
      return `Not approved yet.`;
    }

    function approveRow(a, path, label) {
      const key = 'note:' + path;
      const note = draftBox(h('input', { type: 'text', placeholder: 'A note for the agent (say why when you disapprove)' }), key, a.note);
      const verdict = (a.status === 'approved' || a.status === 'disapproved') ? a.status : null;
      const hint = h('div', { class: 'kv' }, verdict ? 'The note saves as you type.' : 'The note is sent with Approve or Disapprove.');
      // `sha256`: the text this answer is about; the service refuses it if that has changed.
      const send = (v) => { const body = { verdict: v, note: note.value, sha256: a.current, __draft: note.value }; save(path, body, undefined, key); };
      if (verdict) {
        let t = null;
        note.addEventListener('input', () => { clearTimeout(t); say('typing…'); t = setTimeout(() => {
          save(path, { verdict, note: note.value, sha256: a.current, __draft: note.value }, 'quiet', key); }, 600); });
      }
      const row = h('div', { class: 'row' },
        h('button', { class: 'approve' + (a.status === 'approved' ? ' on' : ''), onclick: () => send('approved') }, a.status === 'approved' ? 'Approved' : (label || 'Approve')),
        h('button', { class: 'disapprove' + (a.status === 'disapproved' ? ' on' : ''), onclick: () => send('disapproved') }, a.status === 'disapproved' ? 'Disapproved' : 'Disapprove'),
        (a.status !== 'none') ? h('button', { class: 'link', onclick: () => save(path, { verdict: null }, undefined, key) }, 'withdraw') : null);
      return h('div', {}, note, hint, row);
    }

    function decisionCard(dec, ans) {
      const local = dec.id;
      const a = (ans.decisions || {})[local] || { selected: [], text: '' };
      const resolved = (dec.resolved || []).length > 0;
      const recs = Array.isArray(dec.recommend) ? dec.recommend : (dec.recommend ? [dec.recommend] : []);
      const answered = (a.selected || []).length || (a.text || '').trim();
      const card = h('section', { class: 'card' + (resolved ? ' resolved' : answered ? ' is-answered' : ''), id: local });
      card.append(h('div', { class: 'card-top' }, resolved ? badge('resolved', 'ok') : answered ? badge('answered', 'ok') : badge('open', 'open'),
        h('span', { class: 'id' }, local)), h('h3', {}, mdi(dec.question)));
      if (dec.context) card.append(md(dec.context, 'context'));
      if (dec.as_of) card.append(h('div', { class: 'kv' }, 'as checked on ' + dec.as_of));
      const fs = h('fieldset', { disabled: resolved || null });
      const type = dec.multiple ? 'checkbox' : 'radio';
      const inputs = [];
      for (const o of dec.option || []) {
        const inp = h('input', { type, name: 'd-' + local, value: o.id });
        inp.checked = (resolved ? dec.resolved : (a.selected || [])).includes(o.id);
        inputs.push(inp);
        fs.append(h('label', { class: 'opt' }, inp, h('span', {}, mdi(o.label),
          recs.includes(o.id) ? h('span', { class: 'rec' }, '★ recommended') : null,
          o.hint ? h('span', { class: 'hint' }, mdi(o.hint)) : null)));
      }
      let ta = null;
      if (dec.own_answer !== false) {
        ta = draftBox(h('textarea', { 'aria-label': 'Your own answer or a note', placeholder: 'Or write your own answer, or add a note' }), 'decision:' + local, a.text);
        fs.append(ta);
      }
      const push = (quiet) => save(`decision/${v_id()}/${local}`, { selected: inputs.filter(i => i.checked).map(i => i.value), text: ta ? ta.value : '', __draft: ta ? ta.value : '' }, quiet, 'decision:' + local);
      inputs.forEach(i => i.addEventListener('change', () => push()));
      if (ta) { let t = null; ta.addEventListener('input', () => { clearTimeout(t); say('typing…'); t = setTimeout(() => push('quiet'), 500); }); }
      card.append(fs);
      if (dec.because) card.append(h('p', { class: 'small muted' }, 'Recommended because: ', mdi(dec.because)));
      if (resolved) card.append(h('div', { class: 'gatebox approved' }, h('strong', {}, 'Outcome'), ' ', dec.resolved_on ? h('span', { class: 'muted small' }, dec.resolved_on) : null, md(dec.outcome || '')));
      if (a.at && !resolved) card.append(h('div', { class: 'kv' }, 'your answer, saved ' + a.at + (a.source ? ' (carried over from ' + a.source + ')' : '')));
      card.append(comments(local));
      return card;
    }

    function v_id() { return view.id; }

    // "Only what is open": done and dropped steps, resolved decisions and the
    // long reading (sections, log) fold away. Remembered per browser.
    function focusOn() { try { return localStorage.getItem('planning-focus') === '1'; } catch (e) { return false; } }
    function setFocus(on) {
      try { localStorage.setItem('planning-focus', on ? '1' : '0'); } catch (e) { /* private window */ }
      document.body.classList.toggle('focus', on);
    }

    function reportsBlock(reports) {
      const how = { about: 'about this plan', step: 'about a step', named: 'named by this plan' };
      const rows = reports.map(r => {
        if (r.missing) {
          return h('li', { class: 'report-row missing' }, h('code', {}, r.id),
            badge('missing', 'warn'), h('div', { class: 'muted small' }, `${r.named_in} names it, but there is no such report`));
        }
        const title = r.broken ? h('span', {}, r.title || r.file) : h('a', { href: `/r/${slug}/i/${r.id}` }, mdi(r.title));
        const meta = [r.links.map(l => l === 'step' ? `about ${r.step}` : how[l]).join(', ')];
        if (r.updated) meta.push(String(r.updated));
        return h('li', { class: 'report-row' + (r.broken ? ' broken' : '') }, title, ' ',
          r.broken ? badge('cannot be read', 'warn') : r.state === 'draft' ? badge('draft', 'muted') : null,
          r.step ? h('a', { class: 'small', href: '#' + r.step }, ' ', r.step) : null,
          h('div', { class: 'muted small' }, meta.join(' · ')),
          r.broken ? h('div', { class: 'small' }, `${r.file}: ${r.problem}`) : r.summary ? h('div', { class: 'muted small' }, mdi(r.summary)) : null);
      });
      return h('section', { class: 'reports-block card', id: 'reports' },
        h('h2', {}, 'Reports ', h('span', { class: 'h-meta' }, String(reports.length))), h('ul', { class: 'reports' }, rows));
    }

    // The steps whose protocol is open: kept across re-renders, so an open one
    // follows the agent's new entries as the page reloads. An active step's
    // opens by itself (decision `logs`: live) unless the user closed it.
    const openProtocols = new Set();
    const closedProtocols = new Set();

    // The plan's timeline (decision `logs`): its [[log]] and every step's notes,
    // newest first, as the service merges them.
    function timelineBlock(v) {
      const rows = v.timeline.map(r => h('li', { class: 'tl-row tl-' + r.kind },
        h('div', { class: 'tl-head' },
          h('span', { class: 'mono small muted' }, r.kind === 'log' ? r.day : r.at.slice(0, 16)),
          r.step ? h('a', { class: 'mono small', href: '#' + r.step, onclick: jump(r.step) }, r.step) : null,
          badge(r.kind === 'log' ? 'log' : 'note', r.kind === 'log' ? 'ok' : 'muted'),
          r.commit ? h('code', { class: 'small' }, r.commit) : null),
        md(r.text)));
      const more = v.timeline_older ? h('p', { class: 'muted small' },
        `and ${v.timeline_older} older; each step's protocol has them all.`) : null;
      return [h('h2', { class: 'reading', id: 'wr-timeline' }, 'Timeline ',
                h('span', { class: 'h-meta' }, 'the log and every note, newest first')),
              h('ul', { class: 'timeline' }, rows), more];
    }

    function stepProtocol(s) {
      const p = (view.protocols || {})[s.id];
      if (!p) return null;
      const body = h('div', { class: 'protocol-body' }, h('p', { class: 'muted small' }, 'loading…'));
      const when = p.last ? ', last at ' + p.last.slice(11, 16) : '';
      const det = h('details', { class: 'protocol' },
        h('summary', { class: 'small' }, `Protocol (${p.entries} ${p.entries === 1 ? 'entry' : 'entries'}${when})`), body);
      const live = s.state === 'active';
      if (live) { det.classList.add('protocol-live'); body.classList.add('following'); }
      async function fill() {
        // Following: stay at the newest line, unless the reader scrolled up.
        const atEnd = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
        try {
          const r = await api('GET', `/api/r/${slug}/protocol/${view.id}/${s.id}`);
          body.replaceChildren(md(r.text, 'protocol-text'));
        } catch (e) { body.replaceChildren(h('p', { class: 'small' }, 'cannot load the protocol: ' + e.message)); }
        if (live && (atEnd || !body.dataset.seen)) { body.scrollTop = body.scrollHeight; body.dataset.seen = '1'; }
      }
      det.addEventListener('toggle', () => {
        if (det.open) { openProtocols.add(s.id); closedProtocols.delete(s.id); fill(); }
        else { openProtocols.delete(s.id); if (live) closedProtocols.add(s.id); }
      });
      if (live && !closedProtocols.has(s.id)) openProtocols.add(s.id);
      if (openProtocols.has(s.id)) { det.open = true; fill(); }
      return det;
    }

    function stepReports(s) {
      // A report about this step, also at the step it is about.
      const mine = (view.reports || []).filter(r => r.step === s.id && !r.missing);
      if (!mine.length) return null;
      return h('div', { class: 'kv step-reports' }, 'reports: ', ...mine.map((r, i) => [i ? ', ' : '',
        r.broken ? h('span', {}, r.title || r.file, ' ', badge('cannot be read', 'warn'))
          : h('a', { href: `/r/${slug}/i/${r.id}` }, mdi(r.title))]));
    }

    function stepCard(s, ans) {
      const state = s.state || 'open';
      const tick = (ans.steps || {})[s.id];
      const li = h('li', { class: 'step ' + state, id: s.id });
      const head = h('div', { class: 'head' });
      // Every step gets a real checkbox. The user ticks their own; the agent's
      // are set in the file, so theirs only show the state (active: half-filled).
      const mine = s.by === 'user' && state !== 'dropped';
      const cb = h('input', { type: 'checkbox', class: 'stepbox', 'aria-label': mine ? 'done' : `${state} (set by the agent in the file)`,
                              title: mine ? 'Tick when you have done it' : `${state}: the agent sets this in the file` });
      cb.checked = state === 'done' || (mine && tick && tick.state === 'done');
      cb.indeterminate = state === 'active';
      if (mine) cb.addEventListener('change', () => save(`step/${view.id}/${s.id}`, { state: cb.checked ? 'done' : 'open' }));
      else {
        // Not `disabled`: that greys a done step out. A click just does nothing.
        cb.setAttribute('aria-readonly', 'true');
        cb.addEventListener('click', e => e.preventDefault());
        cb.classList.add('readonly');
      }
      head.append(cb);
      head.append(h('span', { class: 't' }, mdi(s.title)),
        h('span', { class: 'badges' }, badge(s.by, s.by), s.gate ? badge('gate', 'gate') : null,
          state !== 'open' ? badge(state, state === 'done' ? 'ok' : state === 'blocked' ? 'warn' : 'muted') : null,
          h('span', { class: 'mono small muted' }, s.id)));
      li.append(head);
      if (tick && tick.state === 'done' && state !== 'done') li.append(h('div', { class: 'kv' }, 'done in the page; not yet in the file'));
      const meta = [];
      if ((s.needs || []).length) meta.push('needs ' + s.needs.join(', '));
      if (s.commit) meta.push('commit ' + s.commit);
      if (meta.length) li.append(h('div', { class: 'kv' }, meta.join(' · ')));
      if (s.reason) li.append(h('div', { class: 'small' }, h('strong', {}, state + ': '), s.reason));
      const proto = (view.protocols || {})[s.id];
      if (state === 'active' && proto && proto.last_note) {
        // What the agent is doing now, in its own last note.
        li.append(h('div', { class: 'kv now' }, h('strong', {}, 'now: '), mdi(proto.last_note.split('\n')[0])));
      }
      if (s.details) li.append(md(s.details, 'small'));
      if (s.done_when) li.append(h('div', { class: 'small muted' }, 'Done when: ', mdi(s.done_when)));
      const reps = stepReports(s);
      if (reps) li.append(reps);
      const protoEl = stepProtocol(s);
      if (protoEl) li.append(protoEl);
      if (s.gate) {
        const a = (view.approvals || {})[s.id] || { status: 'none' };
        const box = h('div', { class: 'gatebox ' + (a.status === 'approved' ? 'approved' : a.status === 'disapproved' ? 'disapproved' : '') });
        box.append(h('div', { class: 'small' }, h('strong', {}, 'Needs your approval before it starts. '), statusText(a, 'this step')));
        const finished = state === 'done' || state === 'dropped';
        if (a.status === 'stale' && !finished) box.append(reviewButton());
        else if (!finished) box.append(approveRow(a, `approval/${view.id}/${s.id}`));
        li.append(box);
      }
      li.append(comments(s.id));
      return li;
    }

    // ---- reviewing what changed since an answer -------------------------- //

    // In the popup, not on the page: the difference can be longer than the plan
    // is worth scrolling past, and an answer should pass through it. A <dialog>
    // outside `main`, which render() replaces whenever the file changes.
    const review = { el: document.getElementById('review'), seen: '', changed: false, answered: false, acted: false };
    const LAYOUT_KEY = 'planning-review-layout';
    function reviewLayout() {
      try { return localStorage.getItem(LAYOUT_KEY) === 'sides' ? 'sides' : 'inline'; } catch (e) { return 'inline'; }
    }
    function setReviewLayout(value) {
      try { localStorage.setItem(LAYOUT_KEY, value); } catch (e) { /* private window */ }
      drawReview();
    }
    function isOpenStep(s) { return s && s.state !== 'done' && s.state !== 'dropped'; }
    function allSteps(v) { return (v.data.phase || []).flatMap(ph => ph.step || []); }
    function staleSteps(v) {
      return allSteps(v).filter(s => s.gate && isOpenStep(s) && ((v.approvals || {})[s.id] || {}).status === 'stale');
    }
    function stalePlan(v) { return (v.plan_approval || {}).status === 'stale' ? v.plan_approval : null; }
    function staleApproval(v, local) {
      if (!v) return false;
      if (!local) return Boolean(stalePlan(v));
      return staleSteps(v).some(s => s.id === local);
    }
    // What the reader has in front of them: when this changes, the text did.
    function reviewFingerprint(v) {
      const pa = stalePlan(v);
      return JSON.stringify([pa ? pa.current : '', staleSteps(v).map(s => [s.id, v.approvals[s.id].current])]);
    }
    function reviewButton() {
      return h('div', { class: 'row' }, h('button', { class: 'primary', onclick: openReview }, 'Review changes'));
    }
    function openReview() {
      review.seen = reviewFingerprint(view);
      review.changed = false; review.answered = false; review.acted = false;
      drawReview();
      if (!review.el.open) review.el.showModal();
      document.documentElement.classList.add('modal-open');
      const body = review.el.querySelector('.review-body');
      if (body) { body.scrollTop = 0; body.focus(); }
    }
    review.el.addEventListener('close', () => document.documentElement.classList.remove('modal-open'));
    // A click on the backdrop lands on the dialog itself.
    review.el.addEventListener('click', e => { if (e.target === review.el) review.el.close(); });

    const FIELD_NAMES = { title: 'Title', goal: 'Goal', details: 'Details', done_when: 'Done when', by: 'Who does it',
                          gate: 'Gate', needs: 'Needs', state: 'State' };
    function shortValue(x) {
      if (x === null || x === undefined || x === '') return '(none)';
      if (x === true) return 'yes';
      if (x === false) return 'no';
      return Array.isArray(x) ? (x.length ? x.join(', ') : '(none)') : String(x);
    }
    function proseChange(prose) {
      const sides = reviewLayout() === 'sides';
      const pane = (label, nodes) => h('div', { class: 'rv-pane' }, h('div', { class: 'rv-label' }, label), h('div', { class: 'rv-text' }, ...nodes));
      const part = (kind, text) => kind === 'del' ? h('del', {}, text) : kind === 'add' ? h('ins', {}, text) : document.createTextNode(text);
      if (prose.rewritten) {
        return h('div', { class: 'rv-sides' + (sides ? ' two' : '') },
          pane('Before', [h('del', {}, prose.old)]), pane('After', [h('ins', {}, prose.new)]));
      }
      if (!sides) return h('div', { class: 'rv-text' }, ...prose.runs.map(([k, x]) => part(k, x)));
      return h('div', { class: 'rv-sides two' },
        pane('Before', prose.runs.filter(([k]) => k !== 'add').map(([k, x]) => part(k, x))),
        pane('After', prose.runs.filter(([k]) => k !== 'del').map(([k, x]) => part(k, x))));
    }
    function fieldChange(f) {
      const name = FIELD_NAMES[f.field] || f.field;
      if (f.prose) return h('div', { class: 'rv-field' }, h('div', { class: 'rv-name' }, name), proseChange(f.prose));
      return h('div', { class: 'rv-field' }, h('div', { class: 'rv-name' }, name),
        h('div', { class: 'rv-text' }, h('del', {}, shortValue(f.old)), ' → ', h('ins', {}, shortValue(f.new))));
    }
    // A whole step, for one that was added or removed, or when no earlier text was kept.
    function wholeStep(s) {
      const shown = ['details', 'done_when', 'by', 'gate', 'needs'].filter(k => s[k] !== undefined && s[k] !== null && !(k === 'gate' && !s[k]) && !(Array.isArray(s[k]) && !s[k].length));
      return shown.map(k => h('div', { class: 'rv-field' }, h('div', { class: 'rv-name' }, FIELD_NAMES[k] || k), h('div', { class: 'rv-text' }, shortValue(s[k]))));
    }
    function stepHead(id, title, tags) {
      return h('div', { class: 'rv-step-head' }, h('span', { class: 'id' }, id), h('span', { class: 'rv-step-title' }, title || ''), ...tags);
    }
    const STATUS_BADGE = { added: 'ok', removed: 'gate', moved: 'open' };
    // One step's change. A gated step that waits for an answer is compared with
    // that answer, and has its own row here, so it is read and answered once.
    function stepChange(c, v) {
      const a = staleSteps(v).some(s => s.id === c.id) ? v.approvals[c.id] : null;
      const tags = [];
      if (c.status === 'added' || c.status === 'removed') tags.push(badge(c.status, STATUS_BADGE[c.status]));
      if (c.moved) tags.push(badge(c.from_phase ? `moved from ${c.from_phase}` : 'moved', 'open'));
      if (a) tags.push(badge('gate', 'gate'));
      const box = h('section', { class: 'rv-step' }, stepHead(c.id, c.title, tags));
      if (c.step) box.append(...wholeStep(c.step));
      const fields = a && a.changes && a.changes.available ? a.changes.fields : c.fields;
      for (const f of fields || []) box.append(fieldChange(f));
      if (a) box.append(stepAnswer(c.id, a));
      return box;
    }
    function stepAnswer(id, a) {
      const box = h('div', { class: 'rv-answer' }, h('div', { class: 'rv-name' }, `Your answer for this gate (you ${a.verdict} it at ${a.at})`));
      if (a.changes && !a.changes.available) box.append(h('p', { class: 'small muted' }, a.changes.summary + '.'));
      box.append(reviewRow(a, `approval/${view.id}/${id}`));
      return box;
    }
    function reviewRow(a, path, label) {
      const row = approveRow(a, path, label);
      row.addEventListener('click', e => { if (e.target.closest('button')) { review.answered = true; review.acted = true; } });
      return row;
    }
    function samePart(list, what) {
      if (!list.length) return null;
      return h('details', { class: 'rv-same' }, h('summary', {}, `${list.length} ${what}${list.length > 1 ? 's' : ''} did not change`),
        h('ul', {}, list.map(x => h('li', {}, h('span', { class: 'id' }, x.id), ' ', x.title || ''))));
    }
    function planChanges(pa, v) {
      const out = [];
      const c = pa.changes || {};
      if (!c.available) {
        // Never as if nothing changed: the text as it is now, to answer on.
        out.push(h('p', { class: 'rv-note' }, (c.summary || 'The difference is unavailable') + '. This is the text as it is now.'));
        for (const ph of v.data.phase || []) {
          out.push(h('h3', { class: 'rv-phase' }, ph.title || ph.id, ' ', h('span', { class: 'h-meta mono' }, ph.id)));
          if (ph.goal) out.push(h('div', { class: 'rv-field' }, h('div', { class: 'rv-name' }, 'Goal'), h('div', { class: 'rv-text' }, ph.goal)));
          for (const s of ph.step || []) out.push(h('section', { class: 'rv-step' }, stepHead(s.id, s.title, []), ...wholeStep(s)));
        }
        return out;
      }
      if (!c.count) out.push(h('p', { class: 'rv-note' }, 'No field differs from what you answered on. Answer again to record it.'));
      const same = [];
      for (const ph of c.phases || []) {
        if (ph.status === 'same') { same.push(...(ph.same_steps || [])); continue; }
        const tags = [];
        if (ph.status === 'added' || ph.status === 'removed') tags.push(badge(ph.status, STATUS_BADGE[ph.status]));
        if (ph.moved) tags.push(badge('moved', 'open'));
        out.push(h('h3', { class: 'rv-phase' }, ph.title || ph.id, ' ', h('span', { class: 'h-meta mono' }, ph.id), ' ', ...tags));
        for (const f of ph.fields || []) out.push(fieldChange(f));
        for (const s of ph.steps || []) out.push(stepChange(s, v));
        const fold = samePart(ph.same_steps || [], 'step');
        if (fold) out.push(fold);
      }
      const fold = samePart(same, 'other step');
      if (fold) out.push(fold);
      return out;
    }
    function drawReview() {
      const v = view, pa = stalePlan(v), steps = staleSteps(v);
      const now = reviewFingerprint(v);
      // A change that follows the reader's own click is their answer, not a new text.
      if (review.seen !== now) { review.changed = !review.acted; review.seen = now; }
      review.acted = false;
      const body = h('div', { class: 'review-body', tabindex: '-1' });
      const old = review.el.querySelector('.review-body');
      const top = old ? old.scrollTop : 0;
      const hadFocus = old && old.contains(document.activeElement);
      const layout = reviewLayout();
      const choice = (value, label) => h('button', { type: 'button', class: layout === value ? 'on' : '', 'aria-pressed': String(layout === value), onclick: () => setReviewLayout(value) }, label);
      const parts = [];
      if (pa && pa.changes) parts.push(pa.changes.summary);
      const inTree = new Set(pa && pa.changes && pa.changes.available ? pa.changes.phases.flatMap(ph => ph.steps.map(s => s.id)) : []);
      const alone = steps.filter(s => !inTree.has(s.id));
      if (!pa) for (const s of steps) parts.push(`${s.id}: ${v.approvals[s.id].changes.summary}`);
      review.el.replaceChildren(
        h('header', { class: 'review-head' },
          h('div', { class: 'review-title' }, h('h2', { id: 'review-title' }, 'What changed'),
            h('div', { class: 'small muted' }, parts.join(' · ') || v.data.title)),
          h('div', { class: 'review-tools' },
            h('div', { class: 'rv-switch', role: 'group', 'aria-label': 'How to show changed text' }, choice('inline', 'Inline'), choice('sides', 'Side by side')),
            h('button', { type: 'button', class: 'rv-close', onclick: () => review.el.close() }, 'Close'))),
        body);
      if (review.changed && (pa || steps.length)) body.append(h('p', { class: 'rv-note', role: 'status' }, 'The plan changed while you were reading. This is the newest text.'));
      if (!pa && !steps.length) {
        body.append(h('p', { class: 'rv-note', role: 'status' }, review.answered
          ? 'All answered. You can close this.'
          : 'Nothing here waits for an answer any more: the text was changed back, or it was answered elsewhere.'));
      }
      // Gated steps the plan's comparison does not list: their own, each once.
      for (const s of alone) body.append(stepChange({ id: s.id, title: s.title, fields: [] }, v));
      if (pa) {
        body.append(...planChanges(pa, v));
        body.append(h('div', { class: 'rv-answer rv-foot' }, h('div', { class: 'rv-name' }, `Your answer for the plan as a whole (you ${pa.verdict} it at ${pa.at})`),
          reviewRow(pa, 'approval/' + v.id, 'Approve plan')));
      }
      body.scrollTop = top;
      if (hadFocus) body.focus();
    }

    function comments(on, openBox, alwaysOpen) {
      const list = ((view.answers || {}).comments || []).filter(c => (c.on || '') === on);
      const wrap = h('div', { class: 'comments' });
      for (const c of list) {
        wrap.append(h('div', { class: 'comment' + (c.resolved ? ' resolved' : '') },
          h('span', { class: 'mono small muted' }, `${c.id} · ${c.at}`), ' ', c.text,
          ...(c.replies || []).map(r => h('div', { class: 'small reply' }, '↳ ', mdi(r.text))),
          c.resolved ? h('div', { class: 'small' }, '✓ handled: ' + (c.resolved.note || '')) : null));
      }
      const ckey = 'comment:' + on;
      const ta = draftBox(h('textarea', { placeholder: on ? 'Comment on ' + on + ': split it, add a step, an option is missing…' : 'A comment on the whole item' }), ckey, '');
      const send = h('button', { onclick: async () => {
        if (!ta.value.trim()) return;
        try { await api('POST', `${base}/comment/${view.id}`, { on, text: ta.value }); ta.value = ''; drafts.delete(ckey); toast('Comment saved'); etag = null; load(true); }
        catch (e) { toast('Not saved: ' + e.message); }
      } }, 'Comment');
      const box = h('div', { hidden: alwaysOpen || drafts.get(ckey) ? null : true }, ta, h('div', { class: 'row' }, send));
      if (!alwaysOpen) wrap.append(h('button', { class: 'link', onclick: () => { box.hidden = !box.hidden; if (!box.hidden) ta.focus(); } }, list.length ? 'reply / comment' : 'comment'));
      wrap.append(box);
      return wrap;
    }

    // Where this plan is worked on. A worktree that is gone stays a choice until
    // the user picks another: it is reported, never silently unassigned.
    function worktreePicker(v) {
      const at = v.worktree;
      const names = v.worktrees.concat(at && at.gone ? [at.name] : []);
      const choices = [{ value: '', text: 'anywhere', n: '' }]
        .concat(names.map(name => ({ value: name, text: at && at.gone && name === at.name ? `${name} (gone)` : name, n: '' })));
      return h('div', { class: 'worktree-pick' }, h('span', { class: 'kv' }, 'worktree: '),
        picker('The worktree this plan is worked on in', choices, at ? at.name : '', async value => {
          try {
            const done = await api('PUT', `${base}/worktree/${v.id}`, { name: value || null });
            if (done && done.warning) toast(done.warning);
          } catch (e) { toast(e.message); }
          load(true);
        }),
        at ? h('span', { class: 'path' + (at.gone ? ' muted' : '') }, at.path) : null,
        at && at.gone ? badge('gone', 'warn') : null,
        v.home ? h('span', { class: 'kv' }, ` the plan lives there, on ${v.home.branch || v.home.name}`) : null);
    }

    function renderBar(v, decisions, ans) {
      const s = v.summary;
      const openDec = decisions.filter(x => !(x.resolved || []).length);
      const answeredOpen = openDec.filter(x => { const a = (ans.decisions || {})[x.id]; return a && ((a.selected || []).length || (a.text || '').trim()); });
      const parts = [];
      if (openDec.length) parts.push(`${answeredOpen.length} of ${openDec.length} open decisions answered`);
      if (s.steps.total) parts.push(`${s.steps.done}/${s.steps.total} steps done`);
      if (s.for_user.length) parts.push(`${s.for_user.length} waiting on you`);
      const focusBtn = h('button', { class: 'focus-toggle', 'aria-pressed': String(focusOn()), onclick: () => { setFocus(!focusOn()); focusBtn.setAttribute('aria-pressed', String(focusOn())); focusBtn.textContent = focusOn() ? 'Show everything' : 'Only what is open'; } },
        focusOn() ? 'Show everything' : 'Only what is open');
      const todoBtn = h('button', { onclick: () => document.getElementById('todo').scrollIntoView({ behavior: 'smooth' }) }, 'To-do');
      bar.replaceChildren(h('div', { class: 'in' }, h('span', { class: 'progress-text' }, parts.join(' · ') || 'Nothing open'),
        v.kind === 'plan' && v.folder !== 'done' ? pauseButton(v) : null,
        todoBtn, focusBtn, h('button', { class: 'primary', onclick: copyAnswers }, 'Copy answers')));
      bar.hidden = false;
    }

    async function copyAnswers() {
      const v = view, d = v.data, ans = v.answers || {};
      const decisions = v.kind === 'decision' ? [Object.assign({}, d, { id: v.id })] : (d.decision || []);
      let out = `### Answers to ${d.title} (${v.id}, ${new Date().toISOString().slice(0, 10)})\n\n`;
      for (const dec of decisions) {
        if ((dec.resolved || []).length) continue;
        const a = (ans.decisions || {})[dec.id] || {};
        const labels = (dec.option || []).filter(o => (a.selected || []).includes(o.id)).map(o => o.label);
        const parts = [];
        if (labels.length) parts.push(labels.join('; '));
        if ((a.text || '').trim()) parts.push('own answer: ' + a.text.trim().replace(/\n+/g, ' '));
        out += `- ${dec.id}: ${dec.question} — ${parts.length ? parts.join(' | ') : '(no answer)'}\n`;
      }
      for (const [local, a] of Object.entries(v.approvals || {})) {
        if (a.status !== 'none') out += `- step ${local}: ${a.status}${a.note ? ' — ' + a.note : ''}\n`;
      }
      try { await navigator.clipboard.writeText(out); toast('Copied: paste it to the agent'); }
      catch (e) { const w = window.open('', '_blank'); if (w) w.document.body.innerText = out; else alert(out); }
    }

    await load(true);
    watch(() => load(false), 'repo', slug);
    poll(() => load(false), 15000);
  }

  // ---- routing ------------------------------------------------------------- //

  function route() {
    const p = location.pathname.split('/').filter(Boolean).map(decodeURIComponent);
    // Each view lays out its own width: the lanes want it, an item reads narrow.
    document.body.dataset.view = !p.length ? 'index' : p[0] === 'r' && p.length === 2 ? 'repo'
      : p[0] === 'r' && p[2] === 'i' ? 'item' : 'page';
    if (!p.length) return viewIndex();
    if (p[0] === 'r' && p.length === 2) return viewRepo(p[1]);
    if (p[0] === 'r' && p[2] === 'i' && p[3]) return viewItem(p[1], p[3]);
    if (p[0] === 'r' && p[2] === 'm' && p.length > 3) return viewMarkdown(p[1], p.slice(3).join('/'));
    main.replaceChildren(h('p', {}, 'Nothing here. ', h('a', { href: '/' }, 'All repositories')));
  }
  route();
})();

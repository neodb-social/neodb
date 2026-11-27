(function () {
  // the last save before the page goes elsewhere; the editing code sets it, and a page with no cards has nothing to save
  let leave = () => Promise.resolve();
  // the owner can go to another year from the top bar: every year from the first finished title to this one
  (function years() {
    const D = WR.D, el = document.getElementById("wr-yr");
    if (!D.owner_view || !el || !D.make_url) return;
    const now = new Date().getFullYear(), first = D.history && D.history.first ? +D.history.first.slice(0, 4) : D.year, ys = [];
    for (let y = now; y >= Math.min(first, D.year); y--) ys.push(y);
    if (ys.length < 2) return;
    const sel = document.createElement("select");
    sel.id = "wr-yr"; sel.className = "wr-yr"; sel.setAttribute("aria-label", WR._("Year"));
    sel.innerHTML = ys.map(y => `<option${y === D.year ? " selected" : ""}>${y}</option>`).join("");
    sel.onchange = () => { leave().then(() => { location.href = D.make_url.replace(`/${D.year}/`, `/${sel.value}/`); }, () => { sel.value = String(D.year); toast(WR._("Could not save the deck; try again")); }); };
    el.replaceWith(sel);
  })();
  // a year with no finished title: say so on the stage instead of drawing empty cards
  if (WR.empty) {
    const msg = document.getElementById("wr-empty");
    const past = WR.D.year < new Date().getFullYear();
    msg.textContent = past ? WR._("Nothing was finished in {year}.", { year: WR.D.year }) : WR.D.owner_view ? WR._("Nothing finished this year yet. Mark a title as complete and the cards fill in.") : WR._("Nothing finished this year yet.");
    msg.hidden = false;
    document.querySelectorAll(".wrapped-page, #wr-dots, #wr-deck, .wr-ctl, #wr-cue, #wr-edit, #wr-publish").forEach(e => { e.hidden = true; });
    // the owner on an audience that sees none of the year's titles: links to the audiences that do
    const other = (WR.D.levels_with_titles || []).filter(lv => lv !== WR.D.level);
    if (WR.D.owner_view && other.length) {
      const label = { 0: WR._("Public"), 1: WR._("Followers only"), 2: WR._("Only me") }, nav = document.createElement("p");
      nav.className = "wr-empty-aud";
      nav.append(WR._("This audience sees none of this year's titles. Open another:"), " ");
      other.forEach((lv, k) => { const a = document.createElement("a"), u = new URL(location.href); u.searchParams.set("as", lv); a.href = u.toString(); a.textContent = label[lv]; nav.append(a); if (k < other.length - 1) nav.append(" · "); });
      msg.after(nav);
    }
    return;
  }
  const { D, _, _n, CATS, CAT_LABEL, CAT_NOUN, CAT_ICON, CAT_COLOR, MONTHS, MONTHS_LONG, MONTHS_NARROW, ROLE_LABEL, stats: S, esc, cover, fmtDate, who, trunc, SVG } = WR;
  // the owner gets the editing controls and saves to the server; a visitor sees the cards the owner left shown
  const OWNER = !!D.owner_view, URLS = D.urls || {}, CDN = document.getElementById("wr-stage").dataset.cdn || "https://cdn.jsdelivr.net";
  const CSRF = (document.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
  const $ = id => document.getElementById("wr-" + id);
  const n = S.items.length, Y = D.year, base = D.member.url.split("/users/")[0];
  // the bars each card must clear, set from 2025 production data (data-findings-2026-11-03.md and the round-nine pass rates)
  const RULE = { SMALL: 25, STREAK: 7, ACTIVE: 10, BUSY_MIN: 5, FAN: 10, GAP: 3, GAP_MARKS: 20, NEW: 0.6, OLD: 0.2, WISH: 3, WAIT: 365, TAGGED: 10 };
  // a small year keeps to these cards; the others start hidden and can be shown one by one
  const SMALL = n < RULE.SMALL, COMPACT = new Set(["intro", "covers", "top", "outro"]);
  const INK = SVG.ink, MUTED = SVG.muted, PRIMARY = SVG.primary, LINE = SVG.line;
  // a card is 4:5, 1080 by 1350; the layouts keep their top and use DY, the height added to the old square, below it
  const CW = 1080, CH = 1350, DY = CH - CW;
  const LOGO = { green: "#8EAD78", blue: "#4A9FD0", coral: "#E9795D" };
  let TINT = { green: "#eef3ea", blue: "#e8f2f8", coral: "#fbece8", paper: "#fbfcfc", own: "#f1f3f4", own2: "#eef2f5", piece: "#b1c5d1", piece2: "#a3bbc9" };
  const path = u => String(u || "").replace(base, "");
  // a day with more than 30 completions is an import, not a day of watching or reading: the time cards leave it out
  const perDay = {}; S.items.forEach(i => { perDay[i.date] = (perDay[i.date] || 0) + 1; });
  const BULK = new Set(Object.keys(perDay).filter(d => perDay[d] > TREND.BULK_DAY));
  // the deck year as one history row, built from the deck's own items so the trend cards agree with the other cards
  function yearFromItems() {
    const z = () => Array(12).fill(0), y = { m: z(), b: z(), c: {}, d: z(), r: z(), f: z(), g: z() }, days = new Set();
    S.items.forEach(it => {
      const mo = +it.date.slice(5, 7) - 1;
      // a bulk-import day counts toward nothing the cards compare, its ratings included
      if (BULK.has(it.date)) { y.b[mo]++; return; }
      if (it.rating) { y.r[mo]++; y.g[mo] += it.rating; if (it.rating === 10) y.f[mo]++; }
      y.m[mo]++; (y.c[it.category] = y.c[it.category] || z())[mo]++;
      if (!days.has(it.date)) { days.add(it.date); y.d[mo]++; }
    });
    return y;
  }
  const TR = TREND.model(D.history, Y, yearFromItems(), S.lastMonth), trIntro = TREND.introLine(TR);
  const bulkN = S.items.filter(i => BULK.has(i.date)).length, bulkDay = [...BULK].sort((a, b) => perDay[b] - perDay[a])[0];
  const bulkNote = !bulkN ? "" : BULK.size > 1 ? _n("{n} titles marked on {date} and {days} other bulk day are left out", "{n} titles marked on {date} and {days} other bulk days are left out", BULK.size - 1, { n: bulkN, date: fmtDate(bulkDay), days: BULK.size - 1 }) : _("{n} titles marked on {date} are left out", { n: bulkN, date: fmtDate(bulkDay) });
  const TM = (() => {
    const timed = S.items.filter(i => !BULK.has(i.date)), y0 = new Date(Y, 0, 1);
    const counts = MONTHS.map((z, m) => timed.filter(i => +i.date.slice(5, 7) === m + 1).length), active = counts.slice(0, D.year < new Date().getFullYear() || S.lastMonth === 0 ? S.lastMonth + 1 : S.lastMonth); // the month still running is never the quietest
    const weeks = Array(53).fill(0); timed.forEach(i => { weeks[Math.min(52, Math.floor((new Date(i.date + "T12:00:00") - y0) / 864e5 / 7))]++; });
    const mw = weeks.indexOf(Math.max(...weeks));
    return { counts, busiest: counts.indexOf(Math.max(...counts)), quietest: active.indexOf(Math.min(...active)), weeks, marathonWeek: mw, marathonStart: new Date(y0.getTime() + mw * 7 * 864e5) };
  })();

  // ---------- social stats ----------
  const mine = new Map(S.items.map(i => [path(i.url), i]));
  const friends = (D.friends || []).map(f => { const map = new Map(); f.items.forEach(i => { if (i.u && !map.has(i.u)) map.set(i.u, i); }); const shared = [...map.keys()].filter(u => mine.has(u)); return { ...f, map, shared, jaccard: shared.length / Math.max(1, (f.n || map.size) + mine.size - shared.length) }; });
  const social = { enough: friends.filter(f => f.map.size).length >= 3 };
  if (social.enough) {
    const counts = new Map(); friends.forEach(f => f.shared.forEach(u => counts.set(u, (counts.get(u) || []).concat([f]))));
    let best = null; counts.forEach((fs, u) => { if (!best || fs.length > best.fs.length) best = { u, fs }; });
    if (best && best.fs.length >= 2) social.company = { it: mine.get(best.u), fs: best.fs };
    social.twin = friends.filter(f => f.shared.length >= 2).sort((a, b) => b.jaccard - a.jaccard)[0] || null;
    let firstCount = 0, afterCount = 0; const perItem = new Map(), perFriend = new Map();
    friends.forEach(f => f.shared.forEach(u => { const m = mine.get(u), fi = f.map.get(u); if (!fi.d) return; if (m.date < fi.d) { firstCount++; perItem.set(u, (perItem.get(u) || 0) + 1); } else if (fi.d < m.date) { afterCount++; perFriend.set(f, (perFriend.get(f) || 0) + 1); } }));
    const topItem = [...perItem.entries()].sort((a, b) => b[1] - a[1])[0]; const topFriend = [...perFriend.entries()].sort((a, b) => b[1] - a[1])[0];
    social.first = { count: firstCount, it: topItem ? mine.get(topItem[0]) : null, itN: topItem ? topItem[1] : 0, after: afterCount, leader: topFriend ? topFriend[0] : null, leaderN: topFriend ? topFriend[1] : 0 };
  }
  const withMarks = S.items.filter(i => i.marks != null);
  social.obscure = withMarks.length ? withMarks.filter(i => i.marks < 10).length / withMarks.length : null;
  const dItems = S.rated.filter(i => i.dist && i.dist.some(x => x > 0) && (i.raters || 0) >= 20);
  social.crowd = dItems.filter(i => i.rating >= 9).map(i => ({ it: i, share: i.dist[4] })).sort((a, b) => b.share - a.share)[0] || null;
  social.grain = dItems.filter(i => i.rating >= 8).map(i => { const star = Math.round(i.rating / 2); const below = i.dist.slice(0, star - 1).reduce((s, x) => s + x, 0); return { it: i, below, star }; }).sort((a, b) => b.below - a.below)[0] || null;
  social.early = S.items.filter(i => i.first_rank && i.first_rank <= 10 && (i.marks || 0) >= 50).sort((a, b) => b.marks - a.marks)[0] || null;
  social.instanceTop = D.instance_top || null; social.percentile = D.percentile ?? null; social.median = D.median ?? null;

  // ---------- activity: what counts toward the streak ----------
  // progress, complete and dropped shelf changes, notes, episode comments and reviews count; wishlist adds and edits never do.
  // The server sends only the year's entries at least as visible as the page itself, so a public page never reveals private days.
  // Completions on a bulk day are an import, so they do not make that day active.
  const ACT = D.activity.filter(a => !(a.kind === "complete" && BULK.has(a.date)));
  const levelOf = v => v === "followers" ? 1 : v === "me" ? 2 : 0;
  // the audience whose cards this page shows: a visitor's own, or the one the owner views and edits (0 everyone, 1 followers, 2 the owner)
  const LEVEL = [0, 1, 2].includes(D.level) ? D.level : 2, AUD = ["public", "followers", "me"][LEVEL];
  const pad2 = x => String(x).padStart(2, "0");
  const isoDay = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const addDays = (d, k) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + k, 12);
  // days with something logged, and the longest run of them
  const ACTIVITY = (() => {
    const byDay = new Map();
    ACT.forEach(a => { const e = byDay.get(a.date) || { finish: 0, other: 0 }; if (a.kind === "complete") e.finish++; else e.other++; byDay.set(a.date, e); });
    const days = [...byDay.keys()].sort(); let run = 0, best = 0, bestEnd = null, prev = null;
    days.forEach(ds => { const d = new Date(ds + "T12:00:00"); run = prev && isoDay(addDays(prev, 1)) === ds ? run + 1 : 1; if (run > best) { best = run; bestEnd = d; } prev = d; });
    return { byDay, days: days.length, streak: best, start: bestEnd ? addDays(bestEnd, 1 - best) : null, end: bestEnd, hasOther: ACT.some(a => a.kind !== "complete") };
  })();

  // ---------- svg helpers ----------
  const T = (x, y, t, size, o) => { o = o || {}; return `<text x="${x}" y="${y}" font-size="${size}" fill="${o.fill || INK}"${o.weight ? ` font-weight="${o.weight}"` : ""}${o.anchor ? ` text-anchor="${o.anchor}"` : ""}${o.ls ? ` letter-spacing="${o.ls}"` : ""}${o.cls ? ` class="${o.cls}"` : ""}${o.d != null ? ` style="--d:${o.d}ms"` : ""}${o.count != null ? ` data-count="${o.count}" data-pre="${esc(o.pre || "")}" data-suf="${esc(o.suf || "")}"` : ""}>${esc(t)}</text>`; };
  const G = (cls, d, inner, extra) => `<g class="${cls}" style="--d:${d}ms${extra || ""}">${inner}</g>`;
  const eyebrow = t => T(80, 150, t.toUpperCase(), 30, { fill: PRIMARY, weight: 700, ls: 5, cls: "fade", d: 0 });
  // rough text width, so a headline can pick the largest size that fits; Chinese and Japanese characters are one em
  const CJK = s => /[　-鿿]/.test(s || "");
  const MC = (() => { try { return document.createElement("canvas").getContext("2d"); } catch (e) { return null; } })();
  const EST = (s, size, weight) => { s = String(s); if (MC) { MC.font = `${weight || 400} ${size}px ${SVG.font}`; return MC.measureText(s).width; } return [...s].reduce((w, ch) => w + (/[　-鿿＀-￯]/.test(ch) ? 1 : /[A-Z0-9#%+@]/.test(ch) ? 0.64 : 0.54) * size, 0); };
  const SEG = (() => { try { return typeof Intl !== "undefined" && Intl.Segmenter ? new Intl.Segmenter(undefined, { granularity: "word" }) : null; } catch (e) { return null; } })();
  // lines no wider than maxW; a text that needs two lines is split near its middle, so the second line is not one word
  function wrapW(s, size, weight, maxW, maxLines, ls) {
    s = String(s); const cjk = CJK(s), sep = cjk ? "" : " ", wd = x => EST(x, size, weight) + (ls || 0) * x.length;
    // Chinese and Japanese break between words where the browser can find them (so 身世 stays whole), else between characters
    let units = cjk ? (SEG ? [...SEG.segment(s)].map(x => x.segment) : s.match(/[\u3000-\u9fff\uff00-\uffef]|[^\s\u3000-\u9fff\uff00-\uffef]+|\s+/g) || []) : s.split(/\s+/);
    if (cjk) units = units.reduce((a, u) => { if (a.length && /^[，。、；：！？）」』”’…%\s]/.test(u)) a[a.length - 1] += u; else a.push(u); return a; }, []);
    const lines = []; let cur = "";
    units.forEach(u => { const next = cur ? cur + sep + u : u; if (cur && wd(next) > maxW) { lines.push(cur); cur = u; } else cur = next; });
    if (cur) lines.push(cur);
    if (cjk) lines.forEach((l, i) => { lines[i] = l.trim(); });
    if (lines.length === 2 && maxLines >= 2) {
      let best = null;
      for (let k = 1; k < units.length; k++) { const a = units.slice(0, k).join(sep), b = units.slice(k).join(sep), m = Math.max(wd(a), wd(b)); if (wd(a) <= maxW && wd(b) <= maxW && (!best || m < best.m)) best = { a, b, m }; }
      if (best) return [best.a.trim(), best.b.trim()];
    }
    if (lines.length <= maxLines) return lines;
    const keep = lines.slice(0, maxLines); let l = keep[maxLines - 1];
    while (l.length > 1 && wd(l + "…") > maxW) l = cjk || !l.includes(" ") ? l.slice(0, -1) : l.slice(0, l.lastIndexOf(" "));
    keep[maxLines - 1] = l + "…";
    return keep;
  }
  // one headline system: 56 px under the eyebrow, then 144 px on one line, 96 px on one or two lines, else 64 px on two lines
  function head(t, o) {
    o = o || {};
    const text = String(t), top = 206, fits = k => EST(text, k, 800) - k * 0.03 * text.length <= 920, twoAt96 = () => wrapW(text, 96, 800, 920, 3, -2.9).length <= 2;
    const size = o.size || (o.max !== 96 && fits(144) ? 144 : fits(96) || (o.max !== 96 && twoAt96()) ? 96 : 64);
    const at = o.count != null ? text.indexOf(String(o.count)) : -1, cn = String(o.count);
    const opts = { weight: 800, ls: -(size * 0.03).toFixed(1), cls: "rise", d: o.d || 100, count: at >= 0 ? o.count : null, pre: at >= 0 ? text.slice(0, at) : "", suf: at >= 0 ? text.slice(at + cn.length) : "" }, y = top + Math.round(size * 0.72);
    if (size === 144 || (size === 96 && fits(96))) return { svg: T(80, y, text, size, opts), y, size };
    const pitch = size === 96 ? 104 : 76, lines = wrapW(text, size, 800, 920, 2, -(size * 0.03));
    return { svg: lines.map((l, k) => T(80, y + k * pitch, l, size, { ...opts, count: null, d: (o.d || 100) + k * 60 })).join(""), y: y + (lines.length - 1) * pitch, size };
  }
  // the muted line or two under a headline; returns the svg and the last baseline
  function subAt(t, y, d) { const lines = wrapW(t, 36, 400, 920, 2); return { svg: lines.map((l, i) => T(80, y + i * 44, l, 36, { fill: MUTED, cls: "rise", d: (d || 250) + i * 60 })).join(""), y: y + (lines.length - 1) * 44 }; }
  // headline plus its line: the usual top of a card
  function cardTop(title, line, o) {
    const h = head(title, o), s = line ? subAt(line, h.y + (h.size === 144 ? 66 : 58)) : null;
    return { svg: eyebrow(o && o.eyebrow || "") + h.svg + (s ? s.svg : ""), y: s ? s.y : h.y };
  }
  // a one-line legend of swatches; an item is { fill } or { stroke } and a label
  function legendRows(items) {
    let x = 80, rows = 1;
    items.filter(Boolean).forEach(it => { const w = 42 + EST(it.label, 34) + 36; if (x + w > 1010 && x > 80) { x = 80; rows++; } x += w; });
    return rows;
  }
  function legend(items, y, d) {
    // a legend that wraps moves up, so its last row stays clear of the footer
    let x = 80, out = ""; y = Math.min(y, 924 + DY - (legendRows(items) - 1) * 50);
    items.filter(Boolean).forEach(it => {
      const w = 42 + EST(it.label, 34) + 36;
      if (x + w > 1010 && x > 80) { x = 80; y += 50; }
      out += swatch(it, x, y - 26) + T(x + 42, y, it.label, 34, { fill: INK });
      x += w;
    });
    return G("fade", d || 900, out);
  }
  let cid = 0;
  // diagonal stripes of `ink` over `base`: the mark for titles marked in bulk
  function hatchRect(x, y, w, h, rx, base, ink) { const id = "cp" + (cid++); return `<pattern id="${id}" patternUnits="userSpaceOnUse" width="10" height="10" patternTransform="rotate(45)"><rect width="10" height="10" fill="${base}"/><rect width="4" height="10" fill="${ink}"/></pattern><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="url(#${id})"/>`; }
  function swatch(it, x, y) {
    if (it.hatch) return hatchRect(x, y, 30, 30, 8, it.fill || "none", it.hatch);
    if (it.stripes) { const id = "cp" + (cid++), k = 30 / it.stripes.length; return `<clipPath id="${id}"><rect x="${x}" y="${y}" width="30" height="30" rx="8"/></clipPath><g clip-path="url(#${id})"${it.opacity ? ` opacity="${it.opacity}"` : ""}>${it.stripes.map((c, i) => `<rect x="${x + i * k}" y="${y}" width="${k + 0.5}" height="30" fill="${c}"/>`).join("")}</g>`; }
    return `<rect x="${x}" y="${y}" width="30" height="30" rx="8" fill="${it.fill || "none"}"${it.opacity ? ` opacity="${it.opacity}"` : ""}${it.stroke ? ` stroke="${it.stroke}" stroke-width="${it.dash ? 2 : 3}"` : ""}${it.dash ? ` stroke-dasharray="5 4"` : ""}/>`;
  }
  // fit "bottom" puts a square cover at the foot of its 2:3 box, so a row keeps one baseline; "fill" crops it to the box
  // every cover a card draws, by its item's id, so an export can load it from this site's origin (a cross-origin image would
  // block the PNG); covers from any source count: finished titles, collections, hand-picked titles, the wishlist, words cards
  const coverUuid = new Map(), uuidOf = u => (String(u || "").split("?")[0].replace(/\/$/, "").split("/").pop() || "");
  function coverSvg(it, x, y, w, h, cls, d, fit) { const id = "cp" + (cid++); if (it.cover && it.url && !coverUuid.has(it.cover)) coverUuid.set(it.cover, uuidOf(it.url)); const sq = (it.category === "music" || it.category === "podcast") && fit !== "fill"; if (sq && h > w) { if (fit === "bottom") y += h - w; h = w; } return `<g class="${cls || "pop"}" style="--d:${d || 0}ms"><clipPath id="${id}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${Math.round(w * 0.06)}"/></clipPath><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${Math.round(w * 0.06)}" fill="${CAT_COLOR[it.category] || "#ccc"}"/><image href="${esc(it.cover)}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid slice" clip-path="url(#${id})"/></g>`; }
  // a rating as stars drawn as shapes, so a half star is exact: full stars in coral, a half star grey with its left half in coral.
  // y is the baseline of the row, as for text of the same size; returns the svg and the width of the row
  const starPts = (cx, cy, R) => Array.from({ length: 10 }, (z, k) => { const a = -Math.PI / 2 + k * Math.PI / 5, r = k % 2 ? R * 0.42 : R; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; });
  const ptsStr = p => p.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(" ");
  function starRow(x, y, rating, size, d) {
    const full = Math.floor(rating / 2), half = rating % 2 === 1, R = size * 0.46, pitch = size * 1.04, cy = y - size * 0.36;
    let g = "";
    for (let k = 0; k < full + (half ? 1 : 0); k++) {
      const p = starPts(x + R + k * pitch, cy, R);
      // the left half of a star: the bottom inner point, the five points on the left, and the top
      g += half && k === full ? `<polygon points="${ptsStr(p)}" fill="${LINE}"/><polygon points="${ptsStr([p[5], p[6], p[7], p[8], p[9], p[0]])}" fill="${LOGO.coral}"/>` : `<polygon points="${ptsStr(p)}" fill="${LOGO.coral}"/>`;
    }
    return { svg: `<g class="rise" style="--d:${d}ms">${g}</g>`, w: (full + (half ? 1 : 0)) * pitch };
  }
  function wrapText(s, maxc, maxLines) { const cjk = /[　-鿿]/.test(s); const units = cjk ? s.split("") : s.split(/\s+/); const lines = []; let line = ""; for (const u of units) { const sep = cjk ? "" : " "; if ((line + sep + u).length > maxc && line) { lines.push(line); line = u; if (lines.length >= maxLines) break; } else line = line ? line + sep + u : u; } if (lines.length < maxLines && line) lines.push(line); return lines; }
  // the footer: the site's small square icon, faded, bottom left; the handle bottom right
  const ICON = { g: "M 22 16 L 74 16 A 6 6 0 0 1 80 22 L 80 38 A 6 6 0 0 1 74 44 L 71.3246 44 A 4.2 4.2 0 0 0 68.4495 51.2617 A 6.5 6.5 0 1 1 59.5505 51.2617 A 4.2 4.2 0 0 0 56.6754 44 L 37.3246 44 A 0.2 0.2 0 0 1 37.1877 43.6542 A 10.5 10.5 0 1 0 22.8123 43.6542 A 0.2 0.2 0 0 1 22.6754 44 L 22 44 A 6 6 0 0 1 16 38 L 16 22 A 6 6 0 0 1 22 16 Z", b: "M 22 48 L 22.6754 48 A 4.2 4.2 0 0 0 25.5505 40.7383 A 6.5 6.5 0 1 1 34.4495 40.7383 A 4.2 4.2 0 0 0 37.3246 48 L 38 48 A 6 6 0 0 1 44 54 L 44 56.6754 A 0.2 0.2 0 0 1 43.6542 56.8123 A 10.5 10.5 0 1 0 43.6542 71.1877 A 0.2 0.2 0 0 1 44 71.3246 L 44 74 A 6 6 0 0 1 38 80 L 22 80 A 6 6 0 0 1 16 74 L 16 54 A 6 6 0 0 1 22 48 Z", c: "M 54 48 L 56.6754 48 A 0.2 0.2 0 0 1 56.8123 48.3458 A 10.5 10.5 0 1 0 71.1877 48.3458 A 0.2 0.2 0 0 1 71.3246 48 L 74 48 A 6 6 0 0 1 80 54 L 80 74 A 6 6 0 0 1 74 80 L 54 80 A 6 6 0 0 1 48 74 L 48 71.3246 A 4.2 4.2 0 0 0 40.7383 68.4495 A 6.5 6.5 0 1 1 40.7383 59.5505 A 4.2 4.2 0 0 0 48 56.6754 L 48 54 A 6 6 0 0 1 54 48 Z", a: "M57 30.5V22 M57 30.5L65.085 27.873 M57 30.5L61.996 37.377 M57 30.5L52.004 37.377 M57 30.5L48.915 27.873" };
  let iconN = 0;
  function iconMark(x, y, size, opacity) { const id = "ik" + (iconN++); const k = size / 64; return `<g transform="translate(${x - 16 * k},${y - 16 * k}) scale(${k})" opacity="${opacity}"><defs><mask id="${id}" maskUnits="userSpaceOnUse" x="0" y="0" width="96" height="96"><rect width="96" height="96" fill="#fff"/><path d="${ICON.a}" fill="none" stroke="#000" stroke-width="4.2" stroke-linecap="round" stroke-linejoin="round"/></mask></defs><g mask="url(#${id})"><path fill="#8EAD78" d="${ICON.g}"/><path fill="#4A9FD0" d="${ICON.b}"/><path fill="#E9795D" d="${ICON.c}"/></g></g>`; }
  // the member's own picture in a 44-unit circle in the corner, where the faded site icon is when the member has not set one
  const AVATAR = D.member.avatar_card || "";
  function cornerMark() { if (!AVATAR) return iconMark(80, 968 + DY, 40, 0.45); const id = "av" + (iconN++); return `<g class="av"><clipPath id="${id}"><circle cx="100" cy="${988 + DY}" r="22"/></clipPath><circle cx="100" cy="${988 + DY}" r="22" fill="${LINE}"/><image href="${esc(AVATAR)}" x="78" y="${966 + DY}" width="44" height="44" preserveAspectRatio="xMidYMid slice" clip-path="url(#${id})"/><circle cx="100" cy="${988 + DY}" r="22" fill="none" stroke="#000" stroke-opacity="0.12" stroke-width="1.5"/></g>`; }
  // ink: the handle in the text colour, for a card whose handle sits on a shape (the outro's cover piece)
  function card(bg, body, ink) { return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${CW} ${CH}" font-family="${SVG.font}"><rect class="bg" width="${CW}" height="${CH}" fill="${bg}"/>${body}${cornerMark()}<text x="1000" y="${1006 + DY}" text-anchor="end" font-size="28" fill="${ink ? INK : MUTED}">${esc(D.member.handle)}</text></svg>`; }
  const UI = { DY, T, G, cardTop, legend, legendRows, hatchRect, wrapW, CAT_COLOR, CAT_LABEL, CAT_NOUN, CATS, INK, MUTED, LINE, get TINT() { return TINT; } };
  // the intro and the outro carry one large piece of the default cover, traced in its 1200-unit space and cropped by the card;
  // the asterisk is a second sub-path, so with fill-rule evenodd it is a hole that always shows the card background
  const PIECE = { piece: "M1436 -240.5C1294 -240.7 729.1 -240.8 587 -240.5C444.9 -240.2 584.3 -279 583.7 -238.7C583.1 -198.5 579.7 -65.9 583.5 1C587.2 67.9 601.9 133.7 606.1 162.4C610.2 191.1 607.2 169.9 608.3 173.3C609.4 176.7 610.9 180.3 612.6 182.9C614.3 185.5 616.3 187.5 618.5 189C620.6 190.5 622.9 191.5 625.6 192C628.2 192.5 631.3 192.4 634.4 191.8C637.4 191.2 639.6 190.9 643.9 188.4C648.2 185.8 656.1 180.2 660.3 176.7C664.5 173.3 663.2 174.8 669.2 167.7C675.2 160.5 689.5 141.6 696.3 133.7C703.1 125.8 705.3 124.4 709.8 120.4C714.3 116.4 718.3 113.1 723.3 109.8C728.4 106.5 734.9 102.9 740.1 100.6C745.3 98.3 749.1 97 754.4 95.9C759.7 94.8 766.3 94 771.9 94C777.4 94 782.2 94.7 787.6 96C793 97.4 798.4 99.1 804.3 102.2C810.2 105.3 817.5 110.3 822.9 114.6C828.2 118.9 831.6 121.9 836.6 127.8C841.7 133.8 848.4 142.7 853.2 150.3C858 157.8 861.7 165.2 865.3 173.1C868.9 181 872 189.1 874.8 197.7C877.5 206.3 880.1 216.4 881.8 224.6C883.6 232.9 884.5 238 885.4 247C886.2 256 887 269.2 887 278.9C887 288.5 886.1 297 885.2 304.9C884.3 312.7 883.3 318.5 881.5 326C879.6 333.5 876.9 342.8 874.3 349.9C871.7 356.9 869.4 362.1 865.8 368.3C862.2 374.5 856.9 382.1 852.6 387.2C848.4 392.3 845 395.4 840.4 398.9C835.8 402.4 829.7 406 824.9 408.3C820 410.7 816.3 411.8 811.4 412.9C806.4 413.9 800.1 414.5 795 414.6C789.9 414.7 786 414.4 780.9 413.5C775.7 412.6 768.7 410.7 764.1 409.3C759.5 407.9 758.5 407.6 753.3 405.1C748.1 402.7 741.9 400 733.1 394.4C724.3 388.7 708.1 376.3 700.4 371.1C692.8 365.9 692.6 365.6 687.3 363.1C681.9 360.7 673 357.5 668.3 356.2C663.6 354.9 661.7 355.3 659 355.4C656.3 355.4 654.4 355.6 652 356.5C649.6 357.4 646.3 359.1 644.3 360.8C642.4 362.5 641.3 363.9 640.1 366.7C639 369.6 637.8 373.3 637.5 378C637.3 382.7 632.2 346.9 638.7 394.7C645.3 442.5 669.9 617.4 676.7 664.9C683.4 712.3 678.4 675.3 679.4 679.1C680.3 683 680.9 684.9 682.5 688C684.1 691.1 686.5 695.1 688.8 697.7C691 700.3 692.7 701.7 696 703.5C699.3 705.2 704.4 707.2 708.4 708.1C712.4 708.9 670 715.2 720 708.6C770 702 957.9 675.4 1008.1 668.7C1058.3 661.9 1016.9 667.7 1021.1 668.3C1025.4 668.8 1030.5 670.5 1033.5 672C1036.5 673.5 1037.7 675.3 1039.1 677.4C1040.5 679.5 1041.4 681.7 1042 684.5C1042.5 687.3 1042.7 690.6 1042.4 694C1042.1 697.4 1042.4 699.2 1040.2 704.7C1038.1 710.2 1034.8 718.4 1029.5 727C1024.1 735.6 1013.6 748.3 1008.1 756.6C1002.5 764.9 999.3 770.6 996.1 776.7C993 782.9 990.8 788.8 989.1 793.6C987.3 798.3 986.6 801.1 985.6 805.3C984.7 809.5 983.9 814.5 983.5 819C983.2 823.5 983.2 828.1 983.5 832C983.8 835.9 984.1 838.4 985.1 842.4C986.2 846.3 987.8 851.7 989.7 855.9C991.5 860 993.4 863.4 996.2 867.3C999 871.2 1002.9 875.8 1006.3 879.3C1009.7 882.7 1012.8 885.2 1016.6 887.9C1020.3 890.7 1022.3 892.6 1028.7 895.9C1035.2 899.1 1047.9 904.6 1055.3 907.3C1062.7 910.1 1067.2 911 1073.3 912.4C1079.4 913.7 1082.9 914.8 1092 915.6C1101.1 916.5 1115.9 917.8 1128 917.5C1140.1 917.2 1152.2 916 1164.6 914.1C1176.9 912.1 1156.8 907.1 1202 905.7C1247.2 904.3 1396.5 906 1436 905.7C1475.5 905.3 1438.6 1094.2 1439.2 903.8C1439.8 713.3 1439.6 -46.5 1439.5 -237C1439.4 -427.5 1439.4 -238.8 1438.9 -239.4C1438.3 -239.9 1578 -240.3 1436 -240.5Z", star: "M1052 50.4C1050.4 50.4 1048.4 50.7 1046.8 51.3C1045.2 51.9 1043.5 53 1042.4 53.9C1041.3 54.8 1040.8 55.4 1040.1 56.7C1039.4 58 1038.6 59.9 1038.2 61.6C1037.9 63.2 1037.4 60.2 1038.1 66.4C1038.8 72.7 1042 93.1 1042.6 99C1043.2 104.9 1042.2 101.1 1041.8 101.6C1041.3 102.1 1046.1 102.8 1039.7 101.9C1033.3 101 1010.1 96.9 1003.3 96.1C996.5 95.3 1000.1 96.7 998.8 97.3C997.4 97.9 996.2 98.6 995.1 99.6C994.1 100.5 993.2 101.7 992.5 103C991.8 104.3 991.2 106 990.9 107.3C990.6 108.6 990.5 109.7 990.7 110.9C990.8 112.2 991.1 113.5 991.7 114.9C992.3 116.2 993.2 118 994.3 119.2C995.4 120.4 996.1 121.3 998.3 122.1C1000.6 123 1001.9 123.3 1007.9 124.5C1013.8 125.7 1029.5 128.2 1034.3 129.1C1039.1 130.1 1036.4 129.8 1036.8 130.5C1037.1 131.1 1039.1 127.8 1036.5 133C1033.9 138.2 1024.1 156 1021.3 161.7C1018.5 167.4 1019.9 165.3 1019.7 167.1C1019.5 169 1019.6 171 1020 172.6C1020.3 174.2 1020.9 175.3 1021.9 176.6C1022.8 178 1024.3 179.5 1025.9 180.5C1027.4 181.5 1029.1 182.2 1031.1 182.4C1033.2 182.6 1036.5 182.1 1038.2 181.7C1039.9 181.3 1040.5 180.6 1041.4 179.9C1042.4 179.2 1042.8 178.8 1043.9 177.4C1044.9 175.9 1044.9 176.6 1047.8 171.3C1050.7 166 1058.6 150.2 1061.1 145.7C1063.7 141.2 1062.4 144.3 1063.1 144.2C1063.8 144.1 1061.3 141.4 1065.3 145.3C1069.2 149.2 1082.2 163.2 1086.7 167.7C1091.2 172.2 1090.8 171.2 1092.3 172.1C1093.9 173.1 1094.9 173.1 1096.1 173.3C1097.4 173.5 1098.6 173.5 1099.9 173.4C1101.2 173.2 1102.7 172.9 1103.9 172.4C1105.2 171.8 1106.6 170.9 1107.6 170.1C1108.6 169.2 1109.2 168.8 1109.9 167.4C1110.7 166 1111.6 163.2 1112 161.6C1112.3 159.9 1112.1 158.9 1111.9 157.6C1111.7 156.3 1111.7 155.5 1110.7 153.9C1109.7 152.2 1110.2 152.3 1105.9 147.6C1101.6 142.9 1088.3 129.6 1084.7 125.7C1081.1 121.8 1084 124.7 1084.1 124.1C1084.3 123.5 1080.1 125.1 1085.7 122.1C1091.3 119.2 1111.7 109.5 1117.6 106.1C1123.6 102.8 1120.6 103.4 1121.6 102C1122.5 100.6 1123 99.6 1123.3 97.9C1123.5 96.1 1123.6 93.3 1123.1 91.4C1122.6 89.4 1121.7 87.7 1120.4 86.1C1119.1 84.6 1117 83.1 1115.3 82.3C1113.6 81.5 1112.2 81.2 1110 81.4C1107.8 81.6 1108 80.7 1101.9 83.4C1095.7 86 1078.1 95.1 1073 97.5C1067.9 99.9 1071.8 97.9 1071.3 97.6C1070.8 97.2 1070.9 100.6 1070.1 95.4C1069.2 90.2 1067 72.5 1066.1 66.4C1065.1 60.4 1064.9 60.8 1064.4 59.1C1063.9 57.5 1063.8 57.5 1063 56.5C1062.2 55.4 1060.8 53.7 1059.7 52.9C1058.6 52 1057.7 51.6 1056.4 51.2C1055.1 50.8 1053.6 50.4 1052 50.4Z" };
  const piece = (place, fill, d, sx, sy) => `<g class="slot" style="--sx:${sx}px;--sy:${sy}px;--d:0ms"><path class="piece" transform="${place}" fill="${fill}" fill-rule="evenodd" d="${d}"/></g>`;

  // ---------- the cards ----------
  // the cover-and-column layout of the single-title cards: a 2:3 cover up to `w` wide, a column of lines to its right
  const label = t => ({ t: t.toUpperCase(), size: 30, fill: PRIMARY, weight: 700, ls: 3, lines: 1, after: 6 });
  const titleRow = (t, size) => ({ t, size: size || 44, weight: 700, lines: 2 });
  const line = (t, size, fill) => ({ t, size: size || 34, fill: fill || MUTED, lines: 2 });
  const featureW = (y, w) => Math.min(w || 360, Math.floor((890 + DY - y) / 1.5));
  function feature(it, y, rows, d, w) {
    w = featureW(y, w);
    const x = 80 + w + 40, colW = 1000 - x;
    let out = coverSvg(it, 80, y, w, w * 1.5, "pop", d || 300), ty = null, after = 0;
    rows.filter(Boolean).forEach((r, k) => {
      if (r.gap) { after += r.gap; return; }
      const size = r.size || 34;
      if (r.stars != null) { ty = ty == null ? y + Math.round(size * 0.74) : ty + after + 16 + Math.round(size * 0.74); out += starRow(x, ty, r.stars, size, (d || 300) + 100 + k * 60).svg; after = Math.round(size * 0.26) + (r.after || 0); return; }
      wrapW(r.t, size, r.weight || 400, colW, r.lines || 2, r.ls).forEach((l, j) => {
        ty = ty == null ? y + Math.round(size * 0.74) : j ? ty + Math.round(size * 1.3) : ty + after + 16 + Math.round(size * 0.74);
        out += T(x, ty, l, size, { fill: r.fill || INK, weight: r.weight, ls: r.ls, cls: "rise", d: (d || 300) + 100 + k * 60 });
      });
      after = Math.round(size * 0.26) + (r.after || 0);
    });
    return out;
  }
  // a row of covers as wide as the card allows, at most `max` of them
  function coverRow(its, y, max, d) {
    const list = its.slice(0, max), g = 20, w = Math.min(215, (920 - g * (list.length - 1)) / list.length);
    return { svg: list.map((it, i) => coverSvg(it, 80 + i * (w + g), y, w, w * 1.5, "pop", (d || 300) + i * 90, "bottom")).join(""), w, bottom: y + w * 1.5 };
  }
  function computed() {
    const cards = [];
    const ok = (cond, reason) => cond ? { ok: true } : { ok: false, reason };
    const introLine = (S.lastMonth < 11 ? _n("{titles} across {n} shelf, so far", "{titles} across {n} shelves, so far", S.cats.length, { titles: _n("{n} title", "{n} titles", n) }) : _n("{titles} across {n} shelf", "{titles} across {n} shelves", S.cats.length, { titles: _n("{n} title", "{n} titles", n) }));
    cards.push({ id: "intro", name: _("Intro"), kind: "computed", interest: { ok: true }, bg: () => TINT.own, body: () => `${piece("translate(35 -35) scale(0.9)", TINT.piece, PIECE.piece + PIECE.star, 60, -60)}${eyebrow(_("Annual summary"))}${String(Y).split("").map((ch, i) => T(80 + i * 180, 560 + DY / 2, ch, 300, { weight: 800, cls: "rise", d: 100 + i * 120 })).join("")}${T(80, 680 + DY / 2, _("{name}’s year", { name: D.member.name }), 48, { weight: 700, ls: -1, cls: "rise", d: 600 })}${T(80, 740 + DY / 2, introLine, 34, { fill: MUTED, cls: "rise", d: 700 })}${trIntro ? T(80, 790 + DY / 2, trIntro[0].toUpperCase() + trIntro.slice(1), 34, { fill: MUTED, cls: "rise", d: 800 }) : ""}` });
    // a small year: every cover on one card, in the order finished, as large as the space allows
    cards.push({ id: "covers", name: _("Your year in covers"), kind: "computed", interest: ok(SMALL, _("only in a small year, under {n} titles", { n: RULE.SMALL })), bg: () => TINT.paper, body: () => {
      const its = S.items, N = its.length, t = cardTop(_n("{n} title", "{n} titles", N), "", { eyebrow: _("Your year in covers"), count: N });
      const g = N > 12 ? 14 : 24, AW = 920, top0 = t.y + 60, AH = 900 + DY - top0; let best = null;
      for (let cols = 1; cols <= N; cols++) { const rows = Math.ceil(N / cols), w = Math.min((AW - g * (cols - 1)) / cols, (AH - g * (rows - 1)) / rows / 1.5); if (!best || w > best.w) best = { cols, rows, w }; }
      const { cols, rows, w } = best, h = w * 1.5, x0 = 80 + (AW - (cols * w + (cols - 1) * g)) / 2, y0 = top0 + (AH - (rows * h + (rows - 1) * g)) / 2;
      return t.svg + its.map((it, k) => coverSvg(it, x0 + (k % cols) * (w + g), y0 + Math.floor(k / cols) * (h + g), w, h, "pop", 250 + k * Math.min(90, 1400 / N), "bottom")).join("");
    } });
    const droppedN = () => ACT.filter(a => a.kind === "dropped").length;
    cards.push({ id: "shelves", name: _("Shelves"), kind: "computed", interest: { ok: true }, bg: () => TINT.green, body: () => {
      const t = cardTop(_n("{n} title", "{n} titles", n), "", { eyebrow: _("Completed"), count: n }), bmax = Math.max(...S.cats.map(c => S.byCat[c].length)), pitch = Math.min(S.cats.length > 6 ? 96 : 112, Math.floor((800 + DY - t.y) / S.cats.length));
      let y = t.y + 50, bars = "";
      S.cats.forEach((c, i) => {
        const k = S.byCat[c].length, w = 40 + (k / bmax) * 540, bw = S.byCat[c].filter(it => BULK.has(it.date)).length / k * w;
        bars += G("growx", 200 + i * 70, `<rect x="80" y="${y}" width="${w}" height="46" rx="12" fill="${CAT_COLOR[c]}"/>${bw ? hatchRect(80 + w - bw, y, bw, 46, 12, "none", "rgba(255,255,255,0.6)") : ""}`)
          + T(80 + w + 20, y + 34, k, 34, { weight: 800, cls: "fade", d: 500 + i * 70 }) + T(80 + w + 30 + EST(k, 34, 800), y + 34, CAT_NOUN[c], 34, { fill: MUTED, cls: "fade", d: 500 + i * 70 });
        y += pitch;
      });
      const notes = [bulkN ? { fill: "#b3bfc6", hatch: "rgba(255,255,255,0.6)", label: _("{n} marked in bulk on {date}", { n: bulkN, date: fmtDate(bulkDay) }) } : null, droppedN() ? { fill: "none", stroke: MUTED, label: _("{n} dropped, not counted", { n: droppedN() }) } : null];
      return t.svg + bars + (notes.some(Boolean) ? legend(notes, Math.max(y + 34, 860 + DY)) : "");
    } });
    const mean = TM.counts.reduce((a, b) => a + b, 0) / Math.max(1, S.lastMonth + 1);
    cards.push({ id: "busiest", name: _("Busiest month"), kind: "computed", interest: ok(TM.counts[TM.busiest] >= RULE.BUSY_MIN && TM.counts[TM.busiest] >= 1.6 * mean, TM.counts[TM.busiest] < RULE.BUSY_MIN ? _n("the busiest month has {n} title, under {min}", "the busiest month has {n} titles, under {min}", TM.counts[TM.busiest], { min: RULE.BUSY_MIN }) : _("{month} is only {x} times the monthly mean, under 1.6", { month: MONTHS_LONG[TM.busiest], x: Math.floor(TM.counts[TM.busiest] / mean * 100) / 100 })), bg: () => TINT.coral, body: () => {
      const t = cardTop(MONTHS_LONG[TM.busiest], _("{n} titles in one month; {month} was the quietest with {quiet}", { n: TM.counts[TM.busiest], month: MONTHS_LONG[TM.quietest], quiet: TM.counts[TM.quietest] }), { eyebrow: _("Busiest month") });
      const mmax = Math.max(...TM.counts), bottom = (bulkNote ? 812 : 858) + DY, room = bottom - Math.max(t.y + 80, 560);
      let spark = "";
      TM.counts.forEach((v, m) => { const h = 24 + (v / mmax) * room, x = 80 + m * 78; spark += G("growy", 300 + m * 60, `<rect x="${x}" y="${bottom - h}" width="56" height="${h}" rx="12" fill="${m === TM.busiest ? LOGO.coral : "#e6d9d4"}"/>`) + T(x + 28, bottom + 44, MONTHS_NARROW[m], 34, { fill: m === TM.busiest ? INK : MUTED, weight: m === TM.busiest ? 700 : 400, anchor: "middle" }) + (m === TM.busiest ? T(x + 28, bottom - h - 14, v, 34, { weight: 800, anchor: "middle", cls: "fade", d: 900 }) : ""); });
      return t.svg + spark + (bulkNote ? T(80, 912 + DY, bulkNote, 34, { fill: MUTED, cls: "fade", d: 900 }) : "");
    } });
    const tc = S.topCat, share = S.byCat[tc].length / n, bestT = S.bestOf[tc];
    // one sentence per shelf, because the verb differs
    const N_ = x => x, VERB = { book: N_("{n} read this year, {pct}% of everything"), movie: N_("{n} watched this year, {pct}% of everything"), tv: N_("{n} watched this year, {pct}% of everything"), music: N_("{n} listened to this year, {pct}% of everything"), game: N_("{n} played this year, {pct}% of everything"), podcast: N_("{n} followed this year, {pct}% of everything"), performance: N_("{n} attended this year, {pct}% of everything") };
    cards.push({ id: "biggest", name: _("Biggest shelf"), kind: "computed", interest: ok(S.cats.length > 1 && share >= 0.4, S.cats.length > 1 ? _("the biggest shelf holds only {pct}%", { pct: Math.round(share * 100) }) : _("only one shelf this year")), bg: () => TINT.blue, body: () => {
      const t = cardTop(CAT_LABEL[tc], _(VERB[tc], { n: S.byCat[tc].length, pct: Math.round(share * 100) }), { eyebrow: _("Biggest shelf") });
      return t.svg + feature(bestT, Math.max(t.y + 50, 440), [label(_("Highest mark")), titleRow(bestT.title), bestT.rating ? { stars: bestT.rating, size: 40 } : line(who(bestT))], 500, 380);
    } });
    // five stars: all of them up to ten; past ten, the ten with a comment first, then the less-marked, then the latest
    const FS = S.fiveStar, fsMany = FS.length > RULE.FAN, said = i => i.comment && i.comment.length > 2 ? 1 : 0;
    const top = FS.length >= 3 ? (fsMany ? FS.slice().sort((a, b) => said(b) - said(a) || (a.marks ?? 1e9) - (b.marks ?? 1e9) || b.date.localeCompare(a.date)).slice(0, RULE.FAN).sort((a, b) => a.date.localeCompare(b.date)) : FS.slice(0, RULE.FAN)) : S.top.slice(0, 8);
    cards.push({ id: "top", name: FS.length >= 3 ? _("Five stars") : _("Top shelf"), kind: "computed", interest: ok(S.rated.length >= 3 || top.length >= 5, _("fewer than three rated titles")), bg: () => TINT.paper, body: () => {
      const line1 = FS.length >= 3 ? fsMany ? _("{n} titles got five stars; here are ten, the ones you wrote about first", { n: FS.length }) : _("Everything that got five stars this year") : S.topLabel;
      const t = cardTop(FS.length >= 3 ? _("{n} favourites", { n: FS.length }) : _("The highest marks"), line1, { eyebrow: FS.length >= 3 ? _("Five stars") : _("Top shelf") });
      // four or fewer get large covers; up to eight keep the wide fan; nine or ten use smaller covers, so the outer covers stay on the card
      const N = top.length, wide = N <= 8, W = N <= 4 ? 280 : wide ? 215 : 190, H = W * 1.5, step = N <= 4 ? 150 : wide ? 95 : 76, cx = 540, rot = i => N > 1 ? -14 + 28 * i / (N - 1) : 0, base = 870 + DY / 2 - H - (wide ? 49 : 63);
      let fan = ""; top.forEach((it, i) => { const k = i - (N - 1) / 2; const x = cx + k * step - W / 2, y = base + Math.abs(k) * 14; fan += `<g transform="translate(${x + W / 2},${y + H}) rotate(${rot(i)}) translate(${-(x + W / 2)},${-(y + H)})">${coverSvg(it, x, y, W, H, "fanin", 300 + i * 90)}</g>`; });
      return t.svg + fan;
    } });
    const q = S.quiet;
    cards.push({ id: "quiet", name: _("Quiet favourite"), kind: "computed", interest: ok(q && q.marks != null && q.marks < 20, q ? _("your favourites all have 20 or more marks here") : _("no top mark to pick from")), bg: () => TINT.coral, body: () => {
      const t = cardTop(q.title, "", { eyebrow: _("The quiet favourite") });
      return t.svg + feature(q, t.y + 56, [line(who(q)), { stars: q.rating, size: 44 }, { gap: 20 }, line(q.marks ? _n("from you, and only {n} other mark on {site}.", "from you, and only {n} other marks on {site}.", q.marks, { site: S.site }) : _("from you, and no other mark on {site}.", { site: S.site }), 34, INK)], 300, 420);
    } });
    // stricter than the shared stats: 3 points (of 10) from the site average, on a title with 20 or more marks
    // (the mock has mark counts, not rating counts; the production check used ratings)
    const gapped = S.rated.filter(i => i.site_rating && (i.marks || 0) >= RULE.GAP_MARKS).map(i => ({ it: i, gap: i.rating - i.site_rating }));
    const dis = gapped.slice().sort((a, b) => a.gap - b.gap)[0] || null, sur = gapped.slice().sort((a, b) => b.gap - a.gap)[0] || null;
    // both marks on one scale of 10, as the site shows an average: the site's average as a number, the member's own rating as stars
    function gapScale(x, y, w, you, avg, d) {
      const px = v => x + (v / 10) * w; let g = `<line x1="${x}" y1="${y}" x2="${x + w}" y2="${y}" stroke="${LINE}" stroke-width="6" stroke-linecap="round"/>`;
      for (let k = 0; k <= 10; k++) g += `<line x1="${px(k)}" y1="${y - (k % 2 ? 5 : 8)}" x2="${px(k)}" y2="${y + (k % 2 ? 5 : 8)}" stroke="${LINE}" stroke-width="3"/>` + (k % 2 ? "" : T(px(k), y + 48, k, 34, { fill: MUTED, anchor: "middle" }));
      g += G("growx", d, `<line x1="${px(Math.min(you, avg))}" y1="${y}" x2="${px(Math.max(you, avg))}" y2="${y}" stroke="${you < avg ? LOGO.coral : LOGO.green}" stroke-width="8" stroke-linecap="round"/>`);
      // each label sits over its dot; when the two would touch, the left one ends at its dot and the right one starts at its dot
      const siteText = _("{site} {value}", { site: S.site, value: avg.toFixed(1) }), youText = _("you"), size = 34, gapW = 10;
      const sw = EST(siteText, size, 400), tw = EST(youText, size, 700), stars = starRow(0, 0, you, size, 0).w, yw = tw + gapW + stars;
      let sx = px(avg) - sw / 2, yx = px(you) - yw / 2;
      if (Math.max(sx, yx) < Math.min(sx + sw, yx + yw) + 24) {
        if (avg < you) { sx = px(avg) - sw; yx = px(you); } else { yx = px(you) - yw; sx = px(avg); }
      }
      const fit = (v, wid) => Math.min(Math.max(v, x - 10), x + w + 10 - wid);
      sx = fit(sx, sw); yx = fit(yx, yw);
      // a label pushed back from the end of the scale can reach the other one; then the left one moves further left
      if (avg < you && sx + sw + 16 > yx) sx = fit(yx - 16 - sw, sw);
      if (you < avg && yx + yw + 16 > sx) yx = fit(sx - 16 - yw, yw);
      g += G("pop", d + 150, `<circle cx="${px(avg)}" cy="${y}" r="14" fill="#fff" stroke="${MUTED}" stroke-width="4"/>`) + T(sx, y - 30, siteText, size, { fill: MUTED, anchor: "start", cls: "fade", d: d + 200 });
      g += G("pop", d + 250, `<circle cx="${px(you)}" cy="${y}" r="16" fill="${INK}"/>`) + T(yx, y - 30, youText, size, { fill: INK, weight: 700, anchor: "start", cls: "fade", d: d + 300 }) + starRow(yx + tw + gapW, y - 30, you, size, d + 300).svg;
      return g;
    }
    cards.push({ id: "disagree", name: _("Where you disagreed"), kind: "computed", interest: ok((dis && dis.gap <= -RULE.GAP) || (sur && sur.gap >= RULE.GAP), _("no rating {gap} points (of 10) from the site average on a title with {n} or more marks", { gap: RULE.GAP, n: RULE.GAP_MARKS })), bg: () => TINT.blue, body: () => {
      const t = cardTop(_("Against the grain"), "", { eyebrow: _("Where you disagreed") });
      const rows = [[dis && dis.gap <= -RULE.GAP ? dis : null, _("The disappointment")], [sur && sur.gap >= RULE.GAP ? sur : null, _("The surprise")]].filter(([g]) => g);
      let b = t.svg;
      if (rows.length === 1) {
        // one title: a large cover, and the scale across the card
        const [[g, lbl]] = rows, it = g.it, y = t.y + 60;
        b += feature(it, y, [label(lbl), titleRow(it.title, 48), line(who(it))], 300, 320);
        b += gapScale(110, y + 480 + 90, 860, it.rating, it.site_rating, 600);
        return b;
      }
      rows.forEach(([g, lbl], i) => {
        const it = g.it, y = t.y + 70 + i * 410, d = 300 + i * 250;
        b += coverSvg(it, 80, y, 180, 270, "pop", d) + T(300, y + 30, lbl.toUpperCase(), 30, { fill: PRIMARY, weight: 700, ls: 3, cls: "fade", d: d + 50 })
          + wrapText(it.title, CJK(it.title) ? 16 : 28, 2).map((l, k) => T(300, y + 80 + k * 48, l, 40, { weight: 700, cls: "rise", d: d + 100 })).join("")
          + gapScale(320, y + 210, 650, it.rating, it.site_rating, d + 200);
      });
      return b;
    } });
    // a streak of seven days or more is the headline; a shorter one becomes a line on the active-days card
    const sAt = ACTIVITY, streakMode = sAt.streak >= RULE.STREAK, nd = k => _n("{n} day", "{n} days", k);
    const FIN = LOGO.green, OTH = LOGO.blue, EMPTY = "#dfe5dc";
    function activeDays(a) {
      const step = 27, cell = 21, x0 = 162, span = a.streak >= 2 ? _("with something logged; the longest run, outlined, was {n} days, {start} to {end}", { n: a.streak, start: fmtDate(isoDay(a.start)), end: fmtDate(isoDay(a.end)) }) : _("with something logged; never two days in a row");
      const t = cardTop(nd(a.days), span, { eyebrow: _("Active days"), count: a.days }), y0 = t.y + 44 + Math.round(DY / 3), rowH = Math.min(36, (836 + DY - y0) / (S.lastMonth + 1));
      let grid = "", fin = 0, oth = 0;
      a.byDay.forEach(e => { if (e.finish) fin++; else oth++; });
      for (let m = 0; m <= S.lastMonth; m++) {
        const y = y0 + m * rowH, dim = new Date(Y, m + 1, 0).getDate(); let row = m % 2 ? "" : T(80, y + 20, MONTHS[m], 34, { fill: MUTED }); // every other month, as on the week chart
        for (let k = 1; k <= dim; k++) {
          const d = new Date(Y, m, k, 12), e = a.byDay.get(isoDay(d)), inRun = a.start && d >= a.start && d <= a.end && a.streak >= 2;
          row += `<rect x="${x0 + (k - 1) * step}" y="${y}" width="${cell}" height="${Math.min(cell, rowH - 6)}" rx="5" fill="${e ? (e.finish ? FIN : OTH) : EMPTY}"${inRun ? ` stroke="${INK}" stroke-width="3"` : ""}/>`;
        }
        grid += G("fade", 250 + m * 50, row);
      }
      return t.svg + grid + legend([{ fill: FIN, label: _n("{n} day finishing a title", "{n} days finishing a title", fin) }, a.hasOther ? { fill: OTH, label: _n("{n} day of progress and comments", "{n} days of progress and comments", oth) } : null], 900 + DY);
    }
    cards.push({ id: "streak", name: streakMode ? _("Streak") : _("Active days"), kind: "computed", interest: streakMode || sAt.days >= RULE.ACTIVE ? { ok: true } : { ok: false, reason: _("{days} with something logged, under {min}, and the longest run is {run}", { days: nd(sAt.days), min: RULE.ACTIVE, run: nd(sAt.streak) }) }, bg: () => TINT.green, body: () => {
      if (!streakMode) return activeDays(sAt);
      const a = sAt, v = { start: fmtDate(isoDay(a.start)), end: fmtDate(isoDay(a.end)) };
      const t = cardTop(nd(a.streak), a.hasOther ? _("something logged every day, {start} to {end}", v) : _("a title finished every day, {start} to {end}", v), { eyebrow: _("Longest streak"), count: a.streak });
      // 44 day squares: the run outlined in the middle, a few days either side; a long run shows its last 44 days
      const cells = 44, len = a.streak, first = len <= cells - 4 ? addDays(a.start, -Math.floor((cells - len) / 2)) : addDays(a.end, 1 - cells);
      const offset = Math.round((a.start - first) / 864e5), gy = t.y + 50 + Math.round(DY / 3);
      let grid = "";
      for (let k = 0; k < cells; k++) {
        const d = addDays(first, k), e = a.byDay.get(isoDay(d)), inRun = d >= a.start && d <= a.end, x = 80 + (k % 11) * 86, y = gy + Math.floor(k / 11) * 80;
        grid += G("pop", inRun ? 350 + Math.max(0, k - offset) * 35 : 250, `<rect x="${x}" y="${y}" width="66" height="66" rx="16" fill="${e ? (e.finish ? FIN : OTH) : EMPTY}"${inRun ? ` stroke="${INK}" stroke-width="4"` : ""}/>`);
      }
      const last = addDays(first, cells - 1);
      return t.svg + grid + T(80, gy + 4 * 80 + 24, _("{start} to {end}", { start: fmtDate(isoDay(first)), end: fmtDate(isoDay(last)) }), 34, { fill: MUTED }) + legend([{ fill: FIN, label: _("a title finished") }, a.hasOther ? { fill: OTH, label: _("progress or a comment") } : null, { fill: EMPTY, label: _("nothing logged") }, { stroke: INK, label: _("the run") }], 900 + DY);
    } });
    cards.push({ id: "weeks", name: _("Week by week"), kind: "computed", interest: ok(n >= 30, _("{n} titles is too few for a week strip", { n })), bg: () => TINT.paper, body: () => {
      const s0 = TM.marathonStart, t = cardTop(_("{n} in a week", { n: TM.weeks[TM.marathonWeek] }), _("the busiest week began {date}; {n} weeks of the year had a title finished", { date: fmtDate(isoDay(s0)), n: TM.weeks.filter(v => v).length }), { eyebrow: _("Week by week") });
      const wmax = Math.max(...TM.weeks), bottom = 820 + DY, room = bottom - (t.y + 90);
      let strip = "";
      TM.weeks.forEach((v, i) => { const h = v ? 16 + v / wmax * room : 6; strip += `<rect x="${80 + i * 17.4}" y="${bottom - h}" width="13" height="${h}" rx="4" fill="${i === TM.marathonWeek ? LOGO.coral : v ? PRIMARY : LINE}"/>`; });
      return t.svg + G("draw", 300, strip) + MONTHS.map((m, i) => i % 2 ? "" : T(80 + i * 4.42 * 17.4, bottom + 44, m, 34, { fill: MUTED })).join("") + legend([{ fill: PRIMARY, label: _("titles finished that week") }, { fill: LOGO.coral, label: _("the busiest week") }], 912 + DY);
    } });
    const dl = S.decadeList, span = dl.length ? dl[dl.length - 1].decade - dl[0].decade : 0, domShare = dl.length ? Math.max(...dl.map(d => d.n)) / dl.reduce((s, d) => s + d.n, 0) : 1;
    // new or old: the share of titles released in the deck year or the two years before it
    const dated = S.items.filter(i => i.year && i.year > 1800 && i.year <= Y + 1), recent = dated.length ? dated.filter(i => i.year >= Y - 2).length / dated.length : 0, rpct = Math.round(recent * 100);
    const eraMode = recent >= RULE.NEW ? "new" : recent <= RULE.OLD ? "old" : "mid";
    cards.push({ id: "era", name: _("The era"), kind: "computed", interest: ok(dated.length >= 10 && (eraMode === "new" || (span >= 15 && domShare <= 0.8)), dated.length < 10 ? _("fewer than ten titles with a release year") : span < 15 ? _("release years span under 15 years") : _("{pct}% of titles come from one decade", { pct: Math.round(domShare * 100) })), bg: () => TINT.blue, body: () => {
      const bottom = 812 + DY;
      let db = "", t;
      if (eraMode === "new") {
        // the last ten years one by one, everything older in one bar; the last three years in blue
        t = cardTop(_("Mostly new"), _("{pct}% came out in the last three years; the oldest title is from {year}.", { pct: rpct, year: S.oldest.year }), { eyebrow: _("The era you lived in") });
        const yrs = [null, ...Array.from({ length: 10 }, (z, k) => Y - 9 + k)], cnt = yrs.map(y => dated.filter(i => y == null ? i.year < Y - 9 : i.year === y).length), cmax = Math.max(...cnt, 1), w = 920 / yrs.length, room = bottom - (t.y + 60);
        yrs.forEach((y, i) => { const h = cnt[i] ? 16 + cnt[i] / cmax * room : 6; db += G("growy", 300 + i * 50, `<rect x="${80 + i * w}" y="${bottom - h}" width="${w - 10}" height="${h}" rx="10" fill="${y != null && y >= Y - 2 ? LOGO.blue : "#d5e3ec"}"/>`) + T(80 + i * w + (w - 10) / 2, bottom + 44, y == null ? _("older") : `’${String(y).slice(2)}`, 34, { fill: MUTED, anchor: "middle" }); });
        return t.svg + db + legend([{ fill: LOGO.blue, label: _("the last three years") }, { fill: "#d5e3ec", label: _("earlier years") }], 912 + DY);
      }
      const lineEra = eraMode === "old" ? _("Only {pct}% came out in the last three years. Half your year was made before {year}.", { pct: rpct, year: S.medianYear }) : _("Half your year was made before {year}, and {pct}% came out in the last three years.", { pct: rpct, year: S.medianYear });
      t = cardTop(S.medianYear, lineEra, { eyebrow: _("The era you lived in") });
      const dmax = Math.max(...dl.map(d => d.n)), w = Math.min(140, 930 / dl.length), room = bottom - (t.y + 60);
      // a label wider than its bar (as in some languages) goes on every other decade, the highlighted one always
      const hi = Math.floor(S.medianYear / 10) * 10, labs = dl.map(d => _("{d}s", { d: String(d.decade).slice(2) })), lw = Math.max(...labs.map(l => EST(l, 34))), every = lw > w - 6 ? 2 : 1, hk = dl.findIndex(d => d.decade === hi);
      dl.forEach((d, i) => { const h = 24 + d.n / dmax * room, cx = Math.min(1000 - lw / 2, Math.max(80 + lw / 2, 80 + i * w + (w - 10) / 2)); db += G("growy", 300 + i * 50, `<rect x="${80 + i * w}" y="${bottom - h}" width="${w - 10}" height="${h}" rx="10" fill="${d.decade === hi ? LOGO.blue : "#d5e3ec"}"/>`) + ((i - hk) % every === 0 ? T(cx, bottom + 44, labs[i], 34, { fill: d.decade === hi ? INK : MUTED, anchor: "middle" }) : ""); });
      return t.svg + db + legend([{ fill: LOGO.blue, label: _("the decade of {year}", { year: S.medianYear }) }, { fill: "#d5e3ec", label: _("titles by decade of release") }], 912 + DY);
    } });
    const ppl = S.creators.concat(S.actors).slice(0, 5);
    cards.push({ id: "people", name: _("People"), kind: "computed", interest: ok(ppl.length >= 1, _("nobody appears on two titles")), bg: () => TINT.green, body: () => {
      const t = cardTop(_("On repeat"), "", { eyebrow: _("People you kept returning to") }), pitch = Math.min(150, (880 + DY - t.y - 60) / ppl.length);
      return t.svg + ppl.map((p, i) => { const yy = t.y + 100 + i * pitch; return G("pop", 300 + i * 100, `<circle cx="124" cy="${yy - 14}" r="44" fill="#dfe5dc"/>` + T(124, yy + 2, p.name.trim().slice(0, 1).toUpperCase(), 42, { weight: 800, anchor: "middle" })) + T(196, yy - 10, trunc(p.name, CJK(p.name) ? 16 : 28), 42, { weight: 700, cls: "rise", d: 350 + i * 100 }) + T(196, yy + 32, [ROLE_LABEL[p.roles.filter(r => r !== "person")[0]] || "", _n("{n} title", "{n} titles", p.n)].filter(Boolean).join(" · "), 34, { fill: MUTED, cls: "rise", d: 400 + i * 100 }); }).join("");
    } });
    return cards;
  }
  function socialCards() {
    const cards = []; const ok = (cond, reason) => cond ? { ok: true } : { ok: false, reason };
    const need = social.enough ? null : _("fewer than three followed members with visible marks this year");
    const initials = (fs, x, y, d) => fs.slice(0, 7).map((f, i) => G("pop", d + i * 80, `<circle cx="${x + i * 76}" cy="${y}" r="32" fill="#dfe5dc" stroke="#fff" stroke-width="4"/>` + T(x + i * 76, y + 11, f.name.trim().slice(0, 1).toUpperCase(), 30, { weight: 800, anchor: "middle" }))).join("");
    const c = social.company;
    cards.push({ id: "company", name: _("In good company"), kind: "social", interest: need ? { ok: false, reason: need } : ok(!!c, _("no title shared with two or more followed members")), bg: () => TINT.green, body: () => {
      const t = cardTop(_("You and {n}", { n: c.fs.length }), _("people you follow finished the same title this year"), { eyebrow: _("In good company") });
      // the initials of up to seven members sit right of the cover, so the cover keeps 360 at most
      const fy0 = t.y + 56, cw = featureW(fy0, 360);
      return t.svg + feature(c.it, fy0, [titleRow(c.it.title), line(who(c.it))], 300, cw) + initials(c.fs, 80 + cw + 72, fy0 + cw * 1.5 - 40, 600);
    } });
    const t2 = social.twin;
    cards.push({ id: "twin", name: _("Taste twin"), kind: "social", interest: need ? { ok: false, reason: need } : ok(!!t2, _("nobody you follow shares two titles with you")), bg: () => TINT.coral, body: () => {
      const t = cardTop(t2.name, _("@{handle} shares {n} of your titles this year, more than anyone you follow", { handle: t2.handle, n: t2.shared.length }), { eyebrow: _("Taste twin") });
      return t.svg + coverRow(t2.shared.map(u => mine.get(u)), t.y + 60 + Math.round(DY * 0.35), 4, 400).svg;
    } });
    // a member the owner does not follow, from the site's daily matches: only the owner sees it unless they turn it on for an
    // audience, and it does not depend on followed members, so the rule above for the friend cards does not apply
    const kin = D.kindred ? { ...D.kindred, its: (D.kindred.items || []).map(i => mine.get(path(i.u))).filter(Boolean) } : null;
    cards.push({ id: "kindred", name: _("Kindred member"), kind: "social", interest: !kin || !kin.its.length ? { ok: false, reason: _("no one you do not follow shares five of your titles this year") } : LEVEL < 2 ? { ok: false, reason: _("only you see it, unless you show it to this audience") } : { ok: true }, bg: () => TINT.blue, body: () => {
      const t = cardTop(kin.name, _("@{handle} is not someone you follow, yet shares {n} of your titles this year", { handle: kin.handle, n: kin.n }), { eyebrow: _("Kindred member") });
      return t.svg + coverRow(kin.its, t.y + 60 + Math.round(DY * 0.35), 4, 400).svg;
    } });
    const f = social.first;
    cards.push({ id: "first", name: _("Who got there first"), kind: "social", interest: need ? { ok: false, reason: need } : ok(f && (f.count + f.after) >= 3, _("too few shared titles with dates")), bg: () => TINT.blue, body: () => {
      const t = cardTop(String(f.count), _("times someone you follow finished a title after you did"), { eyebrow: _("Who got there first"), count: f.count });
      let b = t.svg;
      if (f.it) b += feature(f.it, t.y + 50, [label(_("Most followed")), titleRow(f.it.title, 40), line(_("{n} of them came after you", { n: f.itN }))], 400, 220);
      return b + T(80, 836 + DY, f.after, 72, { weight: 800, ls: -2, cls: "rise", d: 700, count: f.after }) + T(80 + EST(f.after, 72, 800) + 20, 836 + DY, f.leader ? _("times you came after someone,") : _("times you came after someone"), 34, { fill: MUTED, cls: "rise", d: 800 }) + (f.leader ? T(80 + EST(f.after, 72, 800) + 20, 878 + DY, _("most often after {name}", { name: trunc(f.leader.name, 30) }), 34, { fill: MUTED, cls: "rise", d: 850 }) : "");
    } });
    const o = social.obscure;
    cards.push({ id: "obscure", name: _("Off the beaten path"), kind: "social", interest: ok(o != null && o >= 0.2, o == null ? _("no mark counts") : _("only {pct}% of titles have fewer than ten marks", { pct: Math.round(o * 100) })), bg: () => TINT.paper, body: () => {
      const rare = withMarks.filter(i => i.marks < 10).sort((a, b) => (b.rating || 0) - (a.rating || 0)).slice(0, 4);
      const t = cardTop(`${Math.round(o * 100)}%`, _("of your year is titles fewer than ten people on {site} have marked", { site: S.site }), { eyebrow: _("Off the beaten path") });
      const r = coverRow(rare, t.y + 60 + Math.round(DY * 0.35), 4, 400);
      return t.svg + r.svg + rare.map((it, i) => T(80 + i * (r.w + 20), r.bottom + 44, _n("{n} mark", "{n} marks", it.marks), 34, { fill: MUTED, cls: "fade", d: 500 + i * 90 })).join("");
    } });
    const cr = social.crowd, gr = social.grain;
    cards.push({ id: "crowd", name: _("With the crowd, against it"), kind: "social", interest: ok(cr || (gr && gr.below >= 70), _("no rated title with a known distribution")), bg: () => TINT.green, body: () => {
      const grOk = gr && gr.below >= 70, t = cardTop(cr && grOk ? _("Both sides") : cr ? _("With the crowd") : _("Against the grain"), "", { eyebrow: _("How others rated it") });
      let y = t.y + 84, b = t.svg;
      const row = (it, lbl, l1, l2, pct, color, d) => coverSvg(it, 80, y, 170, 255, "pop", d) + T(290, y + 34, lbl.toUpperCase(), 30, { fill: PRIMARY, weight: 700, ls: 3, cls: "fade", d: d + 100 }) + T(290, y + 84, trunc(it.title, CJK(it.title) ? 16 : 28), 40, { weight: 700, cls: "rise", d: d + 150 }) + T(290, y + 132, l1, 34, { fill: MUTED, cls: "rise", d: d + 200 }) + T(290, y + 174, l2, 34, { fill: MUTED, cls: "rise", d: d + 250 }) + G("growx", d + 300, `<rect x="290" y="${y + 204}" width="${pct * 6.9}" height="20" rx="10" fill="${color}"/>`) + `<rect x="290" y="${y + 204}" width="690" height="20" rx="10" fill="none" stroke="${LINE}" stroke-width="2"/>`;
      if (cr) { b += row(cr.it, _("With the crowd"), _("your five stars put you with the {pct}%", { pct: cr.share }), _("who gave it five too"), cr.share, LOGO.green, 200); y += 410; }
      if (grOk) b += row(gr.it, _("Against the grain"), _("you rated it higher than {pct}%", { pct: gr.below }), _("of the {n} people who rated it", { n: gr.it.raters }), gr.below, LOGO.coral, 600);
      return b;
    } });
    const e = social.early;
    cards.push({ id: "early", name: _("Early"), kind: "social", interest: ok(!!e, _("no title where you were among the first ten markers, or the data is not public")), bg: () => TINT.coral, body: () => {
      const t = cardTop(_("#{n}", { n: e.first_rank }), _("to mark it on {site}", { site: S.site }), { eyebrow: _("Early") });
      return t.svg + feature(e, t.y + 56, [titleRow(e.title), line(who(e)), { gap: 20 }, line(_("{n} people have marked it since.", { n: e.marks }), 34, INK)], 300, 420);
    } });
    const it = social.instanceTop, have = it && S.items.find(i => path(i.url) === path(it.url));
    cards.push({ id: "part", name: _("Part of the year"), kind: "social", interest: ok(!!it, _("the instance totals are not ready yet")), bg: () => TINT.blue, body: () => {
      // the number of people is the message; the title sits beside its cover
      const k = it.count, t = cardTop(_n("{n} person", "{n} people", k), _("finished this title, more than any other"), { eyebrow: _("{site} in {year}", { site: S.site, year: Y }), count: k });
      return t.svg + feature(have || { cover: it.cover, category: it.category || "movie", title: it.title }, t.y + 56, [label(_("The most finished")), titleRow((have || it).title), have ? line(who(have)) : null, { gap: 20 }, line(have ? _("You were one of them.") : _("Maybe next year."), 34, INK)], 300, 420);
    } });
    const p = social.percentile;
    cards.push({ id: "pack", name: _("Ahead of the pack"), kind: "social", interest: ok(p != null && p > 50, p != null ? _("below the median this year, so this card stays quiet") : _("the instance percentile is not ready yet")), bg: () => TINT.green, body: () => {
      const t = cardTop(`${p}%`, _("of active members on {site} completed fewer titles than you this year", { site: S.site }), { eyebrow: _("Ahead of the pack") }), by = Math.max(t.y + 170, 640 + DY / 2), px = 80 + p * 9.2;
      return t.svg + G("growx", 400, `<rect x="80" y="${by}" width="${p * 9.2}" height="48" rx="24" fill="${LOGO.green}"/>`) + `<rect x="80" y="${by}" width="920" height="48" rx="24" fill="none" stroke="${LINE}" stroke-width="2"/><line x1="540" y1="${by - 20}" x2="540" y2="${by + 68}" stroke="${INK}" stroke-width="3" stroke-dasharray="6 6"/>` + T(540, by + 112, _("the median member"), 34, { fill: MUTED, anchor: "middle" }) + (social.median != null ? T(540, by + 158, _n("{n} title", "{n} titles", social.median), 34, { fill: INK, weight: 700, anchor: "middle" }) : "") + G("pop", 700, `<circle cx="${px}" cy="${by + 24}" r="18" fill="#fff" stroke="${INK}" stroke-width="5"/>` + T(px, by - 32, _("you"), 34, { weight: 800, anchor: p > 92 ? "end" : "middle" })) + T(80, by + 112, _("fewer titles"), 34, { fill: MUTED }) + T(1000, by + 112, _("more"), 34, { fill: MUTED, anchor: "end" });
    } });
    return cards;
  }
  function extras() {
    const cards = []; const ok = (cond, reason) => cond ? { ok: true } : { ok: false, reason };
    const ly = D.last_year;
    cards.push({ id: "versus", name: _("Versus last year"), kind: "computed", interest: TREND.eligible(TR).ok ? { ok: false, reason: _("Year over year takes its place with five or more years of marks") } : ok(!!ly, _("no previous year on record")), bg: () => TINT.paper, body: () => {
      const tot = Object.values(ly).reduce((a, b) => a + b, 0), diff = n - tot, t = cardTop(_n("{n} title", "{n} titles", Math.abs(diff), { n: `${diff >= 0 ? "+" : ""}${diff}` }), _("{last}: {a}. {year}: {b}.", { last: Y - 1, a: tot, year: Y, b: n }), { eyebrow: _("Versus last year") });
      const pitch = Math.min(96, (840 + DY - t.y - 60) / S.cats.length);
      let rows = "";
      S.cats.forEach((c, i) => { const a = ly[c] || 0, b = S.byCat[c].length, mx = Math.max(a, b, 1), y = t.y + 60 + i * pitch; rows += T(80, y + 32, CAT_LABEL[c], 34, { fill: INK }) + G("growx", 300 + i * 60, `<rect x="330" y="${y}" width="${a / mx * 500}" height="20" rx="10" fill="${LINE}"/>`) + G("growx", 400 + i * 60, `<rect x="330" y="${y + 24}" width="${b / mx * 500}" height="20" rx="10" fill="${CAT_COLOR[c]}"/>`) + T(1000, y + 34, `${a} → ${b}`, 34, { fill: b >= a ? INK : MUTED, weight: 700, anchor: "end", cls: "fade", d: 500 + i * 60 }); });
      return t.svg + rows + legend([{ fill: LINE, label: String(Y - 1) }, { stripes: S.cats.slice(0, 4).map(c => CAT_COLOR[c]), label: _("{year}, in each shelf's colour", { year: Y }) }], 900 + DY);
    } });
    // Finally: titles finished this year that were on the wishlist first, with the longest waits
    // (on the site the wishlist date comes from the shelf log, which has no visibility of its own)
    const fy = iso => _("{date}, {year}", { date: fmtDate(iso), year: iso.slice(0, 4) }), wait = k => k >= 730 ? _("{n} years", { n: Math.floor(k / 365) }) : k >= 365 ? _("over a year") : k >= 60 ? _("{n} months", { n: Math.round(k / 30.4) }) : _n("{n} day", "{n} days", k);
    const wishData = S.items.some(i => i.wishlisted), fromWish = S.items.filter(i => i.wishlisted && i.wishlisted < i.date).map(i => ({ it: i, days: Math.round((new Date(i.date + "T12:00:00") - new Date(i.wishlisted + "T12:00:00")) / 864e5) })).sort((a, b) => b.days - a.days);
    const finOk = fromWish.length >= RULE.WISH || (fromWish.length > 0 && fromWish[0].days >= RULE.WAIT);
    cards.push({ id: "finally", name: _("Finally"), kind: "computed", interest: ok(finOk, !wishData ? _("wishlist history is not in this data") : fromWish.length ? _("only {n} came off the wishlist, and none waited a year", { n: fromWish.length }) : _("nothing came off the wishlist this year")), bg: () => TINT.green, body: () => {
      const f0 = fromWish[0], rest = fromWish.slice(1, 4), k = fromWish.length;
      const t = cardTop(_n("{n} title", "{n} titles", k), _("came off your wishlist this year; the longest wait was {wait}", { wait: wait(f0.days) }), { eyebrow: _("Finally"), count: k });
      const y = t.y + 50, listH = rest.length ? 64 + (rest.length - 1) * 44 : 0, w = featureW(y, Math.min(320, Math.floor((880 + DY - y - listH) / 1.5))), ly = y + w * 1.5 + 64;
      return t.svg + feature(f0.it, y, [label(_("Waited longest")), titleRow(f0.it.title, 40), line(_("saved {date}", { date: fy(f0.it.wishlisted) })), line(_("finished {date}", { date: fy(f0.it.date) }))], 300, w)
        + rest.map((x, j) => T(80, ly + j * 44, wrapW(x.it.title, 34, 400, 660, 1)[0], 34, { cls: "fade", d: 600 + j * 60 }) + T(1000, ly + j * 44, wait(x.days), 34, { fill: MUTED, anchor: "end", cls: "fade", d: 600 + j * 60 })).join("");
    } });
    const w = D.waiting;
    cards.push({ id: "waiting", name: _("Unfinished business"), kind: "computed", interest: finOk ? { ok: false, reason: _("Finally takes its place; show both if you like") } : ok(!!w, _("wishlist history is not public here")), bg: () => TINT.coral, body: () => {
      const t = cardTop(_n("{n} day", "{n} days", w.days), _("on your wishlist, and counting"), { eyebrow: _("Unfinished business"), count: w.days });
      return t.svg + feature(w, t.y + 56, [titleRow(w.title), { gap: 20 }, line(_("Maybe {year}.", { year: Y + 1 }), 34, INK)], 300, 420);
    } });
    const byDay = {}; S.items.forEach(i => (byDay[i.date] = byDay[i.date] || []).push(i)); const day = Object.entries(byDay).filter(([d]) => !BULK.has(d)).sort((a, b) => b[1].length - a[1].length)[0];
    cards.push({ id: "marathonday", name: _("Marathon day"), kind: "computed", interest: ok(day && day[1].length >= 4, _("no day with four or more marks")), bg: () => TINT.green, body: () => {
      const t = cardTop(fmtDate(day[0]), _("{n} titles marked in one day", { n: day[1].length }), { eyebrow: _("Marathon day") }), r = coverRow(day[1], t.y + 60 + Math.round(DY * 0.35), 4, 300);
      return t.svg + r.svg + (day[1].length > 4 ? T(80, r.bottom + 48, _("and {n} more", { n: day[1].length - 4 }), 34, { fill: MUTED, cls: "fade", d: 800 }) : "");
    } });
    // Your tags: the most used tags of the year, counted at the page's visibility, and the titles under the top tag
    const tagMap = new Map(); let tagged = 0;
    S.items.forEach(i => { const ts = i.tags || []; if (ts.length) tagged++; ts.forEach(t => { if (!tagMap.has(t)) tagMap.set(t, []); tagMap.get(t).push(i); }); });
    const tagList = [...tagMap.entries()].map(([t, its]) => ({ t, its })).sort((a, b) => b.its.length - a.its.length || a.t.localeCompare(b.t));
    cards.push({ id: "tags", name: _("Your tags"), kind: "computed", interest: ok(tagged >= RULE.TAGGED, tagged ? _n("only {n} title tagged, under {min}", "only {n} titles tagged, under {min}", tagged, { min: RULE.TAGGED }) : _("no tags on this year's titles")), bg: () => TINT.coral, body: () => {
      const rows = tagList.slice(0, 5), mx = rows[0].its.length;
      const t = cardTop(_n("{n} tag", "{n} tags", tagList.length), _("on {n} titles; you used “{tag}” most", { n: tagged, tag: rows[0].t }), { eyebrow: _("Your tags"), count: tagList.length }), y0 = t.y + 60, pitch = Math.min(120, (880 + DY - y0) / rows.length);
      const grid = rows[0].its.slice(-9), g = 12, gh = Math.min(220, Math.floor((900 + DY - y0 - 14 - 2 * g) / 3)), gw = Math.round(gh / 1.5), gx = 1000 - 3 * gw - 2 * g;
      const cell = j => [gx + (j % 3) * (gw + g), y0 + 14 + Math.floor(j / 3) * (gh + g)];
      return t.svg + rows.map((r, k) => { const y = y0 + k * pitch, bw = 30 + r.its.length / mx * 270; return T(80, y + 36, wrapW(r.t, 36, 700, gx - 120, 1)[0], 36, { weight: 700, cls: "rise", d: 300 + k * 90 }) + G("growx", 350 + k * 90, `<rect x="80" y="${y + 54}" width="${bw}" height="18" rx="9" fill="${k ? MUTED : LOGO.coral}"${k ? ` opacity="0.35"` : ""}/>`) + T(80 + bw + 14, y + 71, r.its.length, 34, { fill: MUTED, weight: 700, cls: "fade", d: 450 + k * 90 }); }).join("")
        + T(gx, y0 - 6, wrapW(_("tagged “{tag}”", { tag: rows[0].t }), 34, 400, 1000 - gx, 1)[0], 34, { fill: MUTED })
        + Array.from({ length: 9 }, (z, j) => { const [x, y] = cell(j); return grid[j] ? coverSvg(grid[j], x, y, gw, gh, "pop", 400 + j * 50, "fill") : `<rect x="${x}" y="${y}" width="${gw}" height="${gh}" rx="${Math.round(gw * 0.06)}" fill="${INK}" opacity="0.06"/>`; }).join("");
    } });
    // the essay: the longest review when there is one, else the longest comment
    const es = S.essay;
    const RV = (D.reviews || []).map(r => ({ ...r, it: S.items.find(i => i.url === r.u) || null }));
    const rv = RV.slice().sort((a, b) => (b.it ? 1 : 0) - (a.it ? 1 : 0) || b.chars - a.chars)[0] || null;
    cards.push({ id: "essay", name: _("The essay"), kind: "computed", interest: ok(rv || (es && es.comment.length >= 140), es ? _("longest comment is {n} characters, under 140, and no review this year", { n: es.comment.length }) : _("no comments or reviews this year")), bg: () => TINT.paper, body: () => {
      const text = rv ? rv.excerpt : es.comment, cjk = CJK(text), t = cardTop(rv ? rv.title : _("In your own words"), "", { eyebrow: _("The essay") });
      const qy = t.y + 70, lines = wrapText(text, cjk ? 22 : 44, Math.floor((730 + DY - qy) / 54) + 1);
      const it = rv ? rv.it : es, meta = rv ? (RV.length > 1 ? (cjk ? _("{n} characters, your longest review of {count}", { n: rv.chars, count: RV.length }) : _("{n} words, your longest review of {count}", { n: rv.words, count: RV.length })) : (cjk ? _("{n} characters, your longest review", { n: rv.chars }) : _("{n} words, your longest review", { n: rv.words }))) : _("{n} characters, your longest comment", { n: es.comment.length });
      return t.svg + lines.map((l, i) => T(80, qy + i * 54, (i === 0 ? "“" : "") + l + (i === lines.length - 1 ? "…”" : ""), 38, { weight: 500, cls: "rise", d: 150 + i * 80 })).join("")
        + (() => { const cy = Math.min(760 + DY, qy + (lines.length - 1) * 54 + 64), tx = it ? 210 : 80; return (it ? coverSvg(it, 80, cy, 100, 150, "pop", 800, "bottom") : "") + T(tx, cy + 60, wrapW(it ? it.title : rv.title, 34, 700, 1000 - tx, 1)[0], 34, { weight: 700, cls: "rise", d: 850 }) + T(tx, cy + 104, meta, 34, { fill: MUTED, cls: "rise", d: 900 }); })();
    } });
    return cards;
  }
  function outro() { return { id: "outro", name: _("Outro"), kind: "computed", interest: { ok: true }, footInk: true, bg: () => TINT.own2, body: () => `${piece(`translate(35 ${1115 + DY}) scale(0.9 -0.9)`, TINT.piece2, PIECE.piece, 60, 60)}${T(80, 520 + DY / 2, _("See you in"), 120, { weight: 800, ls: -3.6, cls: "rise", d: 100 })}${T(80, 660 + DY / 2, Y + 1, 120, { fill: PRIMARY, weight: 800, ls: -3.6, cls: "rise", d: 250 })}${T(80, 750 + DY / 2, D.member.url, 34, { fill: MUTED, cls: "fade", d: 400 })}` }; }

  // ---------- made cards: top N as a collection ----------
  // the deck starts from the member's default post visibility; the server keeps the member's choices
  let deckState = Object.assign({ order: [], hidden: {} }, D.deck || {}), made = deckState.made || [], arranging = false, saveTimer = null, savedShown = JSON.stringify(D.shown || null);
  delete deckState.made;
  // pendingMusic: a track choice the server has not taken yet, so a save sent again after a failure still carries it
  let lastSave = Promise.resolve({}), saveFailed = false, pendingMusic = null;
  // one save at a time, in order: each sends the deck as it is when its turn comes, so an older state never lands after a newer one
  function persist() {
    if (!OWNER) return Promise.resolve({});
    lastSave = lastSave.catch(() => {}).then(() => {
      const body = { aud: LEVEL, order: deckState.order, hidden: deckState.hidden, made }, music = pendingMusic;
      // while a job's result is missing (D.warming) the list lacks its cards, so the saved list stays as it is
      if (!D.warming) { body.shown = (state.shown || []).map(c => c.id); savedShown = JSON.stringify(body.shown); }
      if (music !== null) body.music = music;
      return fetch(URLS.save, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF }, body: JSON.stringify(body) }).then(r => { if (!r.ok) throw new Error(r.status); if (pendingMusic === music) pendingMusic = null; return r.json(); });
    }).then(r => { saveFailed = false; return r; }, e => { saveFailed = true; throw e; });
    return lastSave;
  }
  // the card a link names; a malformed escape names no card, so the show opens on the first
  const hashCard = () => { const h = location.hash.slice(1); try { return decodeURIComponent(h); } catch (e) { return h; } };
  const saveDeck = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => { saveTimer = null; persist().catch(() => toast(_("Could not save the deck; try again"))); }, 300); };
  // the waiting save now, or again the one that failed, then the latest one: a page that goes elsewhere must not drop a change
  function flushSave() { if (saveTimer || saveFailed) { clearTimeout(saveTimer); saveTimer = null; return persist(); } return lastSave; }
  leave = flushSave;
  // to another audience of this page: after the last save has landed, or not at all when it failed
  function goAudience(lv, reopen) { flushSave().then(() => { try { sessionStorage.setItem(REOPEN, reopen); } catch (e) { /* without storage the page just opens on the stage */ } const u = new URL(location.href); u.searchParams.set("as", lv); location.href = u.toString(); }, () => toast(_("Could not save the deck; try again"))); }
  // ---------- made cards: items from highest marks, a collection, or picked by hand ----------
  const site = (D.member.url.match(/^https?:\/\/([^/]+)/) || [])[1] || "neodb.social";
  const myUrls = new Map(S.items.map(i => [i.url, i]));
  const COLS = D.collections || [];
  const refOf = it => ({ url: it.url, title: it.title, cover: it.cover, category: it.category });
  const refRaw = x => ({ url: /^https?:/.test(x.u) ? x.u : base + x.u, title: x.t, cover: x.cov, category: x.c });
  function autoPool(cat) { return (cat === "all" ? S.items : S.byCat[cat] || []).slice().sort((a, b) => (b.rating || 0) - (a.rating || 0) || (b.site_rating || 0) - (a.site_rating || 0)); }
  // the server stores every made card with a source
  function resolveMade(m) {
    const src = m.source;
    if (src.type === "auto") return autoPool(src.cat).slice(0, m.n || 5).map(refOf);
    if (src.type === "collection") { const c = COLS.find(x => x.uuid === src.uuid); return c ? c.items.slice(0, m.n || 5).map(refRaw) : []; }
    if (src.type === "hand") return src.items.slice(0, 10);
    return [];
  }
  // the default title and caption of a made card, from its source
  function madeDefault(m) {
    const src = m.source, n = m.n || 5;
    if (src.type === "hand") { const k = (src.items || []).length; return { title: k ? _("My {n} picks of {year}", { n: k, year: Y }) : _("My top picks of {year}", { year: Y }), caption: _("Chosen by hand") }; }
    if (src.type === "collection") { const c = COLS.find(x => x.uuid === src.uuid); return { title: c ? trunc(c.title, 52) : "", caption: c && c.rel === "liked" ? _("From @{owner}'s collection", { owner: c.owner }) : _("From my collection") }; }
    const cat = src.cat || "all"; return { title: cat === "all" ? _("My top {n} titles of {year}", { n, year: Y }) : _("My top {n} {shelf} of {year}", { n, shelf: CAT_NOUN[cat], year: Y }), caption: _("Highest marks") };
  }
  // ---------- made cards: one title, your words (text first: the member's text is the headline) ----------
  // m = { it: the title, words: { kind, text, title, progress, date, more }, caption: the member's small heading or "" }
  const wordsKind = k => ({ comment: _("My comment"), note: _("My note"), review: _("My review") }[k]);
  // the small heading, cut to one line of the given width
  const eyebrowW = (t, maxW) => T(80, 150, wrapW(String(t).toUpperCase(), 30, 700, maxW, 1, 5)[0], 30, { fill: PRIMARY, weight: 700, ls: 5, cls: "fade", d: 0 });
  // the member's text at the largest size at which all of it fits the box; at the last size it is cut and ends with an ellipsis
  function fitWords(w, maxW, maxH, sizes) {
    const text = String(w.text).replace(/\s+/g, " ").trim() + (w.more ? "…" : ""), cjk = CJK(text);
    for (let k = 0; k < sizes.length; k++) {
      const size = sizes[k], weight = size >= 60 ? 700 : 500, ls = size >= 60 ? -(size * 0.02).toFixed(1) : 0, pitch = Math.round(size * (cjk ? 1.5 : 1.34));
      const room = Math.max(1, Math.floor((maxH - size) / pitch) + 1), all = wrapW(text, size, weight, maxW, 999, ls);
      if (all.length <= room || k === sizes.length - 1) {
        const lines = all.length <= room ? all : wrapW(text, size, weight, maxW, room, ls);
        return { lines, size, weight, ls, pitch, cut: all.length > room, h: size + (lines.length - 1) * pitch };
      }
    }
  }
  // the text between quotation marks; the opening mark hangs in the margin so the lines keep one left edge
  function drawWords(f, x, top, d) {
    const y0 = top + Math.round(f.size * 0.8), n = f.lines.length;
    return T(x - Math.round(f.size * 0.05), y0, "“", f.size, { fill: PRIMARY, weight: f.weight, anchor: "end", cls: "fade", d })
      + f.lines.map((l, i) => T(x, y0 + i * f.pitch, l + (i === n - 1 ? "”" : ""), f.size, { weight: f.weight, ls: f.ls, cls: "rise", d: d + i * 60 })).join("");
  }
  // the title of a review or a note, above its text
  function wordsHead(t, x, top, maxW, d) {
    if (!t) return { svg: "", h: 0 };
    const lines = wrapW(t, 44, 800, maxW, 2, -1.3);
    return { svg: lines.map((l, i) => T(x, top + 36 + i * 52, l, 44, { weight: 800, ls: -1.3, cls: "rise", d: d + i * 60 })).join(""), h: lines.length * 52 + 24 };
  }
  // the member's stars (half stars too), then the progress of a note and the date of the text
  function wordsMeta(m, x, y, d, size) {
    const sr = m.it.rating ? starRow(x, y, m.it.rating, size + 4, d) : { svg: "", w: 0 }, when = [m.words.progress, fmtDate(m.words.date)].filter(Boolean).join(" · ");
    return sr.svg + T(x + (sr.w ? sr.w + 14 : 0), y, when, size, { fill: MUTED, cls: "rise", d });
  }
  // text first: the text fills the card; a small cover, the title, the stars and the date are at the foot
  function wordsBody(m) {
    const it = m.it, w = m.words, top = 206;
    const title = wrapW(it.title, 44, 700, 770, 2), by = title.length === 1 ? who(it) : "";
    const hd = wordsHead(w.title, 80, top, 920, 150), f = fitWords(w, 920, 660 + DY - top - hd.h, [96, 80, 64, 52, 44, 38, 34]);
    const yMeta = 890 + DY, yBy = yMeta - 50, yTitle = (by ? yBy : yMeta) - 54;
    return eyebrowW(m.caption || wordsKind(w.kind), 920) + hd.svg + drawWords(f, 80, top + hd.h, 200)
      + coverSvg(it, 80, 720 + DY, 120, 180, "pop", 600, "bottom")
      + title.map((l, i) => T(230, yTitle - (title.length - 1 - i) * 54, l, 44, { weight: 700, cls: "rise", d: 650 })).join("")
      + (by ? T(230, yBy, wrapW(by, 34, 400, 770, 1)[0], 34, { fill: MUTED, cls: "rise", d: 700 }) : "")
      + wordsMeta(m, 230, yMeta, 750, 34);
  }
  function topNBody(m, its) {
    // the title of a made card names a list: one line at 96 px at most, else 64 px, so the covers keep the room
    const dft = madeDefault(m), N = its.length, t = cardTop(m.title || dft.title, "", { eyebrow: m.caption || dft.caption || _("My pick"), max: 96 });
    let b = t.svg;
    const badge = (x, y, r, d) => G("pop", d, `<circle cx="${x}" cy="${y}" r="28" fill="${INK}"/>` + T(x, y + 11, r, 30, { fill: "#fff", weight: 800, anchor: "middle" }));
    const cell = (it, x, y, w, h, r, d, capW) => coverSvg(it, x, y, w, h, "pop", d, "bottom") + badge(x + 10, y + 10, r, d + 100) + wrapW(it.title, 34, 400, capW || Math.max(w, 120), 2).map((l, j) => T(x, y + h + 40 + j * 40, l, 34, { fill: MUTED, cls: "fade", d: d + 150 })).join("");
    // the covers fill the room between the title and the footer; 96 px under each row for a caption of two lines
    const top0 = t.y + 56, H = 900 + DY - top0, Wd = 920;
    if (N === 3) {
      const h = H - 96, w = Math.min(350, h / 1.5), w2 = Math.round(w * 0.78), h2 = w2 * 1.5;
      [[0, 540 - w / 2, top0, w, w * 1.5], [1, 80, top0 + w * 1.5 - h2, w2, h2], [2, 1000 - w2, top0 + w * 1.5 - h2, w2, h2]].forEach(([i, x, y, cw, ch], k) => { if (its[i]) b += cell(its[i], x, y, cw, ch, i + 1, 300 + k * 150); });
      return b;
    }
    if (N === 5) {
      const h1 = H - 96, w1 = Math.min(380, h1 / 1.5), rx = 80 + w1 + 40, cw = (1000 - rx - 24) / 2, ch = (H - 24 - 192) / 2, w = Math.min(cw, ch / 1.5);
      b += cell(its[0], 80, top0, w1, w1 * 1.5, 1, 300);
      its.slice(1).forEach((it, k) => { const col = k % 2, row = Math.floor(k / 2); b += cell(it, rx + col * (cw + 24), top0 + row * (ch + 24 + 96), w, w * 1.5, k + 2, 450 + k * 120, cw - 12); });
      return b;
    }
    if (N === 9) {
      // three rows of three, each title beside its cover so the covers stay large
      const g = 24, cw = (Wd - 2 * g) / 3, ch = (H - 2 * g) / 3, w = Math.round(ch / 1.5), tx = w + 16;
      its.forEach((it, k) => { const x = 80 + (k % 3) * (cw + g), y = top0 + Math.floor(k / 3) * (ch + g), d = 300 + k * 90; b += coverSvg(it, x, y, w, ch, "pop", d, "bottom") + badge(x + 10, y + 10, k + 1, d + 100) + wrapW(it.title, 34, 600, cw - tx, 3).map((l, j) => T(x + tx, y + 34 + j * 40, l, 34, { weight: 600, cls: "fade", d: d + 150 })).join(""); });
      return b;
    }
    // up to four in one row; more in two rows; each cover at the left of its cell, its caption as wide as the cell
    const cols = N <= 4 ? N : Math.ceil(N / 2), rows = Math.ceil(N / cols), gap = 24;
    const cw = (Wd - gap * (cols - 1)) / cols, ch = (H - gap * (rows - 1)) / rows, cvh = ch - 96;
    its.forEach((it, k) => { const x = 80 + (k % cols) * (cw + gap), y = top0 + Math.floor(k / cols) * (ch + gap); const w = Math.min(cw, cvh / 1.5); b += cell(it, x, y, w, w * 1.5, k + 1, 300 + k * 90, cw - 16); });
    return b;
  }
  const madeCards = () => made.map((m, i) => {
    // one title, your words: the server sent the text only if this audience may see it
    if ((m.source || {}).type === "words") { const w = (D.words || {})[String(m.id)]; return { id: "made-" + m.id, name: w ? w.item.title : _("One title, your words"), kind: "made", interest: w ? { ok: true } : { ok: false, reason: _("the text is gone, or this audience cannot see it") }, bg: () => [TINT.paper, TINT.blue, TINT.green, TINT.coral][i % 4], body: () => wordsBody({ it: w.item, words: w, caption: m.caption }) }; }
    const its = resolveMade(m); return { id: "made-" + m.id, name: m.title || madeDefault(m).title, kind: "made", interest: its.length >= 3 ? { ok: true } : { ok: false, reason: _("fewer than three items can be shown, so the collection may have changed") }, bg: () => [TINT.paper, TINT.blue, TINT.green, TINT.coral][i % 4], body: () => topNBody(m, its) }; });

  // ---------- assemble ----------
  function allCards() {
    const c = computed(), s = socialCards(), x = extras();
    const by = id => c.find(k => k.id === id);
    const tc = TREND.cards(TR, UI);
    const xb = id => x.find(k => k.id === id);
    return ordered([by("intro"), by("covers"), by("shelves"), tc.yoy, by("busiest"), by("biggest"), by("top"), by("quiet"), by("disagree"), by("streak"), by("weeks"), xb("marathonday"), by("era"), tc.taste, by("people"), xb("tags"), ...s, ...madeCards(), xb("versus"), xb("finally"), xb("waiting"), xb("essay"), tc.bests, outro()].filter(Boolean));
  }
  // one visibility for the whole page; a card is only shown or hidden, and a card the rules reject starts hidden with its reason
  const REOPEN = "wr-reopen";
  const PAGEVIS = { public: { label: _("Public"), note: _("Anyone with the link sees the cards; counts use public entries only.") }, followers: { label: _("Followers only"), note: _("Only your followers see the cards; counts include followers-only entries.") }, me: { label: _("Only me"), note: _("Only you see the cards; counts include private entries.") } };
  // the member's order, if any; intro and outro stay put, and a card new since the last arrange goes just before the outro
  const FIXED = new Set(["intro", "outro"]);
  // hidden unless the member says otherwise: a card the rules reject, and in a small year every card outside the compact set
  // "*": the member hid every card of this audience, so a card without an override of its own stays hidden
  const byDefault = c => deckState.hidden["*"] === true || !c.interest.ok || (SMALL && !COMPACT.has(c.id) && c.kind !== "made");
  function ordered(cards) {
    const pos = new Map(deckState.order.map((id, i) => [id, i]));
    if (!pos.size) return cards;
    const mid = cards.filter(c => !FIXED.has(c.id));
    return [...cards.filter(c => c.id === "intro"), ...mid.filter(c => pos.has(c.id)).sort((a, b) => pos.get(a.id) - pos.get(b.id)), ...mid.filter(c => !pos.has(c.id)), ...cards.filter(c => c.id === "outro")];
  }
  function moveCard(id, toId, after) {
    const ids = state.cards.filter(c => !FIXED.has(c.id)).map(c => c.id).filter(x => x !== id), k = ids.indexOf(toId);
    if (k < 0) return;
    ids.splice(after ? k + 1 : k, 0, id); deckState.order = ids; saveDeck(); render();
  }
  const state = {};
  function render() {
    cid = 0;
    const cards = allCards();
    // a visitor sees the list the owner last saw for this audience; without one, the rules and the owner's overrides decide here
    const visible = !OWNER && Array.isArray(D.shown) ? new Set(D.shown) : null;
    cards.forEach(c => { const own = deckState.hidden[c.id]; c.hidden = visible ? !visible.has(c.id) : own != null ? own : byDefault(c); c.why = own || (own == null && deckState.hidden["*"] === true) ? _("hidden by you") : !c.interest.ok ? c.interest.reason : byDefault(c) ? _("not shown in a small year, under {n} titles", { n: RULE.SMALL }) : ""; });
    cards.forEach(c => { if (!c.hidden) { try { c.bgc = c.bg(); c.svg = card(c.bgc, c.body(), c.footInk); } catch (e) { console.warn("card failed", c.id, e); c.hidden = true; c.why = _("could not be drawn from this data"); } } if (c.hidden) c.svg = card(TINT.paper, eyebrow(c.name) + T(80, 330, c.name, 64, { weight: 800, ls: -2 }), c.foot); });
    const shown = cards.filter(c => !c.hidden), hid = cards.filter(c => c.hidden), mid = shown.filter(c => !FIXED.has(c.id));
    const deck = $("deck"), keepId = state.curId;
    const btn = (attr, icon, label) => `<button class="dc-nav" type="button" title="${label}" aria-label="${label}" ${attr}><i class="fa-solid ${icon}"></i></button>`;
    deck.innerHTML = shown.map((c, i) => `<div class="card" data-id="${c.id}" data-i="${i}" data-bg="${c.bgc || TINT.paper}"><div class="face">${c.svg}</div><div class="cf"><span class="n">${i + 1}/${shown.length}</span><span class="btns">${c.id === "kindred" && D.kindred ? `<a class="dc-nav" href="${esc(D.kindred.url)}" target="_blank" rel="noopener" title="${_("View profile")}" aria-label="${_("View profile")}"><i class="fa-solid fa-user"></i></a>` : ""}${btn("data-link", "fa-link", _("Copy link to this card"))}${OWNER ? btn("data-share", "fa-share-from-square", _("Post this card")) : ""}${btn("data-dl", "fa-download", _("Download PNG"))}</span></div></div>`).join("");
    $("dots").innerHTML = shown.map((c, i) => `<button type="button" data-i="${i}" aria-label="${i + 1}/${shown.length} ${esc(c.name)}"><b></b></button>`).join("");
    if (OWNER) {
      const hub = $("hub"); hub.classList.toggle("arranging", arranging);
      const mv = (c, k) => FIXED.has(c.id) ? `<span class="pin"><i class="fa-solid fa-thumbtack"></i> ${c.id === "intro" ? _("always first") : _("always last")}</span>` : `<button type="button" class="outline" data-mv="-1" aria-label="${esc(_("Move {name} earlier", { name: c.name }))}"${k === 0 ? " disabled" : ""}><i class="fa-solid fa-arrow-left"></i></button><button type="button" class="outline" data-mv="1" aria-label="${esc(_("Move {name} later", { name: c.name }))}"${k === mid.length - 1 ? " disabled" : ""}><i class="fa-solid fa-arrow-right"></i></button>`;
      // every card in deck order: a hidden one is dimmed with its reason, and the eye on a tile hides or shows that card; Arrange moves only the shown ones
      const eye = c => { const label = c.hidden ? _("Show this card") : _("Hide this card"); return `<button type="button" class="eye" data-eye="${c.id}" title="${esc(label)}" aria-label="${esc(label)}: ${esc(c.name)}"><i class="fa-solid ${c.hidden ? "fa-eye-slash" : "fa-eye"}"></i></button>`; };
      hub.innerHTML = (arranging ? shown : cards).map(c => arranging
        ? `<div class="ht" data-id="${c.id}"${FIXED.has(c.id) ? "" : ` draggable="true"`}><div class="go">${c.svg}<span>${esc(c.name)}</span></div><div class="mv">${mv(c, mid.indexOf(c))}</div></div>`
        : c.hidden
          ? `<div class="ht off" data-id="${c.id}"><div class="go">${c.svg}<span>${esc(c.name)}</span><small>${esc(c.why)}</small></div>${eye(c)}</div>`
          : `<div class="ht" data-id="${c.id}"><a href="#${c.id}" class="go" data-i="${shown.indexOf(c)}">${c.svg}<span>${esc(c.name)}</span></a>${eye(c)}</div>`).join("")
        + (arranging ? "" : `<button class="make" type="button" id="wr-make"><div class="tile"><i class="fa-solid fa-plus"></i></div><span>${_("Make a card")}</span></button>`);
      $("hubsub").textContent = arranging ? _("Drag a card to a new place, or use its arrows. The intro stays first and the outro last.") : _("Hide or show a card with its eye, change the order with Arrange, or make your own with the plus.");
      // the switch at the top says who sees the page; the count belongs to the grid below it
      $("visnote").textContent = PAGEVIS[AUD].note;
      $("decksub").textContent = _("{shown} cards shown, {hidden} hidden.", { shown: shown.length, hidden: hid.length });
      const ar = $("arrange");
      ar.innerHTML = arranging ? `<i class="fa-solid fa-check"></i> ${_("Done")}` : `<i class="fa-solid fa-arrows-up-down-left-right"></i> ${_("Arrange")}`;
      ar.classList.toggle("primary", arranging); ar.classList.toggle("outline", !arranging);
      $("resetorder").hidden = !deckState.order.length;
      document.querySelectorAll(".pagevis button").forEach(b => b.classList.toggle("on", b.dataset.pv === AUD));
      $("hideall").disabled = deckState.hidden["*"] === true && Object.keys(deckState.hidden).length === 1;
      $("suggest").disabled = !Object.keys(deckState.hidden).length;
      // a tile in the Edit sheet closes the sheet and shows that card on the stage
      [...hub.querySelectorAll("a.go")].forEach(a => a.onclick = e => { e.preventDefault(); $("editdlg").close(); window.scrollTo({ top: 0, behavior: "instant" }); go(+a.dataset.i, "instant"); });
      const mk = $("make"); if (mk) mk.onclick = openMake;
      if (arranging) wireArrange(hub, mid.map(c => c.id));
    }
    // wire
    const setHidden = (id, h) => { const c = cards.find(x => x.id === id), rule = c && byDefault(c); if (h === rule) delete deckState.hidden[id]; else deckState.hidden[id] = h; saveDeck(); render(); };
    [...deck.children].forEach(el => { const c = shown[+el.dataset.i]; if (OWNER) el.querySelector("[data-share]").onclick = () => openPublish(c.id); el.querySelector("[data-dl]").onclick = () => downloadPng(c); el.querySelector("[data-link]").onclick = () => copyLink(D.page_url + "#" + c.id); });
    [...$("dots").children].forEach(b => b.onclick = () => { start(); go(+b.dataset.i); });
    // the eye keeps the keyboard where it was after the grid is drawn again
    if (OWNER) [...$("hub").querySelectorAll("[data-eye]")].forEach(b => b.onclick = () => { const id = b.dataset.eye, c = cards.find(x => x.id === id); setHidden(id, !c.hidden); const again = $("hub").querySelector(`[data-eye="${id}"]`); if (again) again.focus(); });
    state.cards = cards; state.shown = shown;
    // while a job's result is not cached yet (D.warming), its cards are missing, so this list is not saved for visitors
    if (OWNER && !D.warming && JSON.stringify(shown.map(c => c.id)) !== savedShown) saveDeck();
    // stay on the same card after an edit; on the first render, open the card the link names
    const want = keepId != null ? keepId : hashCard();
    let k = shown.findIndex(c => c.id === want);
    if (k < 0) k = keepId != null ? Math.min(cur, shown.length - 1) : 0;
    cur = -1; arrive(k, "instant");
    // the owner may view an audience that sees no card: say so on the stage
    const none = !shown.length; $("empty").hidden = !none; if (none) $("empty").textContent = _("This audience sees no cards. Show some in Edit.");
    stage.classList.toggle("none", none);
  }
  // arrange: drag a tile, or use its arrows, which also work on touch screens and from the keyboard
  function wireArrange(hub, ids) {
    let drag = null;
    const clear = () => hub.querySelectorAll(".over-before, .over-after").forEach(x => x.classList.remove("over-before", "over-after"));
    const after = (t, e) => { const r = t.getBoundingClientRect(); return e.clientX > r.left + r.width / 2; };
    hub.querySelectorAll("[data-mv]").forEach(b => b.onclick = () => { const id = b.closest(".ht").dataset.id, dir = +b.dataset.mv, to = ids[ids.indexOf(id) + dir]; if (!to) return; moveCard(id, to, dir > 0); const again = hub.querySelector(`.ht[data-id="${id}"] [data-mv="${dir}"]`); if (again && !again.disabled) again.focus(); });
    hub.querySelectorAll(".ht[draggable]").forEach(t => {
      t.ondragstart = e => { drag = t.dataset.id; t.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", drag); };
      t.ondragend = () => { t.classList.remove("dragging"); clear(); drag = null; };
      t.ondragover = e => { if (!drag || drag === t.dataset.id) return; e.preventDefault(); const af = after(t, e); t.classList.toggle("over-after", af); t.classList.toggle("over-before", !af); };
      t.ondragleave = () => t.classList.remove("over-before", "over-after");
      t.ondrop = e => { e.preventDefault(); if (!drag || drag === t.dataset.id) return; const id = drag; drag = null; moveCard(id, t.dataset.id, after(t, e)); };
    });
  }
  if (OWNER) {
    $("edit").onclick = () => $("editdlg").showModal();
    $("arrange").onclick = () => { arranging = !arranging; render(); };
    $("resetorder").onclick = () => { deckState.order = []; render(); saveDeck(); toast(_("Back to the default order")); };
    // the counts on every card depend on the page's visibility, so a change reloads the page from the server
    // the switch picks the audience to view and edit; the page opens again with that audience's cards and the Edit sheet open
    document.querySelectorAll(".pagevis button").forEach(b => b.onclick = () => { if (b.dataset.pv !== AUD) goAudience(levelOf(b.dataset.pv), "wr-editdlg"); });
    // every card of this audience off at once (cards that qualify later stay off too), or back to the suggested cards
    $("hideall").onclick = () => { deckState.hidden = { "*": true }; saveDeck(); render(); toast(_("Every card is hidden from this audience")); };
    $("suggest").onclick = () => { deckState.hidden = {}; saveDeck(); render(); toast(_("Back to the suggested cards")); };
  }
  // ---------- the stage: one card at a time, like stories ----------
  const stage = $("stage"), deckEl = $("deck"), RM = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let cur = 0, auto = false, started = false;
  const list = () => deckEl.children;
  function reveal(el, again) { if (again) { el.classList.remove("in"); void el.offsetWidth; } if (el.classList.contains("in")) return; el.classList.add("in"); el.querySelectorAll("text[data-count]").forEach(t => { const to = +t.dataset.count, pre = t.dataset.pre || "", suf = t.dataset.suf || "", t0 = performance.now(), dur = 900; const step = now => { const k = Math.min(1, (now - t0) / dur), v = Math.round(to * (1 - Math.pow(1 - k, 3))); t.textContent = pre + v.toLocaleString() + suf; if (k < 1) requestAnimationFrame(step); }; if (!RM) requestAnimationFrame(step); }); }
  // the screen takes the card's colour; in dark mode a deep shade of the same hue, and the card itself stays light
  function deep(hex) {
    const v = parseInt(String(hex).slice(1), 16), r = (v >> 16 & 255) / 255, g = (v >> 8 & 255) / 255, b = (v & 255) / 255;
    const mx = Math.max(r, g, b), d = mx - Math.min(r, g, b);
    const h = !d ? 0 : mx === r ? ((g - b) / d + 6) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    // the tints are pale, so the saturation is lifted for the hue to read on a dark screen
    return `hsl(${Math.round(h * 60)} ${Math.round((d ? Math.min(0.42, 0.18 + d * 3) : 0) * 100)}% 13%)`;
  }
  function paint() { const el = list()[cur]; if (!el) return; stage.style.setProperty("--stage-light", el.dataset.bg); stage.style.setProperty("--stage-dark", deep(el.dataset.bg)); }
  // a card with more to read stays up longer: 5 to 10 seconds
  const durOf = i => { const c = state.shown[i]; return Math.max(5000, Math.min(10000, 3500 + ((c && c.svg.match(/<text/g)) || []).length * 180)); };
  function segs() {
    const dots = [...$("dots").children];
    dots.forEach((x, k) => { x.classList.toggle("done", k < cur); x.classList.remove("cur"); x.removeAttribute("aria-current"); });
    const x = dots[cur]; if (!x) return;
    x.style.setProperty("--dur", durOf(cur) + "ms"); void x.offsetWidth; x.classList.add("cur"); x.setAttribute("aria-current", "step");
  }
  // a record card (the biggest year, personal bests) gets one sweep of light after its numbers count up
  function shine(el) {
    const c = state.shown[+el.dataset.i], f = el.querySelector(".face");
    clearTimeout(shine.t); if (RM || !c || !c.record || !f) return;
    f.classList.remove("shine");
    shine.t = setTimeout(() => { if (el.classList.contains("cur")) { void f.offsetWidth; f.classList.add("shine"); } }, 1000);
  }
  function arrive(i, how) {
    const cs = list(); if (!cs.length) return;
    i = Math.max(0, Math.min(cs.length - 1, i));
    if (how !== "none") { const el = cs[i]; deckEl.scrollTo({ left: el.offsetLeft - (deckEl.clientWidth - el.offsetWidth) / 2, behavior: how }); }
    if (i === cur) return;
    cur = i; state.curId = state.shown[i].id;
    [...cs].forEach((el, k) => el.classList.toggle("cur", k === i));
    reveal(cs[i], true); if (cs[i - 1]) reveal(cs[i - 1]); if (cs[i + 1]) reveal(cs[i + 1]);
    shine(cs[i]); paint(); segs();
    const last = i === cs.length - 1; stage.classList.toggle("at-end", last); $("end").hidden = !last;
    if (last && auto) setAuto(false);
    // the address names the card, so a copied link opens on it
    const h = i ? "#" + state.curId : "";
    if (location.hash !== h) history.replaceState(null, "", location.pathname + location.search + h);
  }
  const go = (i, how) => arrive(i, how || (RM ? "instant" : "smooth"));
  function playBtn() { const b = $("play"), on = auto && !stage.classList.contains("paused"); b.innerHTML = `<i class="fa-solid ${on ? "fa-pause" : "fa-play"}"></i>`; b.setAttribute("aria-label", on ? _("Pause") : _("Play")); b.title = on ? _("Pause") : _("Play"); }
  // with reduced motion the cards never move on by themselves
  function setAuto(on) { auto = !!on && !RM; stage.classList.toggle("auto", auto); stage.classList.remove("paused"); playBtn(); segs(); immerse(auto); if (auto) soundWithShow(); }
  // while the show plays, the controls fade and only the card and the progress bar stay; a move, a tap or a key brings them back for a while
  let wakeT = null;
  function immerse(on) {
    stage.classList.toggle("immersive", on); clearTimeout(wakeT); stage.classList.remove("awake");
    if (on && stage.getBoundingClientRect().top < -2) stage.scrollIntoView({ block: "start", behavior: RM ? "auto" : "smooth" });
  }
  const wake = () => { if (!stage.classList.contains("immersive")) return; stage.classList.add("awake"); clearTimeout(wakeT); wakeT = setTimeout(() => stage.classList.remove("awake"), 2500); };
  ["pointermove", "pointerdown", "keydown"].forEach(t => stage.addEventListener(t, wake, { passive: true }));
  // the first tap, swipe or key starts the show
  function start() { if (started) return; started = true; setAuto(true); }
  function togglePlay() { started = true; if (!auto) { if (cur >= list().length - 1) go(0, "instant"); setAuto(true); return; } stage.classList.toggle("paused"); playBtn(); immerse(!stage.classList.contains("paused")); }
  $("dots").addEventListener("animationend", e => { if (e.animationName !== "wr-seg" || !auto || !e.target.closest(".cur")) return; go(cur + 1); });
  $("play").onclick = togglePlay; $("play").hidden = RM;
  $("prev").onclick = () => { start(); go(cur - 1); };
  $("next").onclick = () => { start(); go(cur + 1); };
  // tap the right of the card for the next one, the left third for the one before, or a card at the side to go to it
  let holdT = null, held = false;
  deckEl.addEventListener("pointerdown", e => { if (e.button) return; held = false; clearTimeout(holdT); holdT = setTimeout(() => { held = true; stage.classList.add("held"); }, 250); });
  const release = () => { clearTimeout(holdT); stage.classList.remove("held"); };
  ["pointerup", "pointercancel", "pointerleave"].forEach(t => deckEl.addEventListener(t, release));
  deckEl.addEventListener("click", e => {
    if (held) { held = false; return; }
    const el = e.target.closest(".card");
    if (!el || e.target.closest("button, a, .cf")) return;
    const i = +el.dataset.i; start();
    if (i !== cur) { go(i); return; }
    const r = el.getBoundingClientRect(); go(e.clientX - r.left < r.width * 0.3 ? cur - 1 : cur + 1);
  });
  // a swipe ends on a card: that card is the current one
  const settle = () => { const mid = deckEl.scrollLeft + deckEl.clientWidth / 2; let best = 0, bd = Infinity; [...list()].forEach((el, k) => { const dd = Math.abs(el.offsetLeft + el.offsetWidth / 2 - mid); if (dd < bd) { bd = dd; best = k; } }); if (best !== cur) { start(); arrive(best, "none"); } };
  let settleT = null;
  deckEl.addEventListener("scroll", () => { clearTimeout(settleT); settleT = setTimeout(settle, 150); }, { passive: true });
  // the arrow keys and Space work while the stage is on the screen and nothing else has the keyboard
  document.addEventListener("keydown", e => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || document.querySelector("dialog[open]") || e.target.closest("input, textarea, select, [contenteditable]")) return;
    if (stage.getBoundingClientRect().bottom < innerHeight * 0.5) return;
    if (e.key === "ArrowRight") { e.preventDefault(); start(); go(cur + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); start(); go(cur - 1); }
    else if (e.key === " " && !e.target.closest("button, a")) { e.preventDefault(); togglePlay(); }
  });
  // a new window size keeps the same card in the middle
  let sizeT = null;
  addEventListener("resize", () => { clearTimeout(sizeT); sizeT = setTimeout(() => arrive(cur, "instant"), 100); });
  // the show waits while the stage is scrolled away, the tab is in the background or a dialog is open (see the stylesheet)
  new IntersectionObserver(es => stage.classList.toggle("away", !es[0].isIntersecting), { threshold: 0.35 }).observe(stage);
  document.addEventListener("visibilitychange", () => stage.classList.toggle("bg", document.hidden));
  const toYear = () => $("year").scrollIntoView({ behavior: RM ? "auto" : "smooth" });
  $("cuetext").textContent = _("See the whole year"); $("cue").onclick = toYear;
  // the end of the deck, and the end of the page, offer the next steps
  const replay = () => { window.scrollTo({ top: 0, behavior: "instant" }); go(0, "instant"); started = true; setAuto(true); };
  const nextStep = OWNER ? `<button class="wr-pill primary" type="button" data-act="share"><i class="fa-solid fa-share-from-square"></i> ${_("Share")}</button>` : `<a class="wr-pill primary" href="${esc(D.make_url || "")}"><i class="fa-solid fa-wand-magic-sparkles"></i> ${_("Make your own {year}", { year: Y })}</a>`;
  $("end").innerHTML = `<button class="wr-pill" type="button" data-act="replay"><i class="fa-solid fa-rotate-left"></i> ${_("Replay")}</button><button class="wr-pill" type="button" data-act="year"><i class="fa-solid fa-chevron-down"></i> ${_("See the whole year")}</button>${nextStep}`;
  $("cta").innerHTML = `<div class="wr-actions"><button class="wr-pill" type="button" data-act="replay"><i class="fa-solid fa-rotate-left"></i> ${_("Replay")}</button>${OWNER ? `<button class="wr-pill" type="button" data-act="edit"><i class="fa-solid fa-pen"></i> ${_("Edit the deck")}</button>` : ""}${nextStep}</div>`;
  document.querySelectorAll("[data-act]").forEach(b => b.onclick = () => ({ replay, year: toYear, share: () => openPublish(), edit: () => $("editdlg").showModal() })[b.dataset.act]());
  playBtn();
  function copyLink(url) { (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(() => toast(_("Link copied")), () => window.prompt("", url)); }

  // ---------- hero and lists ----------
  function hero() {
  const act = ACTIVITY;
  $("stats").innerHTML = [[n, _("titles completed")], [act.days, _("days with something logged")], [MONTHS_LONG[TM.busiest], _n("busiest month, {n} title", "busiest month, {n} titles", TM.counts[TM.busiest])], [S.avg ? S.avg.toFixed(1) : _n("{n} day", "{n} days", act.streak), S.avg ? _("average rating, of 10") : _("longest streak")]].map(([a, b]) => `<div class="dc-surface"><span class="num"${typeof a === "number" ? ` data-to="${a}"` : ""}>${typeof a === "number" ? a.toLocaleString() : a}</span><span class="lbl">${b}</span></div>`).join("");
  }
  hero();
  // the stat tiles count up once, when they come into view
  new IntersectionObserver((es, ob) => { if (!es[0].isIntersecting) return; ob.disconnect(); if (RM) return; $("stats").querySelectorAll("[data-to]").forEach(el => { const to = +el.dataset.to, t0 = performance.now(); const step = now => { const k = Math.min(1, (now - t0) / 1100); el.textContent = Math.round(to * (1 - Math.pow(1 - k, 3))).toLocaleString(); if (k < 1) requestAnimationFrame(step); }; requestAnimationFrame(step); }); }, { threshold: 0.5 }).observe($("stats"));

  // ---------- the year in covers: a pile for each month, as long as the month was busy ----------
  // the piles make a bar chart: the busiest month fills the track, and each pile draws at most 36 covers spread over its length
  let bymCat = "all", openM = null, lists = [];
  const PILE_MAX = 36, piles = $("piles");
  function monthPanel(m) { const list = lists[m]; return `<div class="pile-open" role="region" aria-label="${esc(MONTHS_LONG[m])}"><div class="month-h"><h3>${esc(MONTHS_LONG[m])}</h3><span class="n">${_n("{n} title", "{n} titles", list.length)}</span></div><div class="bym">${list.map(it => `<div>${cover(it)}<p class="t">${esc(it.title)}</p></div>`).join("")}</div></div>`; }
  function openMonth(m) {
    openM = m; piles.querySelectorAll(".pile-open").forEach(x => x.remove());
    piles.classList.toggle("picked", m != null);
    piles.querySelectorAll(".pile").forEach(b => { const on = +b.dataset.m === m; b.classList.toggle("on", on); b.setAttribute("aria-expanded", on); });
    if (m != null) piles.querySelector(`.pile[data-m="${m}"]`).insertAdjacentHTML("afterend", monthPanel(m));
  }
  function byMonth() {
    lists = S.byMonth.map(all => bymCat === "all" ? all : all.filter(it => it.category === bymCat));
    const max = Math.max(1, ...lists.map(l => l.length));
    piles.style.setProperty("--gaps", max - 1);
    piles.innerHTML = lists.map((list, m) => {
      const c = list.length, pm = max > 1 ? (c - 1) / (max - 1) : 0, k = Math.min(c, PILE_MAX);
      const pick = k < 2 ? list.slice(0, k) : Array.from({ length: k }, (z, j) => list[Math.round(j * (c - 1) / (k - 1))]);
      const imgs = pick.map((it, j) => `<img src="${esc(it.cover)}" alt="" loading="lazy" style="--p:${(k > 1 ? j / (k - 1) * pm : 0).toFixed(4)};--r:${((j * 37) % 7 - 3) * 0.6}deg;background:${CAT_COLOR[it.category] || "#ccc"}">`).join("");
      return `<button type="button" class="pile" data-m="${m}" style="--m:${m};--pm:${pm.toFixed(4)}" aria-expanded="false" aria-label="${esc(_("{month}, {titles}", { month: MONTHS_LONG[m], titles: _n("{n} title", "{n} titles", c) }))}"${c ? "" : " disabled"}><span class="stack">${imgs}${c ? `<span class="cnt">${c}</span>` : ""}</span><span class="mo">${esc(MONTHS[m])}</span></button>`;
    }).join("");
    piles.querySelectorAll(".pile").forEach(b => b.onclick = () => openMonth(openM === +b.dataset.m ? null : +b.dataset.m));
    openMonth(openM != null && lists[openM].length ? openM : null);
    document.querySelectorAll("#wr-bymcat button").forEach(b => { b.classList.toggle("on", b.dataset.cat === bymCat); b.setAttribute("aria-pressed", b.dataset.cat === bymCat); });
  }
  const bymCats = $("bymcat");
  bymCats.innerHTML = `<button type="button" data-cat="all">${_("All")}</button>` + S.cats.map(c => `<button type="button" data-cat="${c}"><i class="fa-solid ${CAT_ICON[c]}"></i> ${CAT_LABEL[c]}</button>`).join("");
  bymCats.querySelectorAll("button").forEach(b => b.onclick = () => { bymCat = b.dataset.cat; byMonth(); });
  byMonth();
  // the piles grow when their top comes up past the lowest 15% of the screen; a tall list on a phone does not wait for 20% of itself
  new IntersectionObserver((es, ob) => { if (es[0].isIntersecting) { piles.classList.add("in"); ob.disconnect(); } }, { rootMargin: "0px 0px -15% 0px" }).observe(piles);

  // ---------- tint from the member's own covers ----------
  async function tintFromCovers() {
    try {
      const imgs = await Promise.all(S.top.slice(0, 8).map(it => new Promise(res => { const im = new Image(); im.onload = () => res(im); im.onerror = () => res(null); im.src = exportHref(it.cover); })));
      const cv = document.createElement("canvas"); cv.width = cv.height = 8; const cx = cv.getContext("2d");
      // one average per cover, then the most saturated of them, so a grey wall of covers does not give a grey tint
      let best = null;
      imgs.filter(Boolean).forEach(im => { cx.drawImage(im, 0, 0, 8, 8); const d = cx.getImageData(0, 0, 8, 8).data; let r = 0, g = 0, b = 0; for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; } r /= 64; g /= 64; b /= 64; const mx = Math.max(r, g, b), mn = Math.min(r, g, b); const sat = mx ? (mx - mn) / mx : 0; if (!best || sat > best.sat) best = { r, g, b, sat }; });
      if (!best) return;
      // the hue of that cover, always light: a dark or dull cover still gives a clear pale tint, and grey covers keep the default
      const r = best.r / 255, g = best.g / 255, b = best.b / 255, mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, l = (mx + mn) / 2;
      const sl = d ? d / (1 - Math.abs(2 * l - 1)) : 0;
      if (sl < 0.12) return;
      const h = (mx === r ? ((g - b) / d + 6) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4) * 60, sat = Math.min(0.55, Math.max(0.3, sl));
      const hsl = (L) => { const a = sat * Math.min(L, 1 - L), f = k0 => { const k = (k0 + h / 30) % 12; return Math.round(255 * (L - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)))).toString(16).padStart(2, "0"); }; return "#" + f(0) + f(8) + f(4); };
      // the piece takes the same hue, darker
      TINT.own = hsl(0.93); TINT.own2 = hsl(0.89); TINT.piece = hsl(0.77); TINT.piece2 = hsl(0.73);
      ["intro", "outro"].forEach(id => {
        const fill = id === "intro" ? TINT.own : TINT.own2, pfill = id === "intro" ? TINT.piece : TINT.piece2;
        document.querySelectorAll(`[data-id="${id}"] rect.bg`).forEach(r => r.setAttribute("fill", fill));
        document.querySelectorAll(`[data-id="${id}"] path.piece`).forEach(r => r.setAttribute("fill", pfill));
        const el = document.querySelector(`.deck [data-id="${id}"]`); if (el) el.dataset.bg = fill;
        const c = state.shown.find(x => x.id === id); if (c) { c.bgc = fill; c.svg = c.svg.replace(/<rect class="bg" width="1080" height="\d+" fill="[^"]*"/, `<rect class="bg" width="${CW}" height="${CH}" fill="${fill}"`).replace(/(<path class="piece" transform="[^"]*" fill=")[^"]*"/, `$1${pfill}"`); }
      });
      paint();
    } catch (e) { /* a tainted canvas just keeps the default tint */ }
  }

  // ---------- make a card ----------
  const mkdlg = $("mkdlg"), pubdlg = $("pubdlg");
  document.querySelectorAll("[data-close]").forEach(b => b.onclick = () => b.closest("dialog").close());
  let mk = null;
  const el = $;
  function openMake() {
    mk = { n: 5, src: "auto:all", picked: [], lookupTimer: null, mode: "list", work: null, text: null };
    ["mk-title", "mk-cap", "mk-q", "mk-wq"].forEach(id => el(id).value = ""); el("mk-res").innerHTML = ""; el("mk-texts").innerHTML = "";
    const seg = el("mk-n"); seg.innerHTML = [3, 4, 5, 6, 7, 8, 9, 10].map(k => `<button type="button" class="${k === 5 ? "on" : ""}" data-n="${k}">${k}</button>`).join("");
    seg.querySelectorAll("button").forEach(b => b.onclick = () => { mk.n = +b.dataset.n; seg.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); mkPreview(); });
    const own = COLS.filter(c => c.rel === "own"), liked = COLS.filter(c => c.rel === "liked");
    const opt = c => `<option value="col:${c.uuid}">${esc(trunc(c.title, 40))}${c.rel === "liked" ? ` · @${esc(c.owner)}` : ""} (${c.total})</option>`;
    el("mk-src").innerHTML = `<optgroup label="${_("Highest marks this year")}"><option value="auto:all">${_("Every shelf")}</option>${S.cats.map(c => `<option value="auto:${c}">${CAT_LABEL[c]}</option>`).join("")}</optgroup>`
      + `<optgroup label="${_("Your collections")}">${own.length ? own.map(opt).join("") : `<option disabled>${_("No collections of your own yet")}</option>`}</optgroup>`
      + `<optgroup label="${_("Collections you liked")}">${liked.length ? liked.map(opt).join("") : `<option disabled>${_("No liked collections yet")}</option>`}</optgroup>`
      + `<optgroup label="${_("Your own list")}"><option value="hand">${_("Pick titles or paste links")}</option></optgroup>`;
    el("mk-src").value = "auto:all"; el("mk-src").onchange = () => setSource(el("mk-src").value);
    setSource("auto:all"); setMode("list"); mkdlg.showModal();
  }
  function setSource(v) {
    mk.src = v; el("mk-src").value = v;
    const hand = v === "hand", col = v.startsWith("col:") ? COLS.find(c => c.uuid === v.slice(4)) : null;
    el("mk-pick").hidden = !hand; el("mk-n").hidden = hand; el("mk-nlabel").hidden = hand; el("mk-nhint").hidden = !hand;
    el("mk-col").hidden = !col; el("mk-colnote").hidden = !col;
    if (col) { el("mk-col").innerHTML = `<span>${col.rel === "own" ? _n("Your collection, {n} item.", "Your collection, {n} items.", col.total) : _n("A collection by @{owner}, {n} item.", "A collection by @{owner}, {n} items.", col.total, { owner: esc(col.owner) })} ${_("The card shows the first ones in the collection's order and follows it when it changes.")}</span> <button type="button" class="outline" id="wr-mk-copy">${_("Pick from it by hand")}</button>`; el("mk-copy").onclick = () => { mk.picked = col.items.slice(0, mk.n).map(x => ({ ...refRaw(x), how: "collection" })); setSource("hand"); }; }
    mkPreview();
  }
  function mkItems() {
    if (mk.src === "hand") return mk.picked.slice(0, 10);
    if (mk.src.startsWith("col:")) { const c = COLS.find(x => x.uuid === mk.src.slice(4)); return c ? c.items.slice(0, mk.n).map(refRaw) : []; }
    return autoPool(mk.src.split(":")[1]).slice(0, mk.n).map(refOf);
  }
  function mkDefaults() {
    const source = mk.src === "hand" ? { type: "hand", items: mk.picked } : mk.src.startsWith("col:") ? { type: "collection", uuid: mk.src.slice(4) } : { type: "auto", cat: mk.src.split(":")[1] };
    return madeDefault({ source, n: mk.n });
  }
  // ---------- the picker of a "your words" card: a title you wrote about, then one of your texts on it ----------
  let wordsMenu = null;
  const VIS_LABEL = v => [_("Public"), _("Followers only"), _("Only me")][v] || "";
  const kindsOf = w => { const c = { comment: 0, note: 0, review: 0 }; w.texts.forEach(t => { c[t.kind]++; }); return [c.comment ? _("comment") : "", c.note ? _n("{n} note", "{n} notes", c.note) : "", c.review ? _("review") : ""].filter(Boolean).join(" · "); };
  function setMode(m) {
    mk.mode = m;
    el("mk-mode").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.mode === m));
    el("mk-list").hidden = m !== "list"; el("mk-words").hidden = m !== "words";
    if (m === "words") { el("mk-colnote").hidden = true; showWorks(); } else setSource(mk.src);
    audLine(); mkPreview();
  }
  async function showWorks() {
    const box = el("mk-works");
    if (!wordsMenu) {
      box.setAttribute("aria-busy", "true");
      try { const r = await fetch(URLS.words, { headers: { Accept: "application/json" } }); if (!r.ok) throw new Error(r.status); wordsMenu = (await r.json()).works || []; }
      catch (e) { box.removeAttribute("aria-busy"); box.innerHTML = `<div class="rmsg">${_("The lookup failed. Try again.")}</div>`; return; }
      box.removeAttribute("aria-busy");
    }
    const q = el("mk-wq").value.trim().toLowerCase(), list = wordsMenu.filter(w => !q || w.title.toLowerCase().includes(q)).slice(0, 60);
    box.innerHTML = list.length ? list.map(w => `<button type="button" data-w="${wordsMenu.indexOf(w)}"${mk.work === w ? ' class="on"' : ""}>${cover(w)}<span>${esc(w.title)}</span><small>${esc(kindsOf(w))}</small></button>`).join("")
      : `<div class="rmsg">${wordsMenu.length ? _("Nothing found") : _("You wrote no comment, note or review in {year}.", { year: Y })}</div>`;
    box.querySelectorAll("[data-w]").forEach(b => b.onclick = () => { mk.work = wordsMenu[+b.dataset.w]; mk.text = mk.work.texts[0]; showWorks(); showTexts(); mkPreview(); });
    showTexts();
  }
  function showTexts() {
    const box = el("mk-texts"), w = mk.work;
    box.innerHTML = !w ? "" : w.texts.map((t, k) => `<label${mk.text === t ? ' class="on"' : ""}><input type="radio" name="wr-mk-text" value="${k}"${mk.text === t ? " checked" : ""}><span class="h"><b>${esc(wordsKind(t.kind))}</b> · ${esc(VIS_LABEL(t.vis))}${t.sensitive ? " · " + esc(_("marked sensitive")) : ""} · ${esc([t.progress, fmtDate(t.date)].filter(Boolean).join(" · "))} · ${esc(_n("{n} character", "{n} characters", t.chars))}</span>${t.title ? `<strong>${esc(t.title)}</strong>` : ""}<span class="x">${esc(t.text)}</span></label>`).join("");
    box.querySelectorAll("input").forEach(r => r.onchange = () => { mk.text = w.texts[+r.value]; showTexts(); mkPreview(); });
    audLine();
  }
  // which audiences get the card: the text's own visibility decides
  function audLine() { const t = mk.text, p = el("mk-aud"); p.hidden = !(mk.mode === "words" && t); if (t) p.textContent = [_("This text is public. Every audience gets this card."), _("This text is for followers only. Your followers and you get this card; the public page leaves it out."), _("This text is only for you. Only you get this card.")][t.vis] || ""; }
  function mkPreview() {
    if (mk.mode === "words") { cid = 10000; const t = mk.text, w = mk.work; el("mk-cap").placeholder = t ? wordsKind(t.kind) : ""; el("mk-preview").innerHTML = t ? card(TINT.paper, wordsBody({ it: w, words: t, caption: el("mk-cap").value.trim() })) : ""; return; }
    cid = 10000; const dft = mkDefaults();
    el("mk-title").placeholder = dft.title; el("mk-cap").placeholder = dft.caption;
    el("mk-nhint").textContent = _("{n} picked. A card takes 3 to 10; drag to reorder.", { n: mk.picked.length });
    el("mk-preview").innerHTML = card(TINT.paper, topNBody({ title: el("mk-title").value.trim() || dft.title, caption: el("mk-cap").value.trim() || dft.caption, source: { type: "none" } }, mkItems()));
    const how = it => it.how === "link" ? _("from a link") : it.how === "collection" ? _("from a collection") : myUrls.has(it.url) ? _("marked {date}", { date: fmtDate(myUrls.get(it.url).date) }) : _("not marked in {year}", { year: Y });
    const pk = el("mk-picked");
    pk.innerHTML = mk.picked.map((it, i) => `<div draggable="true" data-i="${i}"><b>${i + 1}</b>${cover(it)}<span class="t">${esc(it.title)}<small>${how(it)}</small></span><button type="button" data-rm="${i}" title="${_("Remove")}"><i class="fa-solid fa-xmark"></i></button></div>`).join("");
    pk.querySelectorAll("[data-rm]").forEach(b => b.onclick = () => { mk.picked.splice(+b.dataset.rm, 1); mkPreview(); });
    let drag = null; pk.querySelectorAll("[draggable]").forEach(row => { row.ondragstart = () => { drag = +row.dataset.i; }; row.ondragover = e => { e.preventDefault(); row.classList.add("over"); }; row.ondragleave = () => row.classList.remove("over"); row.ondrop = e => { e.preventDefault(); const to = +row.dataset.i; if (drag == null || drag === to) return; const [x] = mk.picked.splice(drag, 1); mk.picked.splice(to, 0, x); mkPreview(); }; });
  }
  function addPicked(it) {
    if (mk.picked.length >= 10) { toast(_("A card takes at most ten titles")); return; }
    if (mk.picked.some(x => x.url === it.url)) { toast(_("Already on the list")); return; }
    mk.picked.push(it); el("mk-q").value = ""; el("mk-res").innerHTML = ""; mkPreview();
  }
  function showResults(groups) {
    const res = el("mk-res"); const flat = [];
    res.innerHTML = groups.filter(g => g.items.length || g.msg).map(g => `<div class="rg">${esc(g.label)}</div>` + (g.msg ? `<div class="rmsg">${g.msg}</div>` : "") + g.items.map(it => { flat.push(it); return `<button type="button" data-k="${flat.length - 1}">${cover(it)}<span>${esc(it.title)}</span><small>${esc(it.sub || "")}</small></button>`; }).join("")).join("");
    res.querySelectorAll("[data-k]").forEach(b => b.onclick = () => addPicked(flat[+b.dataset.k]));
    mk.first = flat[0] || null;
  }
  const isLink = v => /^https?:\/\/\S+\.\S+/i.test(v);
  async function lookupLink(url, attempt) {
    if (el("mk-q").value.trim() !== url) return;
    showResults([{ label: _("From the link"), items: [], msg: attempt ? _("{site} is fetching this page; still checking…", { site: esc(site) }) : _("Looking it up on {site}…", { site: esc(site) }) }]);
    const start = !attempt, body = new URLSearchParams({ url });
    let r; try { r = await fetch(start ? URLS.lookup : `${URLS.lookup}?${body}`, start ? { method: "POST", headers: { Accept: "application/json", "X-CSRFToken": CSRF }, body } : { headers: { Accept: "application/json" } }).then(x => x.json()); } catch (e) { r = { status: "error" }; }
    if (el("mk-q").value.trim() !== url) return;
    if (r.status === "found") { const it = { ...refRaw(r.item), how: "link" }; it.sub = `${CAT_LABEL[it.category] || it.category} · ${myUrls.has(it.url) ? _("marked {date}", { date: fmtDate(myUrls.get(it.url).date) }) : _("not marked in {year}", { year: Y })}`; showResults([{ label: _("From the link"), items: [it] }]); return; }
    if ((r.status === "fetching" || (r.status === "unknown" && !start)) && attempt < 12) { mk.lookupTimer = setTimeout(() => lookupLink(url, attempt + 1), 5000); return; }
    const msg = { fetching: _("Still not ready after a minute. Try the link again later."), unsupported: _("This site is not one this instance can read. Try a link to the title on a supported site, or to its page here."), missing: _("No title at this link on {site}.", { site: esc(site) }), busy: _("Too many lookups just now. Try again in a moment."), invalid: _("That does not look like a full link."), error: _("The lookup failed. Try again.") }[r.status] || _("The lookup failed. Try again.");
    showResults([{ label: _("From the link"), items: [], msg }]);
  }
  if (OWNER) {
  el("mk-q").oninput = e => {
    clearTimeout(mk.lookupTimer); const v = e.target.value.trim();
    if (!v) { el("mk-res").innerHTML = ""; mk.first = null; return; }
    if (isLink(v)) { mk.first = null; el("mk-res").innerHTML = ""; mk.lookupTimer = setTimeout(() => lookupLink(v, 0), 250); return; }
    const q = v.toLowerCase(), have = new Set(mk.picked.map(x => x.url));
    const year = S.items.filter(i => i.title.toLowerCase().includes(q) && !have.has(i.url)).slice(0, 6).map(i => ({ ...refOf(i), sub: `${CAT_LABEL[i.category]} · ${_("marked {date}", { date: fmtDate(i.date) })}` }));
    const seen = new Set(year.map(i => i.url)); const inCols = [];
    COLS.forEach(c => c.items.forEach(x => { const it = refRaw(x); if (inCols.length < 6 && it.title && it.title.toLowerCase().includes(q) && !have.has(it.url) && !seen.has(it.url)) { seen.add(it.url); inCols.push({ ...it, how: "collection", sub: _("in “{title}”", { title: trunc(c.title, 28) }) }); } }));
    showResults([{ label: _("Marked in {year}", { year: Y }), items: year }, { label: _("In your collections"), items: inCols }, ...(year.length || inCols.length ? [] : [{ label: _("Nothing found"), items: [], msg: _("Paste a link to add a title from anywhere.") }])]);
  };
  el("mk-q").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); if (mk.first) addPicked(mk.first); } };
  el("mk-title").oninput = mkPreview; el("mk-cap").oninput = mkPreview;
  el("mk-mode").querySelectorAll("button").forEach(b => b.onclick = () => setMode(b.dataset.mode));
  el("mk-wq").oninput = () => showWorks();
  el("mk-save").onclick = () => {
    if (made.length >= (D.made_max || 20)) { toast(_("A deck can have up to {n} cards of your own", { n: D.made_max || 20 })); return; }
    if (mk.mode === "words") {
      const t = mk.text, w = mk.work; if (!t) { toast(_("Pick a text")); return; }
      const id = Date.now();
      made.push({ id, title: "", caption: el("mk-cap").value.trim(), n: 3, source: { type: "words", kind: t.kind, ref: t.ref } });
      // drawn at once where this audience may see the text (stars only on the owner's own view until the page reloads); the server resolves it from then on
      if (t.vis <= LEVEL) (D.words = D.words || {})[String(id)] = { kind: t.kind, text: t.text, more: t.more, title: t.title, progress: t.progress, date: t.date, item: { url: w.url, title: w.title, cover: w.cover, category: w.category, rating: LEVEL === 2 ? w.rating : null, credits: w.credits } };
      mkdlg.close(); render(); saveDeck(); toast(_("Card added to the deck")); return;
    }
    const its = mkItems(); if (its.length < 3) { toast(_("A card needs at least three titles")); return; }
    const n = mk.src === "hand" ? its.length : mk.n;
    const source = mk.src === "hand" ? { type: "hand", items: its.map(({ url, title, cover, category }) => ({ url, title, cover, category })) } : mk.src.startsWith("col:") ? { type: "collection", uuid: mk.src.slice(4) } : { type: "auto", cat: mk.src.split(":")[1] };
    made.push({ id: Date.now(), title: el("mk-title").value.trim(), caption: el("mk-cap").value.trim(), n, source });
    mkdlg.close(); render(); saveDeck(); toast(_("Card added to the deck"));
  };  }


  // ---------- publish ----------
  function openPublish(preselect) { const tabs = $("pub-tabs"); const show = t => { tabs.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.t === t)); ["post", "page", "dl", "qr"].forEach(k => $("pub-" + k).hidden = k !== t); if (t === "qr") qrTab(); }; tabs.querySelectorAll("button").forEach(b => b.onclick = () => show(b.dataset.t)); show("post"); const grid = $("pub-grid"); grid.innerHTML = state.shown.map(c => `<label><input type="checkbox" value="${c.id}" ${c.id === preselect ? "checked" : ""}>${c.svg}</label>`).join(""); const limit = () => { const on = [...grid.querySelectorAll("input:checked")].length; grid.querySelectorAll("label").forEach(l => l.classList.toggle("full", on >= 4 && !l.querySelector("input").checked)); grid.querySelectorAll("input").forEach(i => i.disabled = on >= 4 && !i.checked); }; grid.querySelectorAll("input").forEach(i => i.onchange = limit); limit(); $("pub-note").value = `${D.page_url}\n#NeoDB #Wrapped${Y}`; $("pub-link").value = D.page_url; $("gif-out").innerHTML = ""; audList(); postMode("images");
    // a post never reaches a wider audience than the cards it carries (the server checks this too)
    document.querySelectorAll('#wr-pub-post input[name="wr-vis"]').forEach(r => { r.disabled = +r.value < LEVEL; r.checked = +r.value === LEVEL; }); pubdlg.showModal(); mp4Gate(); }
  // images: up to four picked cards; video: every card, made here like the MP4 download
  function postMode(m) { $("pub-musicrow").hidden = m !== "video" || !music; document.querySelectorAll('input[name="wr-mode"]').forEach(r => { r.checked = r.value === m; }); $("pub-grid").hidden = m !== "images"; $("pub-imgnote").hidden = m !== "images"; $("pub-vidnote").hidden = m !== "video"; }
  if (OWNER) document.querySelectorAll('input[name="wr-mode"]').forEach(r => { r.onchange = () => postMode(r.value); });
  if (OWNER) $("pub-edit").onclick = () => { pubdlg.close(); $("editdlg").showModal(); };
  // what each audience sees at the one link; the audience on screen counts live, another one as the owner last saw it
  function audList() { $("auds").innerHTML = (D.audiences || []).map(a => { const name = ["public", "followers", "me"][a.level], here = a.level === LEVEL, n = here ? state.shown.length : a.shown, off = here ? !state.shown.length : a.off; return `<li${here ? ' class="on"' : ""}><b>${esc(PAGEVIS[name].label)}</b><span class="st">${off ? _("Not shared") : n == null ? _("Suggested cards") : _n("{n} card", "{n} cards", n)}</span><small>${esc(PAGEVIS[name].note)}</small></li>`; }).join(""); }
  if (OWNER) $("publish").onclick = () => openPublish();
  if (OWNER) $("pub-send").onclick = async () => {
    const video = (document.querySelector('input[name="wr-mode"]:checked') || {}).value === "video";
    const ids = video ? [] : [...document.querySelectorAll("#wr-pub-grid input:checked")].map(i => i.value);
    if (!state.shown.length) { toast(_("This audience sees no cards. Show some in Edit.")); return; }
    if (!video && !ids.length) { toast(_("Pick at least one card")); return; }
    const btn = $("pub-send"), prog = $("pub-prog"); btn.disabled = true; btn.setAttribute("aria-busy", "true");
    try {
      const fd = new FormData();
      if (video) {
        prog.hidden = false; prog.value = 0;
        const { r } = await buildFile("mp4", v => { prog.value = v; }, $("pub-music").checked);
        // the intro card is the video's still on the timeline
        const cv = await toCanvas(state.shown[0], 1080), still = await pngBlob(cv);
        fd.append("kind", "video"); fd.append("video", r.blob, `${Y}-wrapped.mp4`); fd.append("poster", still, `${Y}-intro.png`);
      } else {
        for (const id of ids) { const c = state.shown.find(x => x.id === id); fd.append("img", (await toCanvas(c, 1080)).toDataURL("image/png")); }
      }
      fd.append("comment", $("pub-note").value);
      fd.append("visibility", (document.querySelector('#wr-pub-post input[name="wr-vis"]:checked') || {}).value || String(LEVEL));
      fd.append("aud", LEVEL);
      const r = await fetch(URLS.share, { method: "POST", headers: { "X-CSRFToken": CSRF, Accept: "application/json" }, body: fd });
      if (!r.ok) throw new Error(r.status);
      pubdlg.close(); toast(video ? _("Posted the video to your timeline") : _n("Posted {n} card to your timeline", "Posted {n} cards to your timeline", ids.length));
    } catch (e) { toast(_("Posting failed: {error}", { error: e.message })); }
    prog.hidden = true; btn.disabled = false; btn.removeAttribute("aria-busy");
  };
  if (OWNER) $("pub-copy").onclick = () => copyLink($("pub-link").value);
  // ---------- background music: the owner picks a track in Edit; it starts with the show unless the viewer turns the sound off ----------
  // The file comes straight from Wikimedia Commons; if it fails to load, the toggle turns off and the page stays silent.
  // soundOff: the viewer turned the sound off in this view, so the show no longer starts it (not saved)
  let music = D.music || null, audio = null, soundOff = false;
  // the credit is only where the file is and its licence; the Commons page names the composer and the performer
  const credit = t => `<a href="${esc(t.page)}" target="_blank" rel="noopener">Wikimedia</a> · ${esc(t.licence)}`;
  function soundShow(on) {
    const b = $("sound"), np = $("nowplaying");
    b.hidden = !music; b.setAttribute("aria-pressed", on ? "true" : "false");
    b.querySelector("i").className = `fa-solid ${on ? "fa-volume-high" : "fa-volume-xmark"}`;
    $("soundtext").textContent = on ? _("Sound on") : _("Sound off");
    np.hidden = !on; np.innerHTML = on && music ? `<i class="fa-solid fa-music"></i> ${esc(music.title)} · ${credit(music)}` : "";
  }
  function soundFailed() { if (audio) audio.pause(); audio = null; soundShow(false); toast(_("The music could not be loaded")); }
  function sound(on) {
    if (!on || !music) { if (audio) audio.pause(); soundShow(false); return; }
    // the chosen track repeats for as long as the sound is on
    if (!audio || audio.dataset.id !== music.id) { if (audio) audio.pause(); audio = new Audio(music.mp3); audio.dataset.id = music.id; audio.loop = true; audio.onerror = soundFailed; }
    // a browser that refuses sound without a tap (a swipe is not one) leaves the toggle off, with no message
    audio.play().then(() => soundShow(true), e => { if (e && e.name === "NotAllowedError") soundShow(false); else soundFailed(); });
  }
  // the show starts the music, unless the viewer turned it off in this view
  function soundWithShow() { if (music && !soundOff && !(audio && !audio.paused)) sound(true); }
  $("sound").onclick = () => { const on = $("sound").getAttribute("aria-pressed") !== "true"; if (!on) soundOff = true; sound(on); };
  soundShow(false);
  if (OWNER) {
    // the menu in the Edit sheet: no music first, then the tracks; the play button tries the chosen one
    const sel = $("music"), tracks = D.music_menu || [], tryBtn = $("music-try");
    sel.innerHTML = `<option value="">${_("No music")}</option>` + tracks.map(t => `<option value="${esc(t.id)}">${esc(t.title)}</option>`).join("");
    sel.value = music ? music.id : "";
    let preview = null;
    const tryShow = on => { tryBtn.innerHTML = `<i class="fa-solid ${on ? "fa-stop" : "fa-play"}"></i>`; tryBtn.setAttribute("aria-label", on ? _("Pause") : _("Play")); };
    const stopTry = () => { if (preview) preview.pause(); preview = null; tryShow(false); };
    const creditShow = () => { const t = tracks.find(x => x.id === sel.value); $("music-credit").innerHTML = t ? credit(t) : ""; tryBtn.disabled = !t; };
    tryBtn.onclick = () => { if (preview) { stopTry(); return; } const t = tracks.find(x => x.id === sel.value); if (!t) return; if (audio) sound(false); preview = new Audio(t.mp3); preview.loop = true; preview.play().then(() => tryShow(true), () => { stopTry(); toast(_("The music could not be loaded")); }); };
    sel.onchange = () => {
      stopTry(); const playing = audio && !audio.paused;
      music = tracks.find(x => x.id === sel.value) || null;
      pendingMusic = sel.value; persist().catch(() => toast(_("Could not save the deck; try again")));
      creditShow(); if (audio) { audio.pause(); audio = null; } soundShow(false);
      if (playing && music) sound(true);
    };
    $("editdlg").addEventListener("close", stopTry);
    creditShow();
  }

  // ---------- the QR code tab: one 4:5 image with the year in numbers and a code that opens the page ----------
  // Drawn as SVG like the cards. The code style and its limits come from scan tests with three decoders: at most 33 modules,
  // level Q with a picture in the middle (else M, else L), round corners only where both neighbours are light, plain rounded eyes.
  const qrRR = (x, y, s, r) => `M${x + r} ${y}h${s - 2 * r}a${r} ${r} 0 0 1 ${r} ${r}v${s - 2 * r}a${r} ${r} 0 0 1 ${-r} ${r}h${2 * r - s}a${r} ${r} 0 0 1 ${-r} ${-r}v${2 * r - s}a${r} ${r} 0 0 1 ${r} ${-r}z`;
  function qrSvg(text, x, y, size, avatar) {
    const make = lv => { const c = window.qrcode(0, lv); c.addData(text); c.make(); return c; };
    let center = avatar || null, q;
    if (center) { q = make("Q"); if (q.getModuleCount() > 33) q = make("M"); if (q.getModuleCount() > 33) center = null; }
    if (!center) { q = make("M"); if (q.getModuleCount() > 33) q = make("L"); }
    const n = q.getModuleCount(), QZ = 4, k = size / (n + 2 * QZ), mid = n / 2, hr = center ? n * 0.15 : 0;
    const inHole = (r, c) => hr > 0 && Math.hypot(r + 0.5 - mid, c + 0.5 - mid) < hr + 0.35;
    const inEye = (r, c) => (r < 7 && c < 7) || (r < 7 && c >= n - 7) || (r >= n - 7 && c < 7);
    const dark = (r, c) => r >= 0 && c >= 0 && r < n && c < n && q.isDark(r, c) && !inHole(r, c);
    let d = "", eyes = "";
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
      if (!dark(r, c) || inEye(r, c)) continue;
      // a corner is round when the two modules beside it are light
      const up = dark(r - 1, c), dn = dark(r + 1, c), lf = dark(r, c - 1), rt = dark(r, c + 1), tl = !up && !lf, tr = !up && !rt, br = !dn && !rt, bl = !dn && !lf;
      d += `M${c + (tl ? 0.5 : 0)} ${r}H${c + 1 - (tr ? 0.5 : 0)}${tr ? "a.5 .5 0 0 1 .5 .5" : ""}V${r + 1 - (br ? 0.5 : 0)}${br ? "a.5 .5 0 0 1 -.5 .5" : ""}H${c + (bl ? 0.5 : 0)}${bl ? "a.5 .5 0 0 1 -.5 -.5" : ""}V${r + (tl ? 0.5 : 0)}${tl ? "a.5 .5 0 0 1 .5 -.5" : ""}z`;
    }
    [[0, 0], [0, n - 7], [n - 7, 0]].forEach(([r, c]) => { eyes += qrRR(c, r, 7, 0.66) + qrRR(c + 1, r + 1, 5, 0.42) + qrRR(c + 2, r + 2, 3, 0.27); });
    const at = `transform="translate(${x + QZ * k} ${y + QZ * k}) scale(${k})" fill="${INK}"`;
    let out = `<rect x="${x}" y="${y}" width="${size}" height="${size}" rx="28" fill="#fff" stroke="${LINE}" stroke-width="2"/><path ${at} d="${d}"/><path ${at} fill-rule="evenodd" d="${eyes}"/>`;
    if (center) {
      const cx = x + size / 2, cy = y + size / 2, r = (hr - 0.7) * k, id = "qa" + (iconN++);
      out += `<clipPath id="${id}"><circle cx="${cx}" cy="${cy}" r="${r}"/></clipPath><image href="${esc(center)}" x="${cx - r}" y="${cy - r}" width="${2 * r}" height="${2 * r}" preserveAspectRatio="xMidYMid slice" clip-path="url(#${id})"/><circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#000" stroke-opacity="0.12" stroke-width="1.5"/>`;
    }
    return out;
  }
  // a line no wider than maxW: 2 px smaller at a time down to min, then cut with an ellipsis
  const qrFit = (t, size, weight, maxW, min) => { let z = size; while (z > min && EST(t, z, weight) > maxW) z -= 2; let x = String(t); while (x.length > 2 && EST(x, z, weight) > maxW) x = x.slice(0, -2) + "…"; return { t: x, size: z }; };
  function qrHeadline(t, maxW) {
    const W = (x, z) => EST(x, z, 800) - z * 0.03 * x.length;
    for (let z = 72; z >= 56; z -= 4) if (W(t, z) <= maxW) return { lines: [t], size: z };
    const lines = wrapW(t, 56, 800, maxW, 2, -1.7);
    let z = 56; while (z > 44 && lines.some(l => W(l, z) > maxW)) z -= 4;
    return { lines: lines.map(l => { while (l.length > 2 && W(l, z) > maxW) l = l.slice(0, -2) + "…"; return l; }), size: z };
  }
  function qrCard() {
    const W = CW, H = CH, SPLIT = 780, clip = "qc" + (iconN++);
    // the wall: the top titles, then the other rated ones by rating; no default covers, since the image is shared
    const real = it => it.cover && !/default-cover/.test(it.cover);
    const covers = [...new Set([...S.top, ...S.rated.slice().sort((a, b) => b.rating - a.rating)])].filter(real), nc = covers.length;
    const bigC = nc < 21, cols = bigC ? 5 : 7, rows = bigC ? 2 : 3, w = bigC ? 300 : 196, h = w * 1.5, g = bigC ? 28 : 22, shift = (w + g) / 2;
    let b = `<rect width="${W}" height="${H}" fill="${TINT.paper}"/><clipPath id="${clip}"><rect width="${W}" height="${SPLIT}"/></clipPath><g clip-path="url(#${clip})"><rect width="${W}" height="${SPLIT}" fill="${TINT.own2}"/><g transform="rotate(-8 540 ${SPLIT / 2})">`;
    if (nc) for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) b += coverSvg(covers[(r * cols + c) % nc], -260 + c * (w + g) + (r % 2) * shift, -70 + r * (h + g), w, h, "", 0, "fill");
    b += `</g></g>`;
    const colW = 490, hl = qrHeadline(_("{name}’s year", { name: D.member.name }), colW);
    b += T(80, 872, `${_("Annual summary")} ${Y}`.toUpperCase(), 30, { fill: PRIMARY, weight: 700, ls: 5 });
    let y = hl.lines.length > 1 ? 948 : 958;
    hl.lines.forEach((l, i) => { if (i) y += Math.round(hl.size * 1.14); b += T(78, y, l, hl.size, { weight: 800, ls: -(hl.size * 0.03).toFixed(1) }); });
    const five = S.fiveStar.length, nums = [_n("{n} title", "{n} titles", n), five >= 3 ? _n("{n} five-star", "{n} five-star", five) : _n("{n} shelf", "{n} shelves", S.cats.length)];
    const st = qrFit(nums.join(" · "), 34, 400, colW, 28); b += T(80, y + 58, st.t, st.size, { fill: MUTED });
    b += iconMark(80, 1244, 40, 1);
    const P = 390, px = 1000 - P, py = 842;
    b += qrSvg(D.page_url, px, py, P, AVATAR || null);
    const hd = qrFit(D.member.handle, 30, 600, 640, 24), wide = EST(hd.t, hd.size, 600) > P;
    b += T(wide ? 1000 : px + P / 2, py + P + 46, hd.t, hd.size, { weight: 600, anchor: wide ? "end" : "middle" });
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" font-family="${SVG.font}">${b}</svg>`;
  }
  // the code opens the page for the widest audience that sees something; the image shows that audience's numbers
  let qrBlob = null, qrLib = null;
  const qrName = () => `${Y}-${String(D.member.handle).replace(/^@/, "").split("@")[0]}-qr.png`;
  const loadQr = () => qrLib = qrLib || new Promise((res, rej) => { if (window.qrcode) return res(); const s = document.createElement("script"); s.src = `${CDN}/npm/qrcode-generator@1.4.4/qrcode.js`; s.onload = res; s.onerror = () => rej(new Error("no QR library")); document.head.appendChild(s); });
  async function qrTab() {
    const auds = D.audiences || [], open = [0, 1].find(lv => { const a = auds.find(x => x.level === lv); return a && !(lv === LEVEL ? !state.shown.length : a.off); });
    const warn = $("qr-warn"), btns = ["qr-share", "qr-copy", "qr-dl"].map($), prev = $("qr-prev");
    $("qr-link").innerHTML = _("The code opens {link}.", { link: `<b>${esc(D.page_url)}</b>` });
    btns.forEach(b => { b.disabled = true; });
    // one warning at most, the strongest reason first
    const editBtn = `<button type="button" class="dc-btn outline" data-qr-edit>${_("Change in Edit")}</button>`;
    let locked = false;
    if (open == null) { locked = true; warn.innerHTML = `${esc(_("Only you can see the page now, so the code opens nothing for other people."))} ${editBtn}`; }
    else if (open === 1) warn.innerHTML = `${esc(_("Only your followers can open this page, after they sign in."))} ${editBtn}`;
    else if (D.anon_ok === false) warn.innerHTML = `${esc(_("People who are not signed in cannot open this page, because your profile is hidden from them."))} <a href="${esc(URLS.preferences)}">${esc(_("Change it in Settings"))}</a>`;
    else warn.innerHTML = "";
    warn.hidden = !warn.innerHTML;
    const ed = warn.querySelector("[data-qr-edit]"); if (ed) ed.onclick = () => { pubdlg.close(); $("editdlg").showModal(); };
    prev.classList.toggle("locked", locked);
    // the image needs the cards of the audience the code opens; another view offers to switch to it
    if (!locked && open !== LEVEL) {
      prev.innerHTML = `<button type="button" class="dc-btn primary" data-qr-switch>${esc(_("Show the cards the code opens"))}</button>`;
      prev.querySelector("[data-qr-switch]").onclick = () => goAudience(open, "wr-pubdlg:qr");
      return;
    }
    try { await loadQr(); } catch (e) { toast(_("Export failed: {error}", { error: e.message })); return; }
    const svg = qrCard(); prev.innerHTML = svg + (locked ? '<i class="fa-solid fa-lock"></i>' : "");
    if (locked) return;
    // the PNG is made now and kept, so Copy and Share run inside the click (Safari drops the gesture across a wait)
    try { const cv = await toCanvas({ svg }, CW); qrBlob = await pngBlob(cv); }
    catch (e) { toast(_("Export failed: {error}", { error: e.message })); return; }
    const file = new File([qrBlob], qrName(), { type: "image/png" });
    const canShare = !!(navigator.canShare && navigator.canShare({ files: [file] })), canCopy = !!(navigator.clipboard && window.ClipboardItem);
    $("qr-share").hidden = !canShare; $("qr-copy").hidden = !canCopy;
    const first = canShare ? "qr-share" : canCopy ? "qr-copy" : "qr-dl";
    btns.forEach(b => { b.disabled = false; b.classList.toggle("primary", b.id === "wr-" + first); b.classList.toggle("outline", b.id !== "wr-" + first); });
  }
  if (OWNER) {
    $("qr-share").onclick = () => { if (!qrBlob) return; navigator.share({ files: [new File([qrBlob], qrName(), { type: "image/png" })] }).catch(e => { if (e && e.name !== "AbortError") toast(_("Could not share: {error}", { error: e.message })); }); };
    $("qr-copy").onclick = () => { if (!qrBlob) return; navigator.clipboard.write([new ClipboardItem({ "image/png": qrBlob })]).then(() => toast(_("Image copied")), e => toast(_("Could not copy: {error}", { error: e.message }))); };
    $("qr-dl").onclick = () => { if (qrBlob) setTimeout(URL.revokeObjectURL, 60000, saveBlob(qrBlob, qrName())); };
  }

  // a modal dialog is on top of everything, so a toast goes into the topmost open one
  function toast(t) { const el = document.createElement("div"); el.className = "wr-toast"; el.setAttribute("role", "status"); el.textContent = t; ([...document.querySelectorAll("dialog[open]")].pop() || document.body).appendChild(el); setTimeout(() => el.remove(), 2200); }

  // ---------- png and gif export ----------
  const dataCache = new Map();
  S.items.forEach(i => coverUuid.set(i.cover, i.uuid));
  (D.collections || []).forEach(c => c.items.forEach(x => coverUuid.set(x.cov, uuidOf(x.u))));
  function exportHref(h) {
    try { if (new URL(h, location.href).origin === location.origin) return h; } catch (e) { return h; }
    const u = coverUuid.get(h);
    return u && D.cover_url ? D.cover_url.replace("UUID", u) : h;
  }
  // the SVG with its images inlined, and whether any of them could not be read (that drawing then goes without it)
  async function inlineImages(svg) { let missing = false; const hrefs = [...new Set([...svg.matchAll(/<image href="([^"]+)"/g)].map(m => m[1]))]; for (const h of hrefs) { if (!dataCache.has(h)) { try { const r = await fetch(exportHref(h.replace(/&amp;/g, "&"))); if (!r.ok) throw new Error(r.status); const b = await r.blob(); dataCache.set(h, await new Promise(res => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(b); })); } catch (e) { /* this file goes without the cover; the next export tries again */ } } if (!dataCache.has(h)) missing = true; svg = svg.split(`<image href="${h}"`).join(`<image href="${dataCache.get(h) || ""}"`); } return { svg, missing }; }
  const pngBlob = cv => new Promise((res, rej) => cv.toBlob(b => b ? res(b) : rej(new Error("no PNG")), "image/png"));
  // a card as a canvas `size` wide, in the card's 4:5 shape
  // cv.missing: an image of the card could not be read, so this drawing is not kept for a later export
  async function toCanvas(c, size) { const hgt = Math.round(size * CH / CW), { svg, missing } = await inlineImages(c.svg.replace("<svg ", `<svg width="${size}" height="${hgt}" `)); const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" })); const im = new Image(); await new Promise((res, rej) => { im.onload = res; im.onerror = rej; im.src = url; }); const cv = document.createElement("canvas"); cv.width = size; cv.height = hgt; cv.getContext("2d").drawImage(im, 0, 0, size, hgt); URL.revokeObjectURL(url); cv.missing = missing; return cv; }
  // one card as a PNG; toBlob encodes off the main thread
  let lastUrl = null;
  const saveBlob = (blob, name) => { const url = URL.createObjectURL(blob), a = document.createElement("a"); a.download = name; a.href = url; a.click(); return url; };
  async function downloadPng(c) { try { const cv = await toCanvas(c, 1080); const blob = await pngBlob(cv); setTimeout(URL.revokeObjectURL, 60000, saveBlob(blob, `${Y}-${c.id}.png`)); } catch (e) { toast(_("Export failed: {error}", { error: e.message })); } }
  // every card at once, as a ZIP of PNGs, a GIF or an MP4: the page draws each card into a bitmap once (only the page can draw an SVG),
  // and the export worker (export.js) makes the files, so the page stays responsive and the server does no work
  const LIB = { gif: `${CDN}/npm/gifenc@1.0.3/dist/gifenc.esm.js`, mp4: `${CDN}/npm/mp4-muxer@5.2.1/build/mp4-muxer.mjs` };
  const hashOf = str => { let h = 0x811c9dc5; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193); } return (h >>> 0).toString(36); };
  const keyOf = c => c.id + ":" + hashOf(c.svg);
  let exporter = null, sent = new Set(), probe = null;
  function worker() {
    if (!exporter) { exporter = new Worker(document.getElementById("wr-stage").dataset.export, { type: "module" }); sent = new Set(); }
    return exporter;
  }
  // one request to the worker; progress messages come before the answer
  // one request to the worker: every reply carries the request's id, so two requests at once never take each other's answer
  let askSeq = 0;
  const ask = (msg, onProgress) => new Promise((res, rej) => {
    const w = worker(), id = ++askSeq;
    msg = { ...msg, id };
    const off = () => { w.removeEventListener("message", on); w.removeEventListener("error", fail); };
    const on = e => { const d = e.data; if (d.id !== id) return; if (d.type === "progress") { if (onProgress) onProgress(d.done, d.total); return; } off(); if (d.type === "error") rej(new Error(d.message === "stalled" ? _("the video encoder stopped working") : d.message)); else res(d); };
    const fail = e => { off(); exporter = null; rej(new Error(e.message || "the export worker stopped")); };
    w.addEventListener("message", on); w.addEventListener("error", fail);
    w.postMessage(msg, msg.audio ? [msg.audio.left.buffer, ...(msg.audio.right.buffer !== msg.audio.left.buffer ? [msg.audio.right.buffer] : [])] : []);
  });
  // the MP4 button is there only when this browser can encode H.264
  function mp4Gate() { if (!OWNER) return; $("dl-musicrow").hidden = !music; probe = probe || ask({ type: "probe" }).catch(() => ({ mp4: false })); probe.then(r => { $("dl-mp4").disabled = !r.mp4; $("mp4-note").hidden = !!r.mp4; const v = document.querySelector('input[name="wr-mode"][value="video"]'); v.disabled = !r.mp4; if (!r.mp4 && v.checked) postMode("images"); }); }
  // draw every card the worker does not have yet, then ask it for the file; progress runs from 0 to 1
  // the chosen track for an MP4: fetched from Wikimedia and decoded here at 48 kHz (only the page has Web Audio), then handed to
  // the worker without a copy; null when it fails, so the video is made without sound
  async function trackPcm(t) {
    try {
      const r = await fetch(t.mp3); if (!r.ok) throw new Error(r.status);
      const buf = await new OfflineAudioContext(2, 1, 48000).decodeAudioData(await r.arrayBuffer());
      const left = buf.getChannelData(0), right = buf.numberOfChannels > 1 ? buf.getChannelData(1) : left;
      return { rate: 48000, left: new Float32Array(left), right: new Float32Array(right) };
    } catch (e) { return null; }
  }
  const stay = e => { e.preventDefault(); e.returnValue = ""; };
  // one file at a time, from either tab: the worker keeps only the cards of the export it is making
  let exportQueue = Promise.resolve();
  function buildFile(kind, onProgress, withMusic) {
    const run = exportQueue.then(async () => {
      addEventListener("beforeunload", stay);
      try { return await buildFileNow(kind, onProgress, withMusic); } finally { removeEventListener("beforeunload", stay); }
    });
    exportQueue = run.catch(() => {});
    return run;
  }
  async function buildFileNow(kind, onProgress, withMusic) {
    // the cards as they are now: a cover tint that lands meanwhile redraws the intro for the next export, not this one
    const cards = state.shown.map(c => ({ id: c.id, svg: c.svg, key: keyOf(c) })), total = cards.length * 2;
    if (!cards.length) throw new Error(_("This audience sees no cards. Show some in Edit."));
    worker();
    const keep = new Set(cards.map(c => c.key)); [...sent].forEach(k => { if (!keep.has(k)) sent.delete(k); });
    for (let i = 0; i < cards.length; i++) {
      const c = cards[i], key = c.key;
      if (!sent.has(key)) {
        const cv = await toCanvas(c, 1080), bitmap = await createImageBitmap(cv);
        worker().postMessage({ type: "card", key, bitmap }, [bitmap]);
        if (!cv.missing) sent.add(key);
        // let the page paint between two cards
        await new Promise(r => setTimeout(r, 0));
      }
      onProgress((i + 1) / total);
    }
    const items = cards.map((c, i) => ({ key: c.key, name: `${String(i + 1).padStart(2, "0")}-${c.id}.png` }));
    const pcm = kind === "mp4" && withMusic && music ? await trackPcm(music) : null;
    const msg = { type: "export", kind, items, lib: LIB, audio: pcm };
    let r;
    // after a failure (a card the worker could not encode, say) the next export sends every card again
    try { r = await ask(msg, done => onProgress((cards.length + done) / total)); } catch (e) { sent.clear(); throw e; }
    if (kind === "mp4" && withMusic && music) r.musicAsked = true;
    return { r, items };
  }
  async function exportAll(kind, btn) {
    const prog = $("gif-prog"), out = $("gif-out"), btns = ["dl-zip", "dl-gif", "dl-mp4"].map($);
    btns.forEach(b => { b.disabled = true; }); btn.setAttribute("aria-busy", "true"); prog.hidden = false; prog.value = 0; out.innerHTML = "";
    if (lastUrl) { URL.revokeObjectURL(lastUrl); lastUrl = null; }
    try {
      const { r, items } = await buildFile(kind, v => { prog.value = v; }, kind === "mp4" && $("dl-music").checked);
      const mb = (r.blob.size / 1048576).toFixed(1);
      if (kind === "zip") { setTimeout(URL.revokeObjectURL, 60000, saveBlob(r.blob, `${Y}-cards.zip`)); out.innerHTML = `<p class="dc-fine">${_("{size} MB, {n} files", { size: mb, n: items.length })}</p>`; }
      else if (kind === "gif") { lastUrl = saveBlob(r.blob, `${Y}-wrapped.gif`); out.innerHTML = `<img src="${lastUrl}" alt="${_("GIF preview")}"><p class="dc-fine">${_("{size} MB, {n} frames at 2.5 s", { size: mb, n: r.frames })}</p>`; }
      else {
        lastUrl = saveBlob(r.blob, `${Y}-wrapped.mp4`);
        const line = r.music ? _("{size} MB, {seconds} s video with music", { size: mb, seconds: r.seconds }) : _("{size} MB, {seconds} s video", { size: mb, seconds: r.seconds });
        out.innerHTML = `<video src="${lastUrl}" controls loop playsinline></video><p class="dc-fine">${line}</p>${r.musicAsked && !r.music ? `<p class="dc-fine">${_("The music could not be added, so the video has no sound.")}</p>` : ""}`;
      }
    } catch (e) { toast(_("Export failed: {error}", { error: e.message })); }
    prog.hidden = true; btns.forEach(b => { b.disabled = false; }); btn.removeAttribute("aria-busy"); mp4Gate();
  }
  if (OWNER) ["zip", "gif", "mp4"].forEach(kind => { $("dl-" + kind).onclick = e => exportAll(kind, e.currentTarget); });

  render(); tintFromCovers();
  // after a change of visibility, open the Edit sheet or the Share dialog's page tab again
  if (OWNER) {
    let again = "";
    try { again = sessionStorage.getItem(REOPEN) || ""; sessionStorage.removeItem(REOPEN); } catch (e) { /* no storage, nothing to reopen */ }
    if (again === "wr-editdlg") $("editdlg").showModal();
    else if (again.startsWith("wr-pubdlg")) { openPublish(); $("pub-tabs").querySelector(`[data-t="${again.split(":")[1] || "page"}"]`).click(); }
  }
})();

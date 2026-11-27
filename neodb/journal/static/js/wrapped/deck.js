(function () {
  // a year with no finished title: say so instead of drawing empty cards
  if (WR.empty) {
    const lede = document.getElementById("wr-lede");
    const past = WR.D.year < new Date().getFullYear();
    if (lede) lede.textContent = past ? WR._("Nothing was finished in {year}.", { year: WR.D.year }) : WR.D.owner_view ? WR._("Nothing finished this year yet. Mark a title as complete and the cards fill in.") : WR._("Nothing finished this year yet.");
    document.querySelectorAll(".wrapped-page > section:not(:first-of-type), .wr-toolbar, .deck-nav, #wr-deck, #wr-dots, #wr-stats").forEach(e => { e.hidden = true; });
    return;
  }
  const { src, D, _, _n, CATS, CAT_LABEL, CAT_NOUN, CAT_ICON, CAT_COLOR, MONTHS, MONTHS_LONG, MONTHS_NARROW, ROLE_LABEL, stats: S, esc, cover, stars, fmtDate, who, trunc, SVG } = WR;
  // the owner gets the editing controls and saves to the server; a visitor sees the cards the owner left shown
  const OWNER = !!D.owner_view, URLS = D.urls || {}, CDN = document.querySelector(".wrapped-page").dataset.cdn || "https://cdn.jsdelivr.net";
  const CSRF = (document.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
  const $ = id => document.getElementById("wr-" + id);
  const n = S.items.length, Y = D.year, base = D.member.url.split("/users/")[0];
  // the bars each card must clear, set from 2025 production data (data-findings-2026-11-03.md and the round-nine pass rates)
  const RULE = { SMALL: 25, STREAK: 7, ACTIVE: 10, BUSY_MIN: 5, FAN: 10, GAP: 3, GAP_MARKS: 20, NEW: 0.6, OLD: 0.2, WISH: 3, WAIT: 365, TAGGED: 10 };
  // a small year keeps to these cards; the others start hidden and can be shown one by one
  const SMALL = n < RULE.SMALL, COMPACT = new Set(["intro", "covers", "top", "outro"]);
  const INK = SVG.ink, MUTED = SVG.muted, PRIMARY = SVG.primary, LINE = SVG.line;
  const LOGO = { green: "#8EAD78", blue: "#4A9FD0", coral: "#E9795D" };
  let TINT = { green: "#eef3ea", blue: "#e8f2f8", coral: "#fbece8", paper: "#fbfcfc", own: "#f1f3f4", own2: "#eef2f5" };
  const first = D.member.name.split(" ")[0];
  const path = u => String(u || "").replace(base, "");
  // sentences joined with a space, except after full-width punctuation as in Chinese
  const join = parts => parts.filter(Boolean).reduce((a, b) => a ? a + (/[。！？；）]$/.test(a) ? "" : " ") + b : b, "");
  // the deck year as one history row, built from the deck's own items so the trend cards agree with the other cards
  function yearFromItems() {
    const z = () => Array(12).fill(0), y = { m: z(), b: z(), c: {}, d: z(), r: z(), f: z(), g: z() }, perDay = {}, days = new Set();
    S.items.forEach(it => { perDay[it.date] = (perDay[it.date] || 0) + 1; });
    S.items.forEach(it => {
      const mo = +it.date.slice(5, 7) - 1;
      if (it.rating) { y.r[mo]++; y.g[mo] += it.rating; if (it.rating === 10) y.f[mo]++; }
      if (perDay[it.date] > TREND.BULK_DAY) { y.b[mo]++; return; }
      y.m[mo]++; (y.c[it.category] = y.c[it.category] || z())[mo]++;
      if (!days.has(it.date)) { days.add(it.date); y.d[mo]++; }
    });
    return y;
  }
  const TR = TREND.model(D.history, Y, yearFromItems(), S.lastMonth), trIntro = TREND.introLine(TR);
  // a day with more than 30 completions is an import, not a day of watching or reading: the time cards leave it out
  const perDay = {}; S.items.forEach(i => { perDay[i.date] = (perDay[i.date] || 0) + 1; });
  const BULK = new Set(Object.keys(perDay).filter(d => perDay[d] > TREND.BULK_DAY));
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
  social.obscureN = withMarks.filter(i => i.marks < 10).length;
  const dItems = S.rated.filter(i => i.dist && i.dist.some(x => x > 0) && (i.marks || 0) >= 20);
  social.crowd = dItems.filter(i => i.rating >= 9).map(i => ({ it: i, share: i.dist[4] })).sort((a, b) => b.share - a.share)[0] || null;
  social.grain = dItems.filter(i => i.rating >= 8).map(i => { const star = Math.round(i.rating / 2); const below = i.dist.slice(0, star - 1).reduce((s, x) => s + x, 0); return { it: i, below, star }; }).sort((a, b) => b.below - a.below)[0] || null;
  social.early = S.items.filter(i => i.first_rank && i.first_rank <= 10 && (i.marks || 0) >= 50).sort((a, b) => b.marks - a.marks)[0] || null;
  social.instanceTop = D.instance_top || null; social.percentile = D.percentile ?? null;

  // ---------- activity: what counts toward the streak ----------
  // progress, complete and dropped shelf changes, notes, episode comments and reviews count; wishlist adds and edits never do.
  // The page counts only entries at least as visible as the page itself, so a public page never reveals private days.
  // Completions on a bulk day are an import, so they do not make that day active.
  const STREAK_KINDS = new Set(["progress", "complete", "dropped", "note", "comment", "review"]);
  const ACT = (D.activity || S.items.map(i => ({ date: i.date, kind: "complete", vis: 0 }))).filter(a => STREAK_KINDS.has(a.kind) && String(a.date).startsWith(String(Y)) && !(a.kind === "complete" && BULK.has(a.date)));
  const levelOf = v => v === "followers" ? 1 : v === "me" ? 2 : 0;
  const pad2 = x => String(x).padStart(2, "0");
  const isoDay = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const addDays = (d, k) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + k, 12);
  const actCache = {};
  function activityAt(level) {
    if (actCache[level]) return actCache[level];
    const byDay = new Map();
    ACT.forEach(a => { if ((a.vis || 0) > level) return; const e = byDay.get(a.date) || { finish: 0, other: 0 }; if (a.kind === "complete") e.finish++; else e.other++; byDay.set(a.date, e); });
    const days = [...byDay.keys()].sort(); let run = 0, best = 0, bestEnd = null, prev = null;
    days.forEach(ds => { const d = new Date(ds + "T12:00:00"); run = prev && isoDay(addDays(prev, 1)) === ds ? run + 1 : 1; if (run > best) { best = run; bestEnd = d; } prev = d; });
    const start = bestEnd ? addDays(bestEnd, 1 - best) : null; let fin = 0, oth = 0;
    for (let k = 0; k < best; k++) { const e = byDay.get(isoDay(addDays(start, k))); if (e.finish) fin++; else oth++; }
    return (actCache[level] = { byDay, days: days.length, streak: best, start, end: bestEnd, finishDays: fin, otherDays: oth, hasOther: ACT.some(a => a.kind !== "complete" && (a.vis || 0) <= level) });
  }

  // ---------- svg helpers ----------
  const T = (x, y, t, size, o) => { o = o || {}; return `<text x="${x}" y="${y}" font-size="${size}" fill="${o.fill || INK}"${o.weight ? ` font-weight="${o.weight}"` : ""}${o.anchor ? ` text-anchor="${o.anchor}"` : ""}${o.ls ? ` letter-spacing="${o.ls}"` : ""}${o.cls ? ` class="${o.cls}"` : ""}${o.d != null ? ` style="--d:${o.d}ms"` : ""}${o.count != null ? ` data-count="${o.count}" data-pre="${esc(o.pre || "")}" data-suf="${esc(o.suf || "")}"` : ""}>${esc(t)}</text>`; };
  const G = (cls, d, inner, extra) => `<g class="${cls}" style="--d:${d}ms${extra || ""}">${inner}</g>`;
  const eyebrow = t => T(80, 150, t.toUpperCase(), 30, { fill: PRIMARY, weight: 700, ls: 5, cls: "fade", d: 0 });
  // rough text width, so a headline can pick the largest size that fits; Chinese and Japanese characters are one em
  const CJK = s => /[　-鿿]/.test(s || "");
  const MC = (() => { try { return document.createElement("canvas").getContext("2d"); } catch (e) { return null; } })();
  const EST = (s, size, weight) => { s = String(s); if (MC) { MC.font = `${weight || 400} ${size}px ${SVG.font}`; return MC.measureText(s).width; } return [...s].reduce((w, ch) => w + (/[　-鿿＀-￯]/.test(ch) ? 1 : /[A-Z0-9#%+@]/.test(ch) ? 0.64 : 0.54) * size, 0); };
  // lines no wider than maxW; a text that needs two lines is split near its middle, so the second line is not one word
  function wrapW(s, size, weight, maxW, maxLines, ls) {
    s = String(s); const cjk = CJK(s), sep = cjk ? "" : " ", wd = x => EST(x, size, weight) + (ls || 0) * x.length;
    let units = cjk ? s.match(/[\u3000-\u9fff\uff00-\uffef]|[^\s\u3000-\u9fff\uff00-\uffef]+|\s+/g) || [] : s.split(/\s+/);
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
  const sub = (t, y, d) => subAt(t, y, d).svg;
  // headline plus its line: the usual top of a card
  function cardTop(title, line, o) {
    const h = head(title, o), s = line ? subAt(line, h.y + (h.size === 144 ? 66 : 58)) : null;
    return { svg: eyebrow(o && o.eyebrow || "") + h.svg + (s ? s.svg : ""), y: s ? s.y : h.y };
  }
  const big = (t, y, size, d) => T(80, y || 330, t, size || 144, { weight: 800, ls: -((size || 144) * 0.03).toFixed(1), cls: "rise", d: d || 100 });
  // a one-line legend of swatches; an item is { fill } or { stroke } and a label
  function legendRows(items) {
    let x = 80, rows = 1;
    items.filter(Boolean).forEach(it => { const w = 42 + EST(it.label, 34) + 36; if (x + w > 1010 && x > 80) { x = 80; rows++; } x += w; });
    return rows;
  }
  function legend(items, y, d) {
    // a legend that wraps moves up, so its last row stays clear of the footer
    let x = 80, out = ""; y = Math.min(y, 924 - (legendRows(items) - 1) * 50);
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
  function coverSvg(it, x, y, w, h, cls, d, fit) { const id = "cp" + (cid++); const sq = (it.category === "music" || it.category === "podcast") && fit !== "fill"; if (sq && h > w) { if (fit === "bottom") y += h - w; h = w; } return `<g class="${cls || "pop"}" style="--d:${d || 0}ms"><clipPath id="${id}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${Math.round(w * 0.06)}"/></clipPath><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${Math.round(w * 0.06)}" fill="${CAT_COLOR[it.category] || "#ccc"}"/><image href="${esc(src(it.cover))}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid slice" clip-path="url(#${id})"/></g>`; }
  function wrapText(s, maxc, maxLines) { const cjk = /[　-鿿]/.test(s); const units = cjk ? s.split("") : s.split(/\s+/); const lines = []; let line = ""; for (const u of units) { const sep = cjk ? "" : " "; if ((line + sep + u).length > maxc && line) { lines.push(line); line = u; if (lines.length >= maxLines) break; } else line = line ? line + sep + u : u; } if (lines.length < maxLines && line) lines.push(line); return lines; }
  // the footer: the site's small square icon, faded, bottom left; the handle bottom right
  const ICON = { g: "M 22 16 L 74 16 A 6 6 0 0 1 80 22 L 80 38 A 6 6 0 0 1 74 44 L 71.3246 44 A 4.2 4.2 0 0 0 68.4495 51.2617 A 6.5 6.5 0 1 1 59.5505 51.2617 A 4.2 4.2 0 0 0 56.6754 44 L 37.3246 44 A 0.2 0.2 0 0 1 37.1877 43.6542 A 10.5 10.5 0 1 0 22.8123 43.6542 A 0.2 0.2 0 0 1 22.6754 44 L 22 44 A 6 6 0 0 1 16 38 L 16 22 A 6 6 0 0 1 22 16 Z", b: "M 22 48 L 22.6754 48 A 4.2 4.2 0 0 0 25.5505 40.7383 A 6.5 6.5 0 1 1 34.4495 40.7383 A 4.2 4.2 0 0 0 37.3246 48 L 38 48 A 6 6 0 0 1 44 54 L 44 56.6754 A 0.2 0.2 0 0 1 43.6542 56.8123 A 10.5 10.5 0 1 0 43.6542 71.1877 A 0.2 0.2 0 0 1 44 71.3246 L 44 74 A 6 6 0 0 1 38 80 L 22 80 A 6 6 0 0 1 16 74 L 16 54 A 6 6 0 0 1 22 48 Z", c: "M 54 48 L 56.6754 48 A 0.2 0.2 0 0 1 56.8123 48.3458 A 10.5 10.5 0 1 0 71.1877 48.3458 A 0.2 0.2 0 0 1 71.3246 48 L 74 48 A 6 6 0 0 1 80 54 L 80 74 A 6 6 0 0 1 74 80 L 54 80 A 6 6 0 0 1 48 74 L 48 71.3246 A 4.2 4.2 0 0 0 40.7383 68.4495 A 6.5 6.5 0 1 1 40.7383 59.5505 A 4.2 4.2 0 0 0 48 56.6754 L 48 54 A 6 6 0 0 1 54 48 Z", a: "M57 30.5V22 M57 30.5L65.085 27.873 M57 30.5L61.996 37.377 M57 30.5L52.004 37.377 M57 30.5L48.915 27.873" };
  let iconN = 0;
  function iconMark(x, y, size, opacity) { const id = "ik" + (iconN++); const k = size / 64; return `<g transform="translate(${x - 16 * k},${y - 16 * k}) scale(${k})" opacity="${opacity}"><defs><mask id="${id}" maskUnits="userSpaceOnUse" x="0" y="0" width="96" height="96"><rect width="96" height="96" fill="#fff"/><path d="${ICON.a}" fill="none" stroke="#000" stroke-width="4.2" stroke-linecap="round" stroke-linejoin="round"/></mask></defs><g mask="url(#${id})"><path fill="#8EAD78" d="${ICON.g}"/><path fill="#4A9FD0" d="${ICON.b}"/><path fill="#E9795D" d="${ICON.c}"/></g></g>`; }
  function card(bg, body, foot) { return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080" font-family="${SVG.font}"><rect class="bg" width="1080" height="1080" fill="${bg}"/>${body}${iconMark(80, 968, 40, 0.45)}<text x="1000" y="1006" text-anchor="end" font-size="28" fill="${MUTED}">${esc(foot || D.member.handle)}</text></svg>`; }
  const UI = { T, G, eyebrow, big, sub, wrapText, head, cardTop, legend, legendRows, hatchRect, wrapW, EST, CAT_COLOR, CAT_LABEL, CAT_NOUN, CATS, INK, MUTED, LINE, get TINT() { return TINT; } };
  const blobs = (a, b, c) => `<g class="drift"><circle cx="900" cy="180" r="150" fill="${a}" opacity="0.55"/></g><g class="drift2"><circle cx="1010" cy="320" r="100" fill="${b}" opacity="0.55"/></g><g class="drift"><circle cx="960" cy="450" r="70" fill="${c}" opacity="0.55"/></g>`;

  // ---------- the cards ----------
  // the cover-and-column layout of the single-title cards: a 2:3 cover up to `w` wide, a column of lines to its right
  const label = t => ({ t: t.toUpperCase(), size: 30, fill: PRIMARY, weight: 700, ls: 3, lines: 1, after: 6 });
  const titleRow = (t, size) => ({ t, size: size || 44, weight: 700, lines: 2 });
  const line = (t, size, fill) => ({ t, size: size || 34, fill: fill || MUTED, lines: 2 });
  const featureW = (y, w) => Math.min(w || 360, Math.floor((890 - y) / 1.5));
  function feature(it, y, rows, d, w) {
    w = featureW(y, w);
    const x = 80 + w + 40, colW = 1000 - x;
    let out = coverSvg(it, 80, y, w, w * 1.5, "pop", d || 300), ty = null, after = 0;
    rows.filter(Boolean).forEach((r, k) => {
      if (r.gap) { after += r.gap; return; }
      const size = r.size || 34;
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
    cards.push({ id: "intro", name: _("Intro"), kind: "computed", interest: { ok: true }, bg: () => TINT.own, body: () => `${blobs(LOGO.blue, LOGO.green, LOGO.coral)}${eyebrow(_("Annual summary"))}${String(Y).split("").map((ch, i) => T(80 + i * 180, 560, ch, 300, { weight: 800, cls: "rise", d: 100 + i * 120 })).join("")}${T(80, 680, _("{name}’s year", { name: D.member.name }), 48, { weight: 700, ls: -1, cls: "rise", d: 600 })}${T(80, 740, introLine, 34, { fill: MUTED, cls: "rise", d: 700 })}${trIntro ? T(80, 790, trIntro[0].toUpperCase() + trIntro.slice(1), 34, { fill: MUTED, cls: "rise", d: 800 }) : ""}` });
    // a small year: every cover on one card, in the order finished, as large as the space allows
    cards.push({ id: "covers", name: _("Your year in covers"), kind: "computed", interest: ok(SMALL, _("only in a small year, under {n} titles", { n: RULE.SMALL })), bg: () => TINT.paper, body: () => {
      const its = S.items, N = its.length, t = cardTop(_n("{n} title", "{n} titles", N), "", { eyebrow: _("Your year in covers"), count: N });
      const g = N > 12 ? 14 : 24, AW = 920, top0 = t.y + 60, AH = 900 - top0; let best = null;
      for (let cols = 1; cols <= N; cols++) { const rows = Math.ceil(N / cols), w = Math.min((AW - g * (cols - 1)) / cols, (AH - g * (rows - 1)) / rows / 1.5); if (!best || w > best.w) best = { cols, rows, w }; }
      const { cols, rows, w } = best, h = w * 1.5, x0 = 80 + (AW - (cols * w + (cols - 1) * g)) / 2, y0 = top0 + (AH - (rows * h + (rows - 1) * g)) / 2;
      return t.svg + its.map((it, k) => coverSvg(it, x0 + (k % cols) * (w + g), y0 + Math.floor(k / cols) * (h + g), w, h, "pop", 250 + k * Math.min(90, 1400 / N), "bottom")).join("");
    } });
    const lvNow = () => levelOf(deckState.vis), droppedN = () => ACT.filter(a => a.kind === "dropped" && (a.vis || 0) <= lvNow()).length;
    cards.push({ id: "shelves", name: _("Shelves"), kind: "computed", interest: { ok: true }, bg: () => TINT.green, body: () => {
      const t = cardTop(_n("{n} title", "{n} titles", n), "", { eyebrow: _("Completed"), count: n }), bmax = Math.max(...S.cats.map(c => S.byCat[c].length)), pitch = S.cats.length > 6 ? 64 : 72;
      let y = t.y + 50, bars = "";
      S.cats.forEach((c, i) => {
        const k = S.byCat[c].length, w = 40 + (k / bmax) * 540, bw = S.byCat[c].filter(it => BULK.has(it.date)).length / k * w;
        bars += G("growx", 200 + i * 70, `<rect x="80" y="${y}" width="${w}" height="46" rx="12" fill="${CAT_COLOR[c]}"/>${bw ? hatchRect(80 + w - bw, y, bw, 46, 12, "none", "rgba(255,255,255,0.6)") : ""}`)
          + T(80 + w + 20, y + 34, k, 34, { weight: 800, cls: "fade", d: 500 + i * 70 }) + T(80 + w + 30 + EST(k, 34, 800), y + 34, CAT_NOUN[c], 34, { fill: MUTED, cls: "fade", d: 500 + i * 70 });
        y += pitch;
      });
      const notes = [bulkN ? { fill: "#b3bfc6", hatch: "rgba(255,255,255,0.6)", label: _("{n} marked in bulk on {date}", { n: bulkN, date: fmtDate(bulkDay) }) } : null, droppedN() ? { fill: "none", stroke: MUTED, label: _("{n} dropped, not counted", { n: droppedN() }) } : null];
      return t.svg + bars + (notes.some(Boolean) ? legend(notes, Math.max(y + 34, 860)) : "");
    } });
    const mean = TM.counts.reduce((a, b) => a + b, 0) / Math.max(1, S.lastMonth + 1);
    cards.push({ id: "busiest", name: _("Busiest month"), kind: "computed", interest: ok(TM.counts[TM.busiest] >= RULE.BUSY_MIN && TM.counts[TM.busiest] >= 1.6 * mean, TM.counts[TM.busiest] < RULE.BUSY_MIN ? _n("the busiest month has {n} title, under {min}", "the busiest month has {n} titles, under {min}", TM.counts[TM.busiest], { min: RULE.BUSY_MIN }) : _("{month} is only {x} times the monthly mean, under 1.6", { month: MONTHS_LONG[TM.busiest], x: Math.floor(TM.counts[TM.busiest] / mean * 100) / 100 })), bg: () => TINT.coral, body: () => {
      const t = cardTop(MONTHS_LONG[TM.busiest], _("{n} titles in one month; {month} was the quietest with {quiet}", { n: TM.counts[TM.busiest], month: MONTHS_LONG[TM.quietest], quiet: TM.counts[TM.quietest] }), { eyebrow: _("Busiest month") });
      const mmax = Math.max(...TM.counts), bottom = bulkNote ? 812 : 858, room = bottom - Math.max(t.y + 80, 560);
      let spark = "";
      TM.counts.forEach((v, m) => { const h = 24 + (v / mmax) * room, x = 80 + m * 78; spark += G("growy", 300 + m * 60, `<rect x="${x}" y="${bottom - h}" width="56" height="${h}" rx="12" fill="${m === TM.busiest ? LOGO.coral : "#e6d9d4"}"/>`) + T(x + 28, bottom + 44, MONTHS_NARROW[m], 34, { fill: m === TM.busiest ? INK : MUTED, weight: m === TM.busiest ? 700 : 400, anchor: "middle" }) + (m === TM.busiest ? T(x + 28, bottom - h - 14, v, 34, { weight: 800, anchor: "middle", cls: "fade", d: 900 }) : ""); });
      return t.svg + spark + (bulkNote ? T(80, 912, bulkNote, 34, { fill: MUTED, cls: "fade", d: 900 }) : "");
    } });
    const tc = S.topCat, share = S.byCat[tc].length / n, bestT = S.bestOf[tc];
    // one sentence per shelf, because the verb differs
    const N_ = x => x, VERB = { book: N_("{n} read this year, {pct}% of everything"), movie: N_("{n} watched this year, {pct}% of everything"), tv: N_("{n} watched this year, {pct}% of everything"), music: N_("{n} listened to this year, {pct}% of everything"), game: N_("{n} played this year, {pct}% of everything"), podcast: N_("{n} followed this year, {pct}% of everything"), performance: N_("{n} attended this year, {pct}% of everything") };
    cards.push({ id: "biggest", name: _("Biggest shelf"), kind: "computed", interest: ok(S.cats.length > 1 && share >= 0.4, S.cats.length > 1 ? _("the biggest shelf holds only {pct}%", { pct: Math.round(share * 100) }) : _("only one shelf this year")), bg: () => TINT.blue, body: () => {
      const t = cardTop(CAT_LABEL[tc], _(VERB[tc], { n: S.byCat[tc].length, pct: Math.round(share * 100) }), { eyebrow: _("Biggest shelf") });
      return t.svg + feature(bestT, Math.max(t.y + 50, 440), [label(_("Highest mark")), titleRow(bestT.title), bestT.rating ? { t: "★".repeat(Math.round(bestT.rating / 2)), size: 40, fill: LOGO.coral } : line(who(bestT))], 500, 320);
    } });
    // five stars: all of them up to ten; past ten, the ten with a comment first, then the less-marked, then the latest
    const FS = S.fiveStar, fsMany = FS.length > RULE.FAN, said = i => i.comment && i.comment.length > 2 ? 1 : 0;
    const top = FS.length >= 3 ? (fsMany ? FS.slice().sort((a, b) => said(b) - said(a) || (a.marks ?? 1e9) - (b.marks ?? 1e9) || b.date.localeCompare(a.date)).slice(0, RULE.FAN).sort((a, b) => a.date.localeCompare(b.date)) : FS.slice(0, RULE.FAN)) : S.top.slice(0, 8);
    cards.push({ id: "top", name: FS.length >= 3 ? _("Five stars") : _("Top shelf"), kind: "computed", interest: ok(S.rated.length >= 3 || top.length >= 5, _("fewer than three rated titles")), bg: () => TINT.paper, body: () => {
      const line1 = FS.length >= 3 ? fsMany ? _("{n} titles got five stars; here are ten, the ones you wrote about first", { n: FS.length }) : _("Everything that got five stars this year") : S.topLabel;
      const t = cardTop(FS.length >= 3 ? _("{n} favourites", { n: FS.length }) : _("The highest marks"), line1, { eyebrow: FS.length >= 3 ? _("Five stars") : _("Top shelf") });
      // four or fewer get large covers; up to eight keep the wide fan; nine or ten use smaller covers, so the outer covers stay on the card
      const N = top.length, wide = N <= 8, W = N <= 4 ? 280 : wide ? 215 : 190, H = W * 1.5, step = N <= 4 ? 150 : wide ? 95 : 76, cx = 540, rot = i => N > 1 ? -14 + 28 * i / (N - 1) : 0, base = 870 - H - (wide ? 49 : 63);
      let fan = ""; top.forEach((it, i) => { const k = i - (N - 1) / 2; const x = cx + k * step - W / 2, y = base + Math.abs(k) * 14; fan += `<g transform="translate(${x + W / 2},${y + H}) rotate(${rot(i)}) translate(${-(x + W / 2)},${-(y + H)})">${coverSvg(it, x, y, W, H, "fanin", 300 + i * 90)}</g>`; });
      return t.svg + fan;
    } });
    const q = S.quiet;
    cards.push({ id: "quiet", name: _("Quiet favourite"), kind: "computed", interest: ok(q && q.marks != null && q.marks < 20, q ? _("your favourites all have 20 or more marks here") : _("no top mark to pick from")), bg: () => TINT.coral, body: () => {
      const t = cardTop(q.title, "", { eyebrow: _("The quiet favourite") });
      return t.svg + feature(q, t.y + 56, [line(who(q)), { t: "★".repeat(Math.round(q.rating / 2)), size: 44, fill: LOGO.coral }, { gap: 20 }, line(q.marks ? _n("from you, and only {n} other mark on {site}.", "from you, and only {n} other marks on {site}.", q.marks, { site: S.site }) : _("from you, and no other mark on {site}.", { site: S.site }), 34, INK)], 300, 360);
    } });
    // stricter than the shared stats: 1.5 stars from the site average, on a title with 20 or more marks
    // (the mock has mark counts, not rating counts; the production check used ratings)
    const gapped = S.rated.filter(i => i.site_rating && (i.marks || 0) >= RULE.GAP_MARKS).map(i => ({ it: i, gap: i.rating - i.site_rating }));
    const dis = gapped.slice().sort((a, b) => a.gap - b.gap)[0] || null, sur = gapped.slice().sort((a, b) => b.gap - a.gap)[0] || null;
    const stars1 = v => Math.round(v / 2 * 10) / 10;
    // both marks on one five-star scale
    function gapScale(x, y, w, you, avg, d) {
      const px = v => x + (v / 5) * w; let g = `<line x1="${x}" y1="${y}" x2="${x + w}" y2="${y}" stroke="${LINE}" stroke-width="6" stroke-linecap="round"/>`;
      for (let k = 0; k <= 5; k++) g += `<line x1="${px(k)}" y1="${y - 8}" x2="${px(k)}" y2="${y + 8}" stroke="${LINE}" stroke-width="3"/>` + T(px(k), y + 48, k, 34, { fill: MUTED, anchor: "middle" });
      g += G("growx", d, `<line x1="${px(Math.min(you, avg))}" y1="${y}" x2="${px(Math.max(you, avg))}" y2="${y}" stroke="${you < avg ? LOGO.coral : LOGO.green}" stroke-width="8" stroke-linecap="round"/>`);
      const side = (v, other) => Math.abs(px(v) - px(other)) < 150 ? (v < other ? "end" : "start") : px(v) > x + w - 60 ? "end" : px(v) < x + 60 ? "start" : "middle";
      g += G("pop", d + 150, `<circle cx="${px(avg)}" cy="${y}" r="14" fill="#fff" stroke="${MUTED}" stroke-width="4"/>`) + T(px(avg), y - 30, _("{site} {value}", { site: S.site, value: avg }), 34, { fill: MUTED, anchor: side(avg, you), cls: "fade", d: d + 200 });
      g += G("pop", d + 250, `<circle cx="${px(you)}" cy="${y}" r="16" fill="${INK}"/>`) + T(px(you), y - 30, _("you {value}", { value: you }), 34, { fill: INK, weight: 700, anchor: side(you, avg), cls: "fade", d: d + 300 });
      return g;
    }
    cards.push({ id: "disagree", name: _("Where you disagreed"), kind: "computed", interest: ok((dis && dis.gap <= -RULE.GAP) || (sur && sur.gap >= RULE.GAP), _("no rating 1.5 stars from the site average on a title with {n} or more marks", { n: RULE.GAP_MARKS })), bg: () => TINT.blue, body: () => {
      const t = cardTop(_("Against the grain"), "", { eyebrow: _("Where you disagreed") });
      const rows = [[dis && dis.gap <= -RULE.GAP ? dis : null, _("The disappointment")], [sur && sur.gap >= RULE.GAP ? sur : null, _("The surprise")]].filter(([g]) => g);
      let b = t.svg;
      if (rows.length === 1) {
        // one title: a large cover, and the scale across the card
        const [[g, lbl]] = rows, it = g.it, y = t.y + 60;
        b += feature(it, y, [label(lbl), titleRow(it.title, 48), line(who(it))], 300, 260);
        b += gapScale(110, y + 390 + 90, 860, it.rating / 2, stars1(it.site_rating), 600);
        return b;
      }
      rows.forEach(([g, lbl], i) => {
        const it = g.it, y = t.y + 50 + i * 300, d = 300 + i * 250;
        b += coverSvg(it, 80, y, 180, 270, "pop", d) + T(300, y + 30, lbl.toUpperCase(), 30, { fill: PRIMARY, weight: 700, ls: 3, cls: "fade", d: d + 50 })
          + wrapText(it.title, CJK(it.title) ? 16 : 28, 2).map((l, k) => T(300, y + 80 + k * 48, l, 40, { weight: 700, cls: "rise", d: d + 100 })).join("")
          + gapScale(320, y + 210, 650, it.rating / 2, stars1(it.site_rating), d + 200);
      });
      return b;
    } });
    // a streak of seven days or more is the headline; a shorter one becomes a line on the active-days card
    const lv = levelOf(deckState.vis), sAt = activityAt(lv), streakMode = sAt.streak >= RULE.STREAK, nd = k => _n("{n} day", "{n} days", k);
    const FIN = LOGO.green, OTH = LOGO.blue, EMPTY = "#dfe5dc";
    function activeDays(a) {
      const step = 27, cell = 21, x0 = 162, span = a.streak >= 2 ? _("with something logged; the longest run, outlined, was {n} days, {start} to {end}", { n: a.streak, start: fmtDate(isoDay(a.start)), end: fmtDate(isoDay(a.end)) }) : _("with something logged; never two days in a row");
      const t = cardTop(nd(a.days), span, { eyebrow: _("Active days"), count: a.days }), y0 = t.y + 44, rowH = Math.min(36, (836 - y0) / (S.lastMonth + 1));
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
      return t.svg + grid + legend([{ fill: FIN, label: _n("{n} day finishing a title", "{n} days finishing a title", fin) }, a.hasOther ? { fill: OTH, label: _n("{n} day of progress and comments", "{n} days of progress and comments", oth) } : null], 900);
    }
    cards.push({ id: "streak", name: streakMode ? _("Streak") : _("Active days"), kind: "computed", interest: streakMode || sAt.days >= RULE.ACTIVE ? { ok: true } : { ok: false, reason: _("{days} with something logged, under {min}, and the longest run is {run}", { days: nd(sAt.days), min: RULE.ACTIVE, run: nd(sAt.streak) }) }, bg: () => TINT.green, body: () => {
      if (!streakMode) return activeDays(sAt);
      const a = sAt, v = { start: fmtDate(isoDay(a.start)), end: fmtDate(isoDay(a.end)) };
      const t = cardTop(nd(a.streak), a.hasOther ? _("something logged every day, {start} to {end}", v) : _("a title finished every day, {start} to {end}", v), { eyebrow: _("Longest streak"), count: a.streak });
      // 44 day squares: the run outlined in the middle, a few days either side; a long run shows its last 44 days
      const cells = 44, len = a.streak, first = len <= cells - 4 ? addDays(a.start, -Math.floor((cells - len) / 2)) : addDays(a.end, 1 - cells);
      const offset = Math.round((a.start - first) / 864e5), gy = t.y + 50;
      let grid = "";
      for (let k = 0; k < cells; k++) {
        const d = addDays(first, k), e = a.byDay.get(isoDay(d)), inRun = d >= a.start && d <= a.end, x = 80 + (k % 11) * 86, y = gy + Math.floor(k / 11) * 80;
        grid += G("pop", inRun ? 350 + Math.max(0, k - offset) * 35 : 250, `<rect x="${x}" y="${y}" width="66" height="66" rx="16" fill="${e ? (e.finish ? FIN : OTH) : EMPTY}"${inRun ? ` stroke="${INK}" stroke-width="4"` : ""}/>`);
      }
      const last = addDays(first, cells - 1);
      return t.svg + grid + T(80, gy + 4 * 80 + 24, _("{start} to {end}", { start: fmtDate(isoDay(first)), end: fmtDate(isoDay(last)) }), 34, { fill: MUTED }) + legend([{ fill: FIN, label: _("a title finished") }, a.hasOther ? { fill: OTH, label: _("progress or a comment") } : null, { fill: EMPTY, label: _("nothing logged") }, { stroke: INK, label: _("the run") }], 900);
    } });
    cards.push({ id: "weeks", name: _("Week by week"), kind: "computed", interest: ok(n >= 30, _("{n} titles is too few for a week strip", { n })), bg: () => TINT.paper, body: () => {
      const s0 = TM.marathonStart, t = cardTop(_("{n} in a week", { n: TM.weeks[TM.marathonWeek] }), _("the busiest seven days began {date}; {n} weeks of the year had a title finished", { date: fmtDate(isoDay(s0)), n: TM.weeks.filter(v => v).length }), { eyebrow: _("Week by week") });
      const wmax = Math.max(...TM.weeks), bottom = 820, room = bottom - (t.y + 90);
      let strip = "";
      TM.weeks.forEach((v, i) => { const h = v ? 16 + v / wmax * room : 6; strip += `<rect x="${80 + i * 17.4}" y="${bottom - h}" width="13" height="${h}" rx="4" fill="${i === TM.marathonWeek ? LOGO.coral : v ? PRIMARY : LINE}"/>`; });
      return t.svg + G("draw", 300, strip) + MONTHS.map((m, i) => i % 2 ? "" : T(80 + i * 4.42 * 17.4, bottom + 44, m, 34, { fill: MUTED })).join("") + legend([{ fill: PRIMARY, label: _("titles finished that week") }, { fill: LOGO.coral, label: _("the busiest week") }], 912);
    } });
    const dl = S.decadeList, span = dl.length ? dl[dl.length - 1].decade - dl[0].decade : 0, domShare = dl.length ? Math.max(...dl.map(d => d.n)) / dl.reduce((s, d) => s + d.n, 0) : 1;
    // new or old: the share of titles released in the deck year or the two years before it
    const dated = S.items.filter(i => i.year && i.year > 1800 && i.year <= Y + 1), recent = dated.length ? dated.filter(i => i.year >= Y - 2).length / dated.length : 0, rpct = Math.round(recent * 100);
    const eraMode = recent >= RULE.NEW ? "new" : recent <= RULE.OLD ? "old" : "mid";
    cards.push({ id: "era", name: _("The era"), kind: "computed", interest: ok(dated.length >= 10 && (eraMode === "new" || (span >= 15 && domShare <= 0.8)), dated.length < 10 ? _("fewer than ten titles with a release year") : span < 15 ? _("release years span under 15 years") : _("{pct}% of titles come from one decade", { pct: Math.round(domShare * 100) })), bg: () => TINT.blue, body: () => {
      const bottom = 812;
      let db = "", t;
      if (eraMode === "new") {
        // the last ten years one by one, everything older in one bar; the last three years in blue
        t = cardTop(_("Mostly new"), _("{pct}% came out in the last three years; the oldest title is from {year}.", { pct: rpct, year: S.oldest.year }), { eyebrow: _("The era you lived in") });
        const yrs = [null, ...Array.from({ length: 10 }, (z, k) => Y - 9 + k)], cnt = yrs.map(y => dated.filter(i => y == null ? i.year < Y - 9 : i.year === y).length), cmax = Math.max(...cnt, 1), w = 920 / yrs.length, room = bottom - (t.y + 60);
        yrs.forEach((y, i) => { const h = cnt[i] ? 16 + cnt[i] / cmax * room : 6; db += G("growy", 300 + i * 50, `<rect x="${80 + i * w}" y="${bottom - h}" width="${w - 10}" height="${h}" rx="10" fill="${y != null && y >= Y - 2 ? LOGO.blue : "#d5e3ec"}"/>`) + T(80 + i * w + (w - 10) / 2, bottom + 44, y == null ? _("older") : `’${String(y).slice(2)}`, 34, { fill: MUTED, anchor: "middle" }); });
        return t.svg + db + legend([{ fill: LOGO.blue, label: _("the last three years") }, { fill: "#d5e3ec", label: _("earlier years") }], 912);
      }
      const lineEra = eraMode === "old" ? _("Only {pct}% came out in the last three years. Half your year was made before {year}.", { pct: rpct, year: S.medianYear }) : _("Half your year was made before {year}, and {pct}% came out in the last three years.", { pct: rpct, year: S.medianYear });
      t = cardTop(S.medianYear, lineEra, { eyebrow: _("The era you lived in") });
      const dmax = Math.max(...dl.map(d => d.n)), w = Math.min(140, 930 / dl.length), room = bottom - (t.y + 60);
      // a label wider than its bar (as in some languages) goes on every other decade, the highlighted one always
      const hi = Math.floor(S.medianYear / 10) * 10, labs = dl.map(d => _("{d}s", { d: String(d.decade).slice(2) })), lw = Math.max(...labs.map(l => EST(l, 34))), every = lw > w - 6 ? 2 : 1, hk = dl.findIndex(d => d.decade === hi);
      dl.forEach((d, i) => { const h = 24 + d.n / dmax * room, cx = Math.min(1000 - lw / 2, Math.max(80 + lw / 2, 80 + i * w + (w - 10) / 2)); db += G("growy", 300 + i * 50, `<rect x="${80 + i * w}" y="${bottom - h}" width="${w - 10}" height="${h}" rx="10" fill="${d.decade === hi ? LOGO.blue : "#d5e3ec"}"/>`) + ((i - hk) % every === 0 ? T(cx, bottom + 44, labs[i], 34, { fill: d.decade === hi ? INK : MUTED, anchor: "middle" }) : ""); });
      return t.svg + db + legend([{ fill: LOGO.blue, label: _("the decade of {year}", { year: S.medianYear }) }, { fill: "#d5e3ec", label: _("titles by decade of release") }], 912);
    } });
    const ppl = S.creators.concat(S.actors).slice(0, 5);
    cards.push({ id: "people", name: _("People"), kind: "computed", interest: ok(ppl.length >= 1, _("nobody appears on two titles")), bg: () => TINT.green, body: () => {
      const t = cardTop(_("On repeat"), "", { eyebrow: _("People you kept returning to") }), pitch = Math.min(112, (880 - t.y - 60) / ppl.length);
      return t.svg + ppl.map((p, i) => { const yy = t.y + 100 + i * pitch; return G("pop", 300 + i * 100, `<circle cx="124" cy="${yy - 14}" r="44" fill="#dfe5dc"/>` + T(124, yy + 2, p.name.trim().slice(0, 1).toUpperCase(), 42, { weight: 800, anchor: "middle" })) + T(196, yy - 10, trunc(p.name, CJK(p.name) ? 16 : 28), 42, { weight: 700, cls: "rise", d: 350 + i * 100 }) + T(196, yy + 32, [ROLE_LABEL[p.roles.filter(r => r !== "person")[0]] || "", _n("{n} title", "{n} titles", p.n)].filter(Boolean).join(" · "), 34, { fill: MUTED, cls: "rise", d: 400 + i * 100 }); }).join("");
    } });
    return cards;
  }
  function socialCards() {
    const cards = []; const ok = (cond, reason) => cond ? { ok: true } : { ok: false, reason };
    const need = social.enough ? null : _("fewer than three followed members with visible marks this year");
    const fname = f => `@${f.handle}`;
    const initials = (fs, x, y, d) => fs.slice(0, 7).map((f, i) => G("pop", d + i * 80, `<circle cx="${x + i * 76}" cy="${y}" r="32" fill="#dfe5dc" stroke="#fff" stroke-width="4"/>` + T(x + i * 76, y + 11, f.name.trim().slice(0, 1).toUpperCase(), 30, { weight: 800, anchor: "middle" }))).join("");
    const c = social.company;
    cards.push({ id: "company", name: _("In good company"), kind: "social", interest: need ? { ok: false, reason: need } : ok(!!c, _("no title shared with two or more followed members")), bg: () => TINT.green, body: () => {
      const t = cardTop(_("You and {n}", { n: c.fs.length }), _("people you follow finished the same title this year"), { eyebrow: _("In good company") });
      const fy0 = t.y + 56, cw = featureW(fy0, 360);
      return t.svg + feature(c.it, fy0, [titleRow(c.it.title), line(who(c.it))], 300, cw) + initials(c.fs, 80 + cw + 72, fy0 + cw * 1.5 - 40, 600);
    }, named: () => c ? c.fs.map(fname) : [] });
    const t2 = social.twin;
    cards.push({ id: "twin", name: _("Taste twin"), kind: "social", interest: need ? { ok: false, reason: need } : ok(!!t2, _("nobody you follow shares two titles with you")), bg: () => TINT.coral, body: () => {
      const t = cardTop(t2.name, _("@{handle} shares {n} of your titles this year, more than anyone you follow", { handle: t2.handle, n: t2.shared.length }), { eyebrow: _("Taste twin") });
      return t.svg + coverRow(t2.shared.map(u => mine.get(u)), t.y + 60, 4, 400).svg;
    }, named: () => t2 ? [fname(t2)] : [] });
    const f = social.first;
    cards.push({ id: "first", name: _("Who got there first"), kind: "social", interest: need ? { ok: false, reason: need } : ok(f && (f.count + f.after) >= 3, _("too few shared titles with dates")), bg: () => TINT.blue, body: () => {
      const t = cardTop(String(f.count), _("times someone you follow finished a title after you did"), { eyebrow: _("Who got there first"), count: f.count });
      let b = t.svg;
      if (f.it) b += feature(f.it, t.y + 50, [label(_("Most followed")), titleRow(f.it.title, 40), line(_("{n} of them came after you", { n: f.itN }))], 400, 180);
      return b + T(80, 836, f.after, 72, { weight: 800, ls: -2, cls: "rise", d: 700, count: f.after }) + T(80 + EST(f.after, 72, 800) + 20, 836, f.leader ? _("times you came after someone,") : _("times you came after someone"), 34, { fill: MUTED, cls: "rise", d: 800 }) + (f.leader ? T(80 + EST(f.after, 72, 800) + 20, 878, _("most often after {name}", { name: trunc(f.leader.name, 30) }), 34, { fill: MUTED, cls: "rise", d: 850 }) : "");
    }, named: () => f && f.leader ? [fname(f.leader)] : [] });
    const o = social.obscure;
    cards.push({ id: "obscure", name: _("Off the beaten path"), kind: "social", interest: ok(o != null && o >= 0.2, o == null ? _("no mark counts") : _("only {pct}% of titles have fewer than ten marks", { pct: Math.round(o * 100) })), bg: () => TINT.paper, body: () => {
      const rare = withMarks.filter(i => i.marks < 10).sort((a, b) => (b.rating || 0) - (a.rating || 0)).slice(0, 4);
      const t = cardTop(`${Math.round(o * 100)}%`, _("of your year is titles fewer than ten people on {site} have marked", { site: S.site }), { eyebrow: _("Off the beaten path") });
      const r = coverRow(rare, t.y + 60, 4, 400);
      return t.svg + r.svg + rare.map((it, i) => T(80 + i * (r.w + 20), r.bottom + 44, _n("{n} mark", "{n} marks", it.marks), 34, { fill: MUTED, cls: "fade", d: 500 + i * 90 })).join("");
    } });
    const cr = social.crowd, gr = social.grain;
    cards.push({ id: "crowd", name: _("With the crowd, against it"), kind: "social", interest: ok(cr || (gr && gr.below >= 70), _("no rated title with a known distribution")), bg: () => TINT.green, body: () => {
      const grOk = gr && gr.below >= 70, t = cardTop(cr && grOk ? _("Both sides") : cr ? _("With the crowd") : _("Against the grain"), "", { eyebrow: _("How others rated it") });
      let y = t.y + 54, b = t.svg;
      const row = (it, lbl, l1, l2, pct, color, d) => coverSvg(it, 80, y, 170, 255, "pop", d) + T(290, y + 34, lbl.toUpperCase(), 30, { fill: PRIMARY, weight: 700, ls: 3, cls: "fade", d: d + 100 }) + T(290, y + 84, trunc(it.title, CJK(it.title) ? 16 : 28), 40, { weight: 700, cls: "rise", d: d + 150 }) + T(290, y + 132, l1, 34, { fill: MUTED, cls: "rise", d: d + 200 }) + T(290, y + 174, l2, 34, { fill: MUTED, cls: "rise", d: d + 250 }) + G("growx", d + 300, `<rect x="290" y="${y + 204}" width="${pct * 6.9}" height="20" rx="10" fill="${color}"/>`) + `<rect x="290" y="${y + 204}" width="690" height="20" rx="10" fill="none" stroke="${LINE}" stroke-width="2"/>`;
      if (cr) { b += row(cr.it, _("With the crowd"), _("your five stars put you with the {pct}%", { pct: cr.share }), _("who gave it five too"), cr.share, LOGO.green, 200); y += 300; }
      if (grOk) b += row(gr.it, _("Against the grain"), _("you rated it higher than {pct}%", { pct: gr.below }), _("of the {n} people who rated it", { n: gr.it.marks }), gr.below, LOGO.coral, 600);
      return b;
    } });
    const e = social.early;
    cards.push({ id: "early", name: _("Early"), kind: "social", interest: ok(!!e, _("no title where you were among the first ten markers, or the data is not public")), bg: () => TINT.coral, body: () => {
      const t = cardTop(_("#{n}", { n: e.first_rank }), _("to mark it on {site}", { site: S.site }), { eyebrow: _("Early") });
      return t.svg + feature(e, t.y + 56, [titleRow(e.title), line(who(e)), { gap: 20 }, line(_("{n} people have marked it since.", { n: e.marks }), 34, INK)], 300, 360);
    } });
    const it = social.instanceTop, have = it && S.items.find(i => path(i.url) === path(it.url));
    cards.push({ id: "part", name: _("Part of the year"), kind: "social", interest: ok(!!it, _("the instance totals are not ready yet")), bg: () => TINT.blue, body: () => {
      // the number of people is the message; the title sits beside its cover
      const k = it.count, t = cardTop(_n("{n} person", "{n} people", k), _("finished this title, more than any other"), { eyebrow: _("{site} in {year}", { site: S.site, year: Y }), count: k });
      return t.svg + feature(have || { cover: it.cover, category: it.category || "movie", title: it.title }, t.y + 56, [label(_("The most finished")), titleRow((have || it).title), have ? line(who(have)) : null, { gap: 20 }, line(have ? _("You were one of them.") : _("Maybe next year."), 34, INK)], 300, 380);
    } });
    const p = social.percentile;
    cards.push({ id: "pack", name: _("Ahead of the pack"), kind: "social", interest: ok(p != null && p > 50, p != null ? _("below the median this year, so this card stays quiet") : _("the instance percentile is not ready yet")), bg: () => TINT.green, body: () => {
      const t = cardTop(`${p}%`, _("of active members on {site} completed fewer titles than you this year", { site: S.site }), { eyebrow: _("Ahead of the pack") }), by = Math.max(t.y + 170, 640), px = 80 + p * 9.2;
      return t.svg + G("growx", 400, `<rect x="80" y="${by}" width="${p * 9.2}" height="48" rx="24" fill="${LOGO.green}"/>`) + `<rect x="80" y="${by}" width="920" height="48" rx="24" fill="none" stroke="${LINE}" stroke-width="2"/><line x1="540" y1="${by - 20}" x2="540" y2="${by + 68}" stroke="${INK}" stroke-width="3" stroke-dasharray="6 6"/>` + T(540, by + 112, _("the median member"), 34, { fill: MUTED, anchor: "middle" }) + G("pop", 700, `<circle cx="${px}" cy="${by + 24}" r="18" fill="#fff" stroke="${INK}" stroke-width="5"/>` + T(px, by - 32, _("you"), 34, { weight: 800, anchor: p > 92 ? "end" : "middle" })) + T(80, by + 112, _("fewer titles"), 34, { fill: MUTED }) + T(1000, by + 112, _("more"), 34, { fill: MUTED, anchor: "end" });
    } });
    return cards;
  }
  function extras() {
    const cards = []; const ok = (cond, reason) => cond ? { ok: true } : { ok: false, reason };
    const ly = D.last_year;
    cards.push({ id: "versus", name: _("Versus last year"), kind: "computed", interest: TREND.eligible(TR).ok ? { ok: false, reason: _("Year over year takes its place with five or more years of marks") } : ok(!!ly, _("no previous year on record")), bg: () => TINT.paper, body: () => {
      const tot = Object.values(ly).reduce((a, b) => a + b, 0), diff = n - tot, t = cardTop(_n("{n} title", "{n} titles", Math.abs(diff), { n: `${diff >= 0 ? "+" : ""}${diff}` }), _("{last}: {a}. {year}: {b}.", { last: Y - 1, a: tot, year: Y, b: n }), { eyebrow: _("Versus last year") });
      const pitch = Math.min(66, (840 - t.y - 60) / S.cats.length);
      let rows = "";
      S.cats.forEach((c, i) => { const a = ly[c] || 0, b = S.byCat[c].length, mx = Math.max(a, b, 1), y = t.y + 60 + i * pitch; rows += T(80, y + 32, CAT_LABEL[c], 34, { fill: INK }) + G("growx", 300 + i * 60, `<rect x="330" y="${y}" width="${a / mx * 500}" height="20" rx="10" fill="${LINE}"/>`) + G("growx", 400 + i * 60, `<rect x="330" y="${y + 24}" width="${b / mx * 500}" height="20" rx="10" fill="${CAT_COLOR[c]}"/>`) + T(1000, y + 34, `${a} → ${b}`, 34, { fill: b >= a ? INK : MUTED, weight: 700, anchor: "end", cls: "fade", d: 500 + i * 60 }); });
      return t.svg + rows + legend([{ fill: LINE, label: String(Y - 1) }, { stripes: S.cats.slice(0, 4).map(c => CAT_COLOR[c]), label: _("{year}, in each shelf's colour", { year: Y }) }], 900);
    } });
    // Finally: titles finished this year that were on the wishlist first, with the longest waits
    // (on the site the wishlist date comes from the shelf log, which has no visibility of its own)
    const fy = iso => _("{date}, {year}", { date: fmtDate(iso), year: iso.slice(0, 4) }), wait = k => k >= 730 ? _("{n} years", { n: Math.floor(k / 365) }) : k >= 365 ? _("over a year") : k >= 60 ? _("{n} months", { n: Math.round(k / 30.4) }) : _n("{n} day", "{n} days", k);
    const wishData = S.items.some(i => i.wishlisted), fromWish = S.items.filter(i => i.wishlisted && i.wishlisted < i.date).map(i => ({ it: i, days: Math.round((new Date(i.date + "T12:00:00") - new Date(i.wishlisted + "T12:00:00")) / 864e5) })).sort((a, b) => b.days - a.days);
    const finOk = fromWish.length >= RULE.WISH || (fromWish.length > 0 && fromWish[0].days >= RULE.WAIT);
    cards.push({ id: "finally", name: _("Finally"), kind: "computed", interest: ok(finOk, !wishData ? _("wishlist history is not in this data") : fromWish.length ? _("only {n} came off the wishlist, and none waited a year", { n: fromWish.length }) : _("nothing came off the wishlist this year")), bg: () => TINT.green, body: () => {
      const f0 = fromWish[0], rest = fromWish.slice(1, 4), k = fromWish.length;
      const t = cardTop(_n("{n} title", "{n} titles", k), _("came off your wishlist this year; the longest wait was {wait}", { wait: wait(f0.days) }), { eyebrow: _("Finally"), count: k });
      const y = t.y + 50, listH = rest.length ? 64 + (rest.length - 1) * 44 : 0, w = featureW(y, Math.min(260, Math.floor((880 - y - listH) / 1.5))), ly = y + w * 1.5 + 64;
      return t.svg + feature(f0.it, y, [label(_("Waited longest")), titleRow(f0.it.title, 40), line(_("saved {date}", { date: fy(f0.it.wishlisted) })), line(_("finished {date}", { date: fy(f0.it.date) }))], 300, w)
        + rest.map((x, j) => T(80, ly + j * 44, wrapW(x.it.title, 34, 400, 660, 1)[0], 34, { cls: "fade", d: 600 + j * 60 }) + T(1000, ly + j * 44, wait(x.days), 34, { fill: MUTED, anchor: "end", cls: "fade", d: 600 + j * 60 })).join("");
    } });
    const w = D.waiting;
    cards.push({ id: "waiting", name: _("Unfinished business"), kind: "computed", interest: finOk ? { ok: false, reason: _("Finally takes its place; show both if you like") } : ok(!!w, _("wishlist history is not public here")), bg: () => TINT.coral, body: () => {
      const t = cardTop(_n("{n} day", "{n} days", w.days), _("on your wishlist, and counting"), { eyebrow: _("Unfinished business"), count: w.days });
      return t.svg + feature(w, t.y + 56, [titleRow(w.title), { gap: 20 }, line(_("Maybe {year}.", { year: Y + 1 }), 34, INK)], 300, 360);
    } });
    const byDay = {}; S.items.forEach(i => (byDay[i.date] = byDay[i.date] || []).push(i)); const day = Object.entries(byDay).filter(([d]) => !BULK.has(d)).sort((a, b) => b[1].length - a[1].length)[0];
    cards.push({ id: "marathonday", name: _("Marathon day"), kind: "computed", interest: ok(day && day[1].length >= 4, _("no day with four or more marks")), bg: () => TINT.green, body: () => {
      const t = cardTop(fmtDate(day[0]), _("{n} titles marked in one day", { n: day[1].length }), { eyebrow: _("Marathon day") }), r = coverRow(day[1], t.y + 60, 4, 300);
      return t.svg + r.svg + (day[1].length > 4 ? T(80, r.bottom + 48, _("and {n} more", { n: day[1].length - 4 }), 34, { fill: MUTED, cls: "fade", d: 800 }) : "");
    } });
    // Your tags: the most used tags of the year, counted at the page's visibility, and the titles under the top tag
    const lvT = levelOf(deckState.vis), tagData = S.items.some(i => Array.isArray(i.tags)), tagMap = new Map(); let tagged = 0;
    S.items.forEach(i => { const ts = (i.tags || []).filter(([, v]) => (v || 0) <= lvT); if (ts.length) tagged++; ts.forEach(([t]) => { if (!tagMap.has(t)) tagMap.set(t, []); tagMap.get(t).push(i); }); });
    const tagList = [...tagMap.entries()].map(([t, its]) => ({ t, its })).sort((a, b) => b.its.length - a.its.length || a.t.localeCompare(b.t));
    cards.push({ id: "tags", name: _("Your tags"), kind: "computed", interest: ok(tagged >= RULE.TAGGED, !tagData ? _("tags are not in this data") : tagged ? _n("only {n} title tagged, under {min}", "only {n} titles tagged, under {min}", tagged, { min: RULE.TAGGED }) : _("no tags on this year's titles")), bg: () => TINT.coral, body: () => {
      const rows = tagList.slice(0, 5), mx = rows[0].its.length;
      const t = cardTop(_n("{n} tag", "{n} tags", tagList.length), _("on {n} titles; you used “{tag}” most", { n: tagged, tag: rows[0].t }), { eyebrow: _("Your tags"), count: tagList.length }), y0 = t.y + 60, pitch = Math.min(96, (880 - y0) / rows.length);
      const grid = rows[0].its.slice(-9), g = 12, gh = Math.floor((900 - y0 - 14 - 2 * g) / 3), gw = Math.round(gh / 1.5), gx = 1000 - 3 * gw - 2 * g;
      const cell = j => [gx + (j % 3) * (gw + g), y0 + 14 + Math.floor(j / 3) * (gh + g)];
      return t.svg + rows.map((r, k) => { const y = y0 + k * pitch, bw = 30 + r.its.length / mx * 270; return T(80, y + 36, wrapW(r.t, 36, 700, gx - 120, 1)[0], 36, { weight: 700, cls: "rise", d: 300 + k * 90 }) + G("growx", 350 + k * 90, `<rect x="80" y="${y + 54}" width="${bw}" height="18" rx="9" fill="${k ? MUTED : LOGO.coral}"${k ? ` opacity="0.35"` : ""}/>`) + T(80 + bw + 14, y + 71, r.its.length, 34, { fill: MUTED, weight: 700, cls: "fade", d: 450 + k * 90 }); }).join("")
        + T(gx, y0 - 6, wrapW(_("tagged “{tag}”", { tag: rows[0].t }), 34, 400, 1000 - gx, 1)[0], 34, { fill: MUTED })
        + Array.from({ length: 9 }, (z, j) => { const [x, y] = cell(j); return grid[j] ? coverSvg(grid[j], x, y, gw, gh, "pop", 400 + j * 50, "fill") : `<rect x="${x}" y="${y}" width="${gw}" height="${gh}" rx="${Math.round(gw * 0.06)}" fill="${INK}" opacity="0.06"/>`; }).join("");
    } });
    // the essay: the longest review when there is one, else the longest comment
    const es = S.essay;
    const RV = (D.reviews || []).filter(r => String(r.date).startsWith(String(Y)) && (r.vis || 0) <= lvT).map(r => ({ ...r, it: S.items.find(i => i.url === r.u) || null }));
    const rv = RV.slice().sort((a, b) => (b.it ? 1 : 0) - (a.it ? 1 : 0) || b.chars - a.chars)[0] || null;
    cards.push({ id: "essay", name: _("The essay"), kind: "computed", interest: ok(rv || (es && es.comment.length >= 140), es ? _("longest comment is {n} characters, under 140, and no review this year", { n: es.comment.length }) : _("no comments or reviews this year")), bg: () => TINT.paper, body: () => {
      const text = rv ? rv.excerpt : es.comment, cjk = CJK(text), t = cardTop(rv ? rv.title : _("In your own words"), "", { eyebrow: _("The essay") });
      const qy = t.y + 70, lines = wrapText(text, cjk ? 22 : 44, Math.floor((730 - qy) / 54) + 1);
      const it = rv ? rv.it : es, meta = rv ? (RV.length > 1 ? (cjk ? _("{n} characters, your longest review of {count}", { n: rv.chars, count: RV.length }) : _("{n} words, your longest review of {count}", { n: rv.words, count: RV.length })) : (cjk ? _("{n} characters, your longest review", { n: rv.chars }) : _("{n} words, your longest review", { n: rv.words }))) : _("{n} characters, your longest comment", { n: es.comment.length });
      return t.svg + lines.map((l, i) => T(80, qy + i * 54, (i === 0 ? "“" : "") + l + (i === lines.length - 1 ? "…”" : ""), 38, { weight: 500, cls: "rise", d: 150 + i * 80 })).join("")
        + (() => { const cy = Math.min(760, qy + (lines.length - 1) * 54 + 64), tx = it ? 210 : 80; return (it ? coverSvg(it, 80, cy, 100, 150, "pop", 800, "bottom") : "") + T(tx, cy + 60, wrapW(it ? it.title : rv.title, 34, 700, 1000 - tx, 1)[0], 34, { weight: 700, cls: "rise", d: 850 }) + T(tx, cy + 104, meta, 34, { fill: MUTED, cls: "rise", d: 900 }); })();
    } });
    return cards;
  }
  function outro() { return { id: "outro", name: _("Outro"), kind: "computed", interest: { ok: true }, bg: () => TINT.own2, body: () => `${blobs(LOGO.coral, LOGO.blue, LOGO.green)}${T(80, 520, _("See you in"), 120, { weight: 800, ls: -3.6, cls: "rise", d: 100 })}${T(80, 660, Y + 1, 120, { fill: PRIMARY, weight: 800, ls: -3.6, cls: "rise", d: 250 })}${T(80, 750, D.member.url, 34, { fill: MUTED, cls: "fade", d: 400 })}` }; }

  // ---------- made cards: top N as a collection ----------
  // the deck starts from the member's default post visibility; the server keeps the member's choices
  let deckState = Object.assign({ vis: "me", order: [], hidden: {} }, D.deck || {}), made = deckState.made || [], arranging = false, saveTimer = null, savedShown = JSON.stringify(D.shown || null);
  delete deckState.made;
  function persist(extra) {
    if (!OWNER) return Promise.resolve({});
    const shown = (state.shown || []).map(c => c.id);
    savedShown = JSON.stringify(shown);
    return fetch(URLS.save, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF }, body: JSON.stringify(Object.assign({ order: deckState.order, hidden: deckState.hidden, made, shown }, extra || {})) }).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
  }
  const saveDeck = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => persist().catch(() => toast(_("Could not save the deck; try again"))), 300); };
  // ---------- made cards: items from highest marks, a collection, or picked by hand ----------
  const site = (D.member.url.match(/^https?:\/\/([^/]+)/) || [])[1] || "neodb.social";
  const myUrls = new Map(S.items.map(i => [i.url, i]));
  const COLS = D.collections || [];
  const refOf = it => ({ url: it.url, title: it.title, cover: it.cover, category: it.category, note: null });
  const refRaw = x => ({ url: /^https?:/.test(x.u) ? x.u : base + x.u, title: x.t, cover: x.cov, category: x.c, note: x.n || null });
  function autoPool(cat) { return (cat === "all" ? S.items : S.byCat[cat] || []).slice().sort((a, b) => (b.rating || 0) - (a.rating || 0) || (b.site_rating || 0) - (a.site_rating || 0)); }
  function resolveMade(m) {
    const src = m.source || (m.items ? { type: "legacy" } : { type: "auto", cat: "all" });
    if (src.type === "auto") return autoPool(src.cat).slice(0, m.n || 5).map(refOf);
    if (src.type === "collection") { const c = COLS.find(x => x.uuid === src.uuid); return c ? c.items.slice(0, m.n || 5).map(refRaw) : []; }
    if (src.type === "hand") return src.items.slice(0, 10);
    return (m.items || []).map(id => S.items.find(i => i.id === id)).filter(Boolean).map(refOf); // cards saved before sources existed
  }
  // the default title and caption of a made card, from its source
  function madeDefault(m) {
    const src = m.source || { type: "auto", cat: "all" }, n = m.n || 5;
    if (src.type === "hand") { const k = (src.items || []).length; return { title: k ? _("My {n} picks of {year}", { n: k, year: Y }) : _("My top picks of {year}", { year: Y }), caption: _("Chosen by hand") }; }
    if (src.type === "collection") { const c = COLS.find(x => x.uuid === src.uuid); return { title: c ? trunc(c.title, 52) : "", caption: c && c.rel === "liked" ? _("From @{owner}'s collection", { owner: c.owner }) : _("From my collection") }; }
    const cat = src.cat || "all"; return { title: cat === "all" ? _("My top {n} titles of {year}", { n, year: Y }) : _("My top {n} {shelf} of {year}", { n, shelf: CAT_NOUN[cat], year: Y }), caption: _("Highest marks") };
  }
  function topNBody(m, its) {
    // the title of a made card names a list: one line at 96 px at most, else 64 px, so the covers keep the room
    const dft = madeDefault(m), N = its.length, t = cardTop(m.title || dft.title, "", { eyebrow: m.caption || dft.caption || _("My pick"), max: 96 });
    let b = t.svg;
    const badge = (x, y, r, d) => G("pop", d, `<circle cx="${x}" cy="${y}" r="28" fill="${INK}"/>` + T(x, y + 11, r, 30, { fill: "#fff", weight: 800, anchor: "middle" }));
    const cell = (it, x, y, w, h, r, d, capW) => coverSvg(it, x, y, w, h, "pop", d, "bottom") + badge(x + 10, y + 10, r, d + 100) + wrapW(it.title, 34, 400, capW || Math.max(w, 120), 2).map((l, j) => T(x, y + h + 40 + j * 40, l, 34, { fill: MUTED, cls: "fade", d: d + 150 })).join("");
    // the covers fill the room between the title and the footer; 96 px under each row for a caption of two lines
    const top0 = t.y + 56, H = 900 - top0, Wd = 920;
    if (N === 3) {
      const h = H - 96, w = Math.min(320, h / 1.5), w2 = Math.round(w * 0.78), h2 = w2 * 1.5;
      [[0, 540 - w / 2, top0, w, w * 1.5], [1, 80, top0 + w * 1.5 - h2, w2, h2], [2, 1000 - w2, top0 + w * 1.5 - h2, w2, h2]].forEach(([i, x, y, cw, ch], k) => { if (its[i]) b += cell(its[i], x, y, cw, ch, i + 1, 300 + k * 150); });
      return b;
    }
    if (N === 5) {
      const h1 = H - 96, w1 = Math.min(330, h1 / 1.5), rx = 80 + w1 + 40, cw = (1000 - rx - 24) / 2, ch = (H - 24 - 192) / 2, w = Math.min(cw, ch / 1.5);
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
  const madeCards = () => made.map((m, i) => { const its = resolveMade(m); return { id: "made-" + m.id, name: m.title || madeDefault(m).title, kind: "made", interest: its.length >= 3 ? { ok: true } : { ok: false, reason: _("fewer than three items can be shown, so the collection may have changed") }, bg: () => [TINT.paper, TINT.blue, TINT.green, TINT.coral][i % 4], body: () => topNBody(m, its), made: m }; });

  // ---------- assemble ----------
  function allCards() {
    const c = computed(), s = socialCards(), x = extras();
    const by = id => c.find(k => k.id === id);
    const tc = TREND.cards(TR, UI);
    const xb = id => x.find(k => k.id === id);
    return ordered([by("intro"), by("covers"), by("shelves"), tc.yoy, by("busiest"), by("biggest"), by("top"), by("quiet"), by("disagree"), by("streak"), by("weeks"), xb("marathonday"), by("era"), tc.taste, by("people"), xb("tags"), ...s, ...madeCards(), xb("versus"), xb("finally"), xb("waiting"), xb("essay"), tc.bests, outro()].filter(Boolean));
  }
  // one visibility for the whole page; a card is only shown or hidden, and a card the rules reject starts hidden with its reason
  const PAGEVIS = { public: { label: _("Public"), note: _("Anyone with the link sees the page; counts use public entries only.") }, followers: { label: _("Followers only"), note: _("Only your followers see the page; counts include followers-only entries.") }, me: { label: _("Only me"), note: _("Only you see the page; counts include private entries.") } };
  // the member's order, if any; intro and outro stay put, and a card new since the last arrange goes just before the outro
  const FIXED = new Set(["intro", "outro"]);
  // hidden unless the member says otherwise: a card the rules reject, and in a small year every card outside the compact set
  const byDefault = c => !c.interest.ok || (SMALL && !COMPACT.has(c.id) && c.kind !== "made");
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
    const visible = OWNER ? null : new Set(D.shown || []);
    cards.forEach(c => { const own = deckState.hidden[c.id]; c.hidden = visible ? !visible.has(c.id) : own != null ? own : byDefault(c); c.why = own ? _("hidden by you") : !c.interest.ok ? c.interest.reason : byDefault(c) ? _("not shown in a small year, under {n} titles", { n: RULE.SMALL }) : ""; });
    cards.forEach(c => { if (!c.hidden) { try { c.svg = card(c.bg(), c.body(), c.foot); } catch (e) { console.warn("card failed", c.id, e); c.hidden = true; c.why = _("could not be drawn from this data"); } } if (c.hidden) c.svg = card(TINT.paper, eyebrow(c.name) + T(80, 330, c.name, 64, { weight: 800, ls: -2 }), c.foot); });
    const shown = cards.filter(c => !c.hidden), hid = cards.filter(c => c.hidden), mid = shown.filter(c => !FIXED.has(c.id));
    const deck = $("deck");
    io.disconnect(); inView.clear();
    deck.innerHTML = shown.map((c, i) => `<div class="card" data-id="${c.id}" data-i="${i}">${c.svg}<div class="cf"><span><span class="n">${i + 1}/${shown.length}</span> · ${esc(c.name)} ${c.kind !== "computed" ? `<span class="kind">${c.kind === "made" ? _("made") : _("social")}</span>` : ""}</span><span class="btns">${OWNER ? `<button class="dc-nav" title="${_("Post this card")}" aria-label="${_("Post this card")}" data-share><i class="fa-solid fa-share-from-square"></i></button>` : ""}<button class="dc-nav" title="${_("Download PNG")}" aria-label="${_("Download PNG")}" data-dl><i class="fa-solid fa-download"></i></button>${OWNER ? `<button class="dc-nav" title="${_("Hide this card")}" aria-label="${_("Hide this card")}" data-hide><i class="fa-solid fa-eye-slash"></i></button>` : ""}</span></div></div>`).join("");
    $("dots").innerHTML = shown.map((z, i) => `<i class="${i === 0 ? "on" : ""}"></i>`).join("");
    const hub = $("hub"); hub.classList.toggle("arranging", arranging);
    const mv = (c, k) => FIXED.has(c.id) ? `<span class="pin"><i class="fa-solid fa-thumbtack"></i> ${c.id === "intro" ? _("always first") : _("always last")}</span>` : `<button type="button" class="outline" data-mv="-1" aria-label="${esc(_("Move {name} earlier", { name: c.name }))}"${k === 0 ? " disabled" : ""}><i class="fa-solid fa-arrow-left"></i></button><button type="button" class="outline" data-mv="1" aria-label="${esc(_("Move {name} later", { name: c.name }))}"${k === mid.length - 1 ? " disabled" : ""}><i class="fa-solid fa-arrow-right"></i></button>`;
    hub.innerHTML = shown.map((c, i) => arranging
      ? `<div class="ht" data-id="${c.id}"${FIXED.has(c.id) ? "" : ` draggable="true"`}><div class="go">${c.svg}<span>${esc(c.name)}</span></div><div class="mv">${mv(c, mid.indexOf(c))}</div></div>`
      : `<div class="ht" data-id="${c.id}"><a href="#" class="go" data-i="${i}">${c.svg}<span>${esc(c.name)}</span></a></div>`).join("")
      + (arranging || !OWNER ? "" : `<button class="make" type="button" id="wr-make"><div class="tile"><i class="fa-solid fa-plus"></i></div><span>${_("Make a card")}</span></button>`);
    $("hubsub").textContent = !OWNER ? _("Pick a card to jump to it.") : arranging ? _("Drag a card to a new place, or use its arrows. The intro stays first and the outro last.") : _("The page stays here all year. Hide a card from its footer, change the order with Arrange, or make your own with the plus.");
    $("decksub").textContent = OWNER ? join([_("{shown} cards shown, {hidden} hidden.", { shown: shown.length, hidden: hid.length }), PAGEVIS[deckState.vis].note]) : _n("{n} card", "{n} cards", shown.length);
    if (OWNER) {
    const ar = $("arrange");
    ar.innerHTML = arranging ? `<i class="fa-solid fa-check"></i> ${_("Done")}` : `<i class="fa-solid fa-arrows-up-down-left-right"></i> ${_("Arrange")}`;
    ar.classList.toggle("primary", arranging); ar.classList.toggle("outline", !arranging);
    $("resetorder").hidden = !deckState.order.length;
    $("hiddensum").textContent = _("Hidden ({n})", { n: hid.length });
    $("hiddenrow").hidden = !hid.length;
    $("hiddenlist").innerHTML = hid.map(c => `<div>${c.svg}<div><b>${esc(c.name)}</b><small>${esc(c.why)}</small></div><span class="set"><button type="button" class="outline" data-show="${c.id}"><i class="fa-solid fa-eye"></i> ${_("Show")}</button></span></div>`).join("");
    document.querySelectorAll(".pagevis button").forEach(b => b.classList.toggle("on", b.dataset.pv === deckState.vis));
    }
    // wire
    const setHidden = (id, h) => { const c = cards.find(x => x.id === id), rule = c && byDefault(c); if (h === rule) delete deckState.hidden[id]; else deckState.hidden[id] = h; saveDeck(); render(); };
    [...deck.children].forEach(el => { io.observe(el); const c = shown[+el.dataset.i]; if (OWNER) { el.querySelector("[data-share]").onclick = () => openPublish(c.id); el.querySelector("[data-hide]").onclick = () => setHidden(c.id, true); } el.querySelector("[data-dl]").onclick = () => downloadPng(c); });
    [...hub.querySelectorAll("a.go")].forEach(a => a.onclick = e => { e.preventDefault(); go(+a.dataset.i); window.scrollTo({ top: 0, behavior: "smooth" }); });
    const mk = $("make"); if (mk) mk.onclick = openMake;
    [...document.querySelectorAll("[data-show]")].forEach(b => b.onclick = () => setHidden(b.dataset.show, false));
    if (arranging) wireArrange(hub, mid.map(c => c.id));
    state.cards = cards; state.shown = shown; state.exportable = shown;
    if (OWNER && JSON.stringify(shown.map(c => c.id)) !== savedShown) saveDeck();
    setTimeout(() => { const dr = deck.getBoundingClientRect(); [...deck.children].forEach(el => { const r = el.getBoundingClientRect(); const seen = Math.min(r.right, dr.right) - Math.max(r.left, dr.left); if (seen / r.width >= 0.6) reveal(el); }); }, 60);
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
    $("arrange").onclick = () => { arranging = !arranging; render(); };
    $("resetorder").onclick = () => { deckState.order = []; render(); saveDeck(); toast(_("Back to the default order")); };
    // the counts on every card depend on the page's visibility, so a change reloads the page from the server
    document.querySelectorAll(".pagevis button").forEach(b => b.onclick = () => { if (b.dataset.pv === deckState.vis) return; persist({ vis: b.dataset.pv }).then(r => { if (r.reload) location.reload(); }).catch(() => toast(_("Could not change who can see the page"))); });
  }
  // count-up and reveal
  // the current card is the leftmost one in view, so the dots and the arrows agree on first load
  const inView = new Set();
  const io = new IntersectionObserver(es => { es.forEach(e => { const i = +e.target.dataset.i; if (e.isIntersecting) { reveal(e.target); inView.add(i); } else inView.delete(i); }); if (inView.size) cur = Math.min(...inView); [...$("dots").children].forEach((d, i) => d.classList.toggle("on", i === cur)); }, { root: $("deck"), threshold: 0.6 });
  function reveal(el, again) { if (again) { el.classList.remove("in"); void el.offsetWidth; } if (el.classList.contains("in")) return; el.classList.add("in"); el.querySelectorAll("text[data-count]").forEach(t => { const to = +t.dataset.count, pre = t.dataset.pre || "", suf = t.dataset.suf || "", t0 = performance.now(), dur = 900; const step = now => { const k = Math.min(1, (now - t0) / dur), v = Math.round(to * (1 - Math.pow(1 - k, 3))); t.textContent = pre + v.toLocaleString() + suf; if (k < 1) requestAnimationFrame(step); }; if (!matchMedia("(prefers-reduced-motion: reduce)").matches) requestAnimationFrame(step); }); }
  let cur = 0;
  const go = i => { const deck = $("deck"); cur = Math.max(0, Math.min(deck.children.length - 1, i)); deck.children[cur].scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" }); };
  $("prev").onclick = () => go(cur - 1);
  $("next").onclick = () => go(cur + 1);
  let replaying = null;
  $("replay").onclick = () => { if (replaying) { clearInterval(replaying); replaying = null; return; } let i = 0; const deck = $("deck"); const tick = () => { if (i >= deck.children.length) { clearInterval(replaying); replaying = null; return; } go(i); reveal(deck.children[i], true); i++; }; tick(); replaying = setInterval(tick, 2600); };

  // ---------- hero and lists ----------
  function hero() {
  const act = activityAt(levelOf(deckState.vis));
  // the numbers are bold; the sentences come from the text table with the numbers filled in
  const B = x => `<b>${esc(x)}</b>`, five = S.fiveStar.length;
  $("lede").innerHTML = join([_n("{titles} across {n} shelf.", "{titles} across {n} shelves.", S.cats.length, { titles: B(_n("{n} title", "{n} titles", n)) }),
    TM.counts[TM.busiest] >= RULE.BUSY_MIN ? _("{month} was the busiest month with {n}; the quietest was {quiet}.", { month: esc(MONTHS_LONG[TM.busiest]), n: B(TM.counts[TM.busiest]), quiet: esc(MONTHS_LONG[TM.quietest]) }) : "",
    five ? _n("The longest run of days with something logged was {days}, and {n} title got five stars.", "The longest run of days with something logged was {days}, and {n} titles got five stars.", five, { days: B(act.streak), n: B(five) }) : _("The longest run of days with something logged was {days}.", { days: B(act.streak) }),
    S.lastMonth < 11 ? _("The year is still running.") : ""]);
  $("stats").innerHTML = [[n, _("titles completed")], [act.days, _("days with something logged")], [MONTHS_LONG[TM.busiest], _n("busiest month, {n} title", "busiest month, {n} titles", TM.counts[TM.busiest])], [S.avg ? (S.avg / 2).toFixed(1) : _n("{n} day", "{n} days", act.streak), S.avg ? _("average rating, of 5") : _("longest streak")]].map(([a, b]) => `<div class="dc-surface"><span class="num">${a}</span><span class="lbl">${b}</span></div>`).join("");
  }
  hero();
  // the full list, with one shelf at a time or all of them
  let bymCat = "all";
  function byMonth() {
    $("bym").innerHTML = S.byMonth.map((all, m) => { const list = bymCat === "all" ? all : all.filter(it => it.category === bymCat); return list.length ? `<div class="month-h"><h3>${MONTHS_LONG[m]}</h3><span class="n">${_n("{n} title", "{n} titles", list.length)}</span></div><div class="bym">${list.map(it => `<div>${cover(it)}<p class="t">${esc(it.title)}</p></div>`).join("")}</div>` : ""; }).join("");
    document.querySelectorAll("#wr-bymcat button").forEach(b => { b.classList.toggle("on", b.dataset.cat === bymCat); b.setAttribute("aria-pressed", b.dataset.cat === bymCat); });
  }
  const bymCats = $("bymcat");
  bymCats.innerHTML = `<button type="button" data-cat="all">${_("All")}</button>` + S.cats.map(c => `<button type="button" data-cat="${c}"><i class="dot" style="background:${CAT_COLOR[c]}"></i><i class="fa-solid ${CAT_ICON[c]}"></i> ${CAT_LABEL[c]}</button>`).join("");
  bymCats.querySelectorAll("button").forEach(b => b.onclick = () => { bymCat = b.dataset.cat; byMonth(); });
  byMonth();

  // ---------- tint from the member's own covers ----------
  async function tintFromCovers() {
    try {
      const imgs = await Promise.all(S.top.slice(0, 8).map(it => new Promise(res => { const im = new Image(); im.onload = () => res(im); im.onerror = () => res(null); im.src = exportHref(it.cover); })));
      const cv = document.createElement("canvas"); cv.width = cv.height = 8; const cx = cv.getContext("2d");
      // one average per cover, then the most saturated of them, so a grey wall of covers does not give a grey tint
      let best = null;
      imgs.filter(Boolean).forEach(im => { cx.drawImage(im, 0, 0, 8, 8); const d = cx.getImageData(0, 0, 8, 8).data; let r = 0, g = 0, b = 0; for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; } r /= 64; g /= 64; b /= 64; const mx = Math.max(r, g, b), mn = Math.min(r, g, b); const sat = mx ? (mx - mn) / mx : 0; if (!best || sat > best.sat) best = { r, g, b, sat }; });
      if (!best) return;
      const mix = (c, p, t) => Math.round(p + (c - p) * t), hex = (r, g, b) => "#" + [r, g, b].map(v => v.toString(16).padStart(2, "0")).join("");
      TINT.own = hex(mix(best.r, 251, 0.18), mix(best.g, 252, 0.18), mix(best.b, 252, 0.18)); TINT.own2 = hex(mix(best.r, 251, 0.3), mix(best.g, 252, 0.3), mix(best.b, 252, 0.3));
      const apply = (el, id) => el && el.querySelector("rect.bg").setAttribute("fill", id === "intro" ? TINT.own : TINT.own2);
      ["intro", "outro"].forEach(id => { apply(document.querySelector(`.deck [data-id="${id}"]`), id); apply(document.querySelector(`.hub .ht[data-id="${id}"]`), id); const c = state.shown.find(x => x.id === id); if (c) c.svg = c.svg.replace(/<rect class="bg" width="1080" height="1080" fill="[^"]*"/, `<rect class="bg" width="1080" height="1080" fill="${id === "intro" ? TINT.own : TINT.own2}"`); });
    } catch (e) { /* a tainted canvas just keeps the default tint */ }
  }

  // ---------- make a card ----------
  const mkdlg = $("mkdlg"), pubdlg = $("pubdlg");
  document.querySelectorAll("[data-close]").forEach(b => b.onclick = () => b.closest("dialog").close());
  let mk = null;
  const el = $;
  function openMake() {
    mk = { n: 5, src: "auto:all", picked: [], lookupTimer: null };
    ["mk-title", "mk-cap", "mk-q"].forEach(id => el(id).value = ""); el("mk-res").innerHTML = "";
    const seg = el("mk-n"); seg.innerHTML = [3, 4, 5, 6, 7, 8, 9, 10].map(k => `<button type="button" class="${k === 5 ? "on" : ""}" data-n="${k}">${k}</button>`).join("");
    seg.querySelectorAll("button").forEach(b => b.onclick = () => { mk.n = +b.dataset.n; seg.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); mkPreview(); });
    const own = COLS.filter(c => c.rel === "own"), liked = COLS.filter(c => c.rel === "liked");
    const opt = c => `<option value="col:${c.uuid}">${esc(trunc(c.title, 40))}${c.rel === "liked" ? ` · @${esc(c.owner)}` : ""} (${c.total})</option>`;
    el("mk-src").innerHTML = `<optgroup label="${_("Highest marks this year")}"><option value="auto:all">${_("Every shelf")}</option>${S.cats.map(c => `<option value="auto:${c}">${CAT_LABEL[c]}</option>`).join("")}</optgroup>`
      + `<optgroup label="${_("Your collections")}">${own.length ? own.map(opt).join("") : `<option disabled>${_("No collections of your own yet")}</option>`}</optgroup>`
      + `<optgroup label="${_("Collections you liked")}">${liked.length ? liked.map(opt).join("") : `<option disabled>${_("No liked collections yet")}</option>`}</optgroup>`
      + `<optgroup label="${_("Your own list")}"><option value="hand">${_("Pick titles or paste links")}</option></optgroup>`;
    el("mk-src").value = "auto:all"; el("mk-src").onchange = () => setSource(el("mk-src").value);
    setSource("auto:all"); mkdlg.showModal();
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
  function mkPreview() {
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
    let r; try { r = await fetch(`${URLS.lookup}?url=${encodeURIComponent(url)}`, { headers: { Accept: "application/json" } }).then(x => x.json()); } catch (e) { r = { status: "error" }; }
    if (el("mk-q").value.trim() !== url) return;
    if (r.status === "found") { const it = { ...refRaw(r.item), how: "link" }; it.sub = `${CAT_LABEL[it.category] || it.category} · ${myUrls.has(it.url) ? _("marked {date}", { date: fmtDate(myUrls.get(it.url).date) }) : _("not marked in {year}", { year: Y })}`; showResults([{ label: _("From the link"), items: [it] }]); return; }
    if (r.status === "fetching" && attempt < 12) { mk.lookupTimer = setTimeout(() => lookupLink(url, attempt + 1), 5000); return; }
    const msg = { fetching: _("Still not ready after a minute. Try the link again later."), unsupported: _("This site is not one this instance can read. Try a link to the title on a supported site, or to its page here."), missing: _("No title at this link on {site}.", { site: esc(site) }), busy: _("Too many lookups just now. Try again in a moment."), invalid: _("That does not look like a full link."), error: _("The lookup failed. Try again.") }[r.status] || _("The lookup failed. Try again.");
    showResults([{ label: _("From the link"), items: [], msg }]);
  }
  if (OWNER) {
  el("mk-q").oninput = e => {
    clearTimeout(mk.lookupTimer); const v = e.target.value.trim();
    if (!v) { el("mk-res").innerHTML = ""; mk.first = null; return; }
    if (isLink(v)) { mk.lookupTimer = setTimeout(() => lookupLink(v, 0), 250); return; }
    const q = v.toLowerCase(), have = new Set(mk.picked.map(x => x.url));
    const year = S.items.filter(i => i.title.toLowerCase().includes(q) && !have.has(i.url)).slice(0, 6).map(i => ({ ...refOf(i), sub: `${CAT_LABEL[i.category]} · ${_("marked {date}", { date: fmtDate(i.date) })}` }));
    const seen = new Set(year.map(i => i.url)); const inCols = [];
    COLS.forEach(c => c.items.forEach(x => { const it = refRaw(x); if (inCols.length < 6 && it.title && it.title.toLowerCase().includes(q) && !have.has(it.url) && !seen.has(it.url)) { seen.add(it.url); inCols.push({ ...it, how: "collection", sub: _("in “{title}”", { title: trunc(c.title, 28) }) }); } }));
    showResults([{ label: _("Marked in {year}", { year: Y }), items: year }, { label: _("In your collections"), items: inCols }, ...(year.length || inCols.length ? [] : [{ label: _("Nothing found"), items: [], msg: _("Paste a link to add a title from anywhere.") }])]);
  };
  el("mk-q").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); if (mk.first) addPicked(mk.first); } };
  el("mk-title").oninput = mkPreview; el("mk-cap").oninput = mkPreview;
  el("mk-save").onclick = () => {
    const its = mkItems(); if (its.length < 3) { toast(_("A card needs at least three titles")); return; }
    const n = mk.src === "hand" ? its.length : mk.n;
    const source = mk.src === "hand" ? { type: "hand", items: its.map(({ url, title, cover, category, note }) => ({ url, title, cover, category, note })) } : mk.src.startsWith("col:") ? { type: "collection", uuid: mk.src.slice(4) } : { type: "auto", cat: mk.src.split(":")[1] };
    made.push({ id: Date.now(), title: el("mk-title").value.trim(), caption: el("mk-cap").value.trim(), n, source });
    mkdlg.close(); render(); saveDeck(); toast(_("Card added to the deck"));
  };  }


  // ---------- publish ----------
  function openPublish(preselect) { const tabs = $("pub-tabs"); const show = t => { tabs.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.t === t)); ["post", "page", "dl"].forEach(k => $("pub-" + k).hidden = k !== t); }; tabs.querySelectorAll("button").forEach(b => b.onclick = () => show(b.dataset.t)); show("post"); const grid = $("pub-grid"); grid.innerHTML = state.exportable.map(c => `<label><input type="checkbox" value="${c.id}" ${c.id === preselect ? "checked" : ""}>${c.svg}</label>`).join(""); const limit = () => { const on = [...grid.querySelectorAll("input:checked")].length; grid.querySelectorAll("label").forEach(l => l.classList.toggle("full", on >= 4 && !l.querySelector("input").checked)); grid.querySelectorAll("input").forEach(i => i.disabled = on >= 4 && !i.checked); }; grid.querySelectorAll("input").forEach(i => i.onchange = limit); limit(); $("pub-note").value = `#${Y}_report`; $("pub-link").value = D.page_url; $("gif-out").innerHTML = ""; pubSummary(); const pr = document.querySelector(`#wr-pub-post input[name="wr-vis"][value="${levelOf(deckState.vis)}"]`); if (pr) pr.checked = true; pubdlg.showModal(); }
  function pubSummary() { $("pub-summary").textContent = join([_("{shown} cards shown, {hidden} hidden.", { shown: state.shown.length, hidden: state.cards.length - state.shown.length }), PAGEVIS[deckState.vis].note]); }
  if (OWNER) $("publish").onclick = () => openPublish();
  if (OWNER) $("pub-send").onclick = async () => {
    const ids = [...document.querySelectorAll("#wr-pub-grid input:checked")].map(i => i.value);
    if (!ids.length) { toast(_("Pick at least one card")); return; }
    const btn = $("pub-send"); btn.disabled = true; btn.setAttribute("aria-busy", "true");
    try {
      const fd = new FormData();
      for (const id of ids) { const c = state.shown.find(x => x.id === id); fd.append("img", (await toCanvas(c, 1080)).toDataURL("image/png")); }
      fd.append("comment", $("pub-note").value);
      fd.append("visibility", (document.querySelector('#wr-pub-post input[name="wr-vis"]:checked') || {}).value || "0");
      const r = await fetch(URLS.share, { method: "POST", headers: { "X-CSRFToken": CSRF, Accept: "application/json" }, body: fd });
      if (!r.ok) throw new Error(r.status);
      pubdlg.close(); toast(_n("Posted {n} card to your timeline", "Posted {n} cards to your timeline", ids.length));
    } catch (e) { toast(_("Posting failed: {error}", { error: e.message })); }
    btn.disabled = false; btn.removeAttribute("aria-busy");
  };
  if (OWNER) $("pub-copy").onclick = () => { navigator.clipboard && navigator.clipboard.writeText($("pub-link").value); toast(_("Link copied")); };
  function toast(t) { const el = document.createElement("div"); el.className = "wr-toast"; el.setAttribute("role", "status"); el.textContent = t; document.body.appendChild(el); setTimeout(() => el.remove(), 2200); }

  // ---------- png and gif export ----------
  const dataCache = new Map(), coverUuid = new Map();
  const uuidOf = u => (String(u || "").split("?")[0].replace(/\/$/, "").split("/").pop() || "");
  S.items.forEach(i => coverUuid.set(i.cover, i.uuid));
  (D.collections || []).forEach(c => c.items.forEach(x => coverUuid.set(x.cov, uuidOf(x.u))));
  function exportHref(h) {
    try { if (new URL(h, location.href).origin === location.origin) return h; } catch (e) { return h; }
    const u = coverUuid.get(h);
    return u && D.cover_url ? D.cover_url.replace("UUID", u) : h;
  }
  async function inlineImages(svg) { const hrefs = [...new Set([...svg.matchAll(/<image href="([^"]+)"/g)].map(m => m[1]))]; for (const h of hrefs) { if (!dataCache.has(h)) { try { const r = await fetch(exportHref(h.replace(/&amp;/g, "&"))); const b = await r.blob(); dataCache.set(h, await new Promise(res => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(b); })); } catch (e) { dataCache.set(h, ""); } } svg = svg.split(`<image href="${h}"`).join(`<image href="${dataCache.get(h)}"`); } return svg; }
  async function toCanvas(c, size) { const svg = await inlineImages(c.svg.replace("<svg ", `<svg width="${size}" height="${size}" `)); const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" })); const im = new Image(); await new Promise((res, rej) => { im.onload = res; im.onerror = rej; im.src = url; }); const cv = document.createElement("canvas"); cv.width = cv.height = size; cv.getContext("2d").drawImage(im, 0, 0, size, size); URL.revokeObjectURL(url); return cv; }
  async function downloadPng(c) { try { const cv = await toCanvas(c, 1080); const a = document.createElement("a"); a.download = `${Y}-${c.id}.png`; a.href = cv.toDataURL("image/png"); a.click(); } catch (e) { toast(_("Export failed: {error}", { error: e.message })); } }
  if (OWNER) $("dl-png").onclick = async () => { for (const c of state.exportable) await downloadPng(c); };
  if (OWNER) $("dl-gif").onclick = async () => { const prog = $("gif-prog"); prog.hidden = false; prog.value = 0; try { const { GIFEncoder, quantize, applyPalette } = await import(`${CDN}/npm/gifenc@1.0.3/dist/gifenc.esm.js`); const gif = GIFEncoder(); const size = 600; for (let i = 0; i < state.exportable.length; i++) { const cv = await toCanvas(state.exportable[i], size); const data = cv.getContext("2d").getImageData(0, 0, size, size).data; const palette = quantize(data, 128); const index = applyPalette(data, palette); gif.writeFrame(index, size, size, { palette, delay: 2500 }); prog.value = (i + 1) / state.exportable.length; } gif.finish(); const blob = new Blob([gif.bytes()], { type: "image/gif" }); const url = URL.createObjectURL(blob); $("gif-out").innerHTML = `<img src="${url}" alt="${_("GIF preview")}"><p class="dc-fine">${_("{size} MB, {n} frames at 2.5 s", { size: (blob.size / 1024 / 1024).toFixed(1), n: state.exportable.length })}</p>`; const a = document.createElement("a"); a.download = `${Y}-wrapped.gif`; a.href = url; a.click(); } catch (e) { toast(_("GIF failed: {error}", { error: e.message })); } prog.hidden = true; };

  render(); tintFromCovers();
})();

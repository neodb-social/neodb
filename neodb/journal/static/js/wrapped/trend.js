// Trend cards for members with five or more years of marks: year over year, personal bests, how your taste moved.
// TREND.model(history, year, current, upTo) prepares the numbers; TREND.cards(model, ui) returns the three cards in the
// deck's card shape ({id, name, kind, interest, bg, body}). `ui` carries the drawing helpers of the page that hosts them.
// Rules, from the production sample of 2026-11-03:
// - the chart shows at most the last ten years, because the first years of a long history are often a backlog;
// - the member's first year of marks is drawn but never compared, for the same reason;
// - titles marked on a day with more than 30 completions are drawn as a light top and left out of every comparison,
//   because an import without dates puts them all on one day;
// - a year in progress is compared with the same months of every earlier year.
(function () {
  const BULK_DAY = 30, SPAN = 10;
  // text comes from the page's table (stats.js); month names follow the page language
  const _ = (s, v) => window.WR._(s, v), _n = (s, p, n, v) => window.WR._n(s, p, n, v);
  const ORD = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"];
  const ordinal = k => ORD[k] || k + (k % 100 >= 11 && k % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][k % 10] || "th");
  const sum = (a, upTo) => a ? a.slice(0, upTo + 1).reduce((x, y) => x + y, 0) : 0;

  // one year, cut at the same month as the deck year
  function cut(y, upTo, full) {
    const u = full ? 11 : upTo, cats = {};
    Object.entries(y.c || {}).forEach(([c, a]) => { const v = sum(a, u); if (v) cats[c] = v; });
    return { val: sum(y.m, u), bulk: sum(y.b, u), cats, days: sum(y.d, u), rated: sum(y.r, u), five: sum(y.f, u), gsum: sum(y.g, u) };
  }
  function model(h, Y, cur, upTo) {
    if (!h || !h.first) return { none: true };
    const firstYear = +h.first.slice(0, 4), joinedYear = +(h.joined || h.first).slice(0, 4), ws = Math.max(firstYear, Y - SPAN + 1);
    const years = h.years || {}, at = yr => yr === Y ? cur : years[yr];
    const win = [];
    for (let yr = ws; yr <= Y; yr++) { const y = at(yr) || {}; win.push({ yr, ...cut(y, upTo), full: cut(y, upTo, true), first: yr === firstYear }); }
    const now = win[win.length - 1], comp = win.filter(w => w.yr < Y && !w.first && w.val > 0);
    const all = Object.keys(years).map(Number).filter(yr => yr < Y && yr !== firstYear).map(yr => ({ yr, ...cut(years[yr], upTo) }));
    // the first year stays out of the comparisons (often a part year), but no headline may claim to beat a bigger one
    const firstVal = cut(at(firstYear) || {}, upTo).val;
    return { firstYear, firstVal, joinedYear, ws, win, now, comp, all, Y, upTo, inProgress: upTo < 11, yearsOfMarks: Y - firstYear + 1, yearsOnSite: Y - joinedYear + 1 };
  }
  // the line for the intro card; imports date marks back to 1978 for some members, so the count is years on the site
  function introLine(t) {
    // a year before the member joined (imported marks) has no place in their years on the site
    if (!t || t.none || t.yearsOnSite < 1) return "";
    return _("your {nth} year on NeoDB", { nth: ordinal(t.yearsOnSite), n: t.yearsOnSite }) + (t.firstYear < t.joinedYear ? _(", with marks back to {year}", { year: t.firstYear }) : "");
  }
  function eligible(t) {
    if (!t || t.none) return { ok: false, reason: _("no earlier years in the data") };
    if (t.yearsOfMarks < 5) return { ok: false, reason: _n("marks go back {n} year; the trend cards need five", "marks go back {n} years; the trend cards need five", t.yearsOfMarks) };
    const good = t.comp.filter(w => w.val >= 10).length;
    if (good < 3) return { ok: false, reason: _n("{n} earlier year since {year} with ten or more titles; the trend cards need three", "{n} earlier years since {year} with ten or more titles; the trend cards need three", good, { year: t.ws }) };
    return { ok: true };
  }

  // the headline is always a true and kind statement; a quieter year gets a neutral one
  function headline(t, ui) {
    const cv = t.now.val, maxComp = Math.max(...t.comp.map(w => w.val)), span = t.Y - t.ws + 1, firstBigger = t.firstVal >= cv;
    if (!firstBigger && t.all.length >= 4 && t.all.every(w => w.val < cv)) return { text: _("Your biggest year ever"), mark: null };
    if (cv > maxComp && !(firstBigger && t.firstYear >= t.ws)) return { text: _("Your biggest year in {n} years", { n: span }), mark: null };
    const bigger = t.comp.filter(w => w.val >= cv).map(w => w.yr).concat(firstBigger ? [t.firstYear] : []), last = Math.max(...bigger);
    if (t.Y - last >= 3) return { text: _("Your biggest year since {year}", { year: last }), mark: last };
    const shelf = Object.entries(t.now.cats).filter(([c, v]) => v >= 10 && t.comp.every(w => (w.cats[c] || 0) < v)).sort((a, b) => b[1] - a[1])[0];
    if (shelf) return { text: _("More {shelf} than in any year since {year}", { shelf: ui.CAT_NOUN[shelf[0]], year: t.ws }), mark: null };
    const prev = t.win.find(w => w.yr === t.Y - 1);
    if (prev && !prev.first && prev.val >= 10 && cv >= prev.val * 1.2) return { text: _("Up {pct}% on {year}", { pct: Math.round((cv / prev.val - 1) * 100), year: t.Y - 1 }), mark: t.Y - 1 };
    const avg = t.comp.reduce((a, w) => a + w.val, 0) / t.comp.length;
    if (cv >= avg * 1.15) return { text: _("{pct}% above your average", { pct: Math.round((cv / avg - 1) * 100) }), mark: null };
    if (cv >= avg * 0.85) return { text: _("A steady year"), mark: null };
    return { text: t.inProgress ? _n("{n} title so far", "{n} titles so far", cv) : _n("{n} title this year", "{n} titles this year", cv), mark: null };
  }

  function records(t, ui) {
    const out = [], best = (f, min) => { const vals = t.comp.map(w => ({ yr: w.yr, v: f(w) })).filter(x => x.v >= (min || 0)); if (vals.length < 3) return null; return vals.reduce((a, b) => b.v >= a.v ? b : a); };
    const add = (key, label, v, b, fmt) => { if (b && v > b.v) out.push({ key, label, v, prev: b, fmt: fmt || (x => x), margin: v / Math.max(b.v, 1) }); };
    add("titles", _("Most titles"), t.now.val, best(w => w.val));
    Object.entries(t.now.cats).forEach(([c, v]) => { if (v >= 10) add(c, _("Most {shelf}", { shelf: ui.CAT_NOUN[c] }), v, best(w => w.cats[c] || 0)); });
    if (t.now.five >= 3) add("five", _("Most five-star titles"), t.now.five, best(w => w.five));
    add("days", _("Most days finishing a title"), t.now.days, best(w => w.days));
    if (t.now.rated >= 20) add("rating", _("Highest average rating"), t.now.gsum / t.now.rated, best(w => w.rated >= 10 ? w.gsum / w.rated : 0, 0.1), x => `★ ${x.toFixed(1)}`);
    return [out[0] && out[0].key === "titles" ? out.shift() : null, ...out.sort((a, b) => b.margin - a.margin)].filter(Boolean).slice(0, 5);
  }

  function shift(t) {
    const tot = t.comp.reduce((a, w) => a + w.val, 0); if (!tot || !t.now.val) return null;
    const cats = new Set([...Object.keys(t.now.cats), ...t.comp.flatMap(w => Object.keys(w.cats))]);
    const rows = [...cats].map(c => { const before = t.comp.reduce((a, w) => a + (w.cats[c] || 0), 0); const a = before / tot * 100, b = (t.now.cats[c] || 0) / t.now.val * 100; return { c, a, b, d: b - a, big: (t.now.cats[c] || 0) >= 10 || before / t.comp.length >= 10 }; }).filter(r => r.big);
    return rows.sort((x, y) => Math.abs(y.d) - Math.abs(x.d))[0] || null;
  }

  // columns, one per year in the window; light tops are bulk days; in a year in progress the earlier years show
  // the same months solid and the rest of the year as a ghost
  function columns(t, ui, opt) {
    const n = t.win.length, x0 = 80, W = 920, top = opt.top, bottom = opt.bottom || 830 + ui.DY, slot = W / n, bw = Math.min(70, slot * 0.62);
    const max = Math.max(1, ...t.win.map(w => opt.share ? 1 : Math.max(w.val + w.bulk, t.inProgress ? w.full.val + w.full.bulk : 0)));
    const unit = (bottom - top) / max; let g = "";
    t.win.forEach((w, i) => {
      const x = x0 + i * slot + (slot - bw) / 2, isNow = w.yr === t.Y, op = isNow || !w.first ? 1 : 0.35; let y = bottom, segs = "";
      // a short bar for this year gives each shelf at least 12 px, so a small shelf does not disappear
      const short = !opt.share && isNow && (w.val + w.bulk) * unit < 240;
      if (!opt.share && t.inProgress && !isNow) { const fh = (w.full.val + w.full.bulk) * unit; if (fh > 0) g += ui.G("fade", 600 + i * 60, `<rect x="${x + 1}" y="${bottom - fh}" width="${bw - 2}" height="${fh - 1}" rx="6" fill="${ui.LINE}" stroke="${ui.MUTED}" stroke-width="2" stroke-dasharray="5 4"/>`); }
      const order = opt.share ? [opt.share.c, ...ui.CATS.filter(c => c !== opt.share.c)] : ui.CATS;
      if (!opt.share && !isNow) { const h = w.val * unit; if (h) { y -= h; segs += `<rect x="${x}" y="${y}" width="${bw}" height="${Math.max(h - 2, 1)}" rx="5" fill="${GREY}"/>`; } }
      else order.forEach(c => { const v = w.cats[c] || 0; if (!v) return; const h = opt.share ? v / w.val * (bottom - top) : short ? Math.max(v * unit, 12) : v * unit; y -= h; const dim = opt.share && c !== opt.share.c; segs += `<rect x="${x}" y="${y}" width="${bw}" height="${Math.max(h - 2, 1)}" rx="4" fill="${ui.CAT_COLOR[c]}"${dim ? ` opacity="0.3"` : ""}/>`; });
      if (!opt.share && w.bulk) { const h = w.bulk * unit; y -= h; segs += ui.hatchRect(x, y, bw, Math.max(h - 2, 1), 4, BULK_BG, BULK_INK); }
      g += ui.G("growy", 250 + i * 60, `<g opacity="${op}">${segs}</g>`);
      const lab = opt.share ? (opt.share.c in w.cats || isNow ? `${Math.round((w.cats[opt.share.c] || 0) / Math.max(w.val, 1) * 100)}%` : "") : (isNow || w.yr === opt.mark ? String(w.val + w.bulk) : "");
      if (lab && (isNow || w.yr === opt.mark || (opt.share && i === t.win.findIndex(v => !v.first && v.val)))) g += ui.T(x + bw / 2, (opt.share ? top : y) - 14, lab, 34, { anchor: "middle", weight: isNow ? 800 : 600, fill: isNow ? ui.INK : ui.MUTED, cls: "fade", d: 700 + i * 60 });
      g += ui.T(x + bw / 2, bottom + 44, n > 6 ? `’${String(w.yr).slice(2)}` : w.yr, 34, { anchor: "middle", fill: isNow ? ui.INK : ui.MUTED, weight: isNow ? 700 : 400 });
    });
    return g;
  }
  const GREY = "#c3ced6", BULK_BG = "#eef1f3", BULK_INK = "#aebbc3";

  function cards(t, ui) {
    const base = eligible(t), going = t && !t.none && t.inProgress, M = window.WR.MONTHS, ML = window.WR.MONTHS_LONG;
    // record: the stage gives a record card one sweep of light; this one when no compared year was as big
    const yoy = { id: "yoy", name: _("Year over year"), kind: "computed", interest: base, record: base.ok && t.comp.every(w => w.val < t.now.val), bg: () => ui.TINT.blue, body: () => {
      const h = headline(t, ui), avg = Math.round(t.comp.reduce((a, w) => a + w.val, 0) / t.comp.length), cb = t.now.bulk, v = t.now.val + cb;
      const line = (going ? _n("{n} title so far", "{n} titles so far", v) : _n("{n} title", "{n} titles", v)) + (cb ? _(", {n} of them on one bulk day", { n: cb }) : "") + _("; {n} a year on average since {year}", { n: avg, year: t.ws });
      const top = ui.cardTop(h.text, line, { eyebrow: _("Year over year") });
      const items = [{ fill: GREY, label: going ? _("earlier years, {start} to {end}", { start: M[0], end: M[t.upTo] }) : _("earlier years") }, { stripes: ui.CATS.filter(c => t.now.cats[c]).slice(0, 4).map(c => ui.CAT_COLOR[c]), label: _("{year} by shelf", { year: t.Y }) }, t.win.some(w => w.first) ? { fill: GREY, opacity: 0.35, label: _("first year, not compared") } : null, t.win.some(w => w.bulk) ? { fill: BULK_BG, hatch: BULK_INK, label: _("bulk days, left out") } : null, going ? { fill: ui.LINE, stroke: ui.MUTED, dash: true, label: _("the full year") } : null];
      const up = (ui.legendRows(items) - 1) * 50;
      return top.svg + columns(t, ui, { top: top.y + 90, bottom: 830 + ui.DY - up, mark: h.mark }) + ui.legend(items, 912 + ui.DY - up);
    } };
    const recs = base.ok ? records(t, ui) : [];
    const bests = { id: "bests", name: _("Personal bests"), kind: "computed", record: true, interest: base.ok ? (recs.length >= 3 ? { ok: true } : { ok: false, reason: recs.length ? _n("only {n} record broken, under three", "only {n} records broken, under three", recs.length) : _("no record broken against the years since {year}", { year: t.ws }) }) : base, bg: () => ui.TINT.green, body: () => {
      const n = recs.length, title = { 3: _("Three records"), 4: _("Four records"), 5: _("Five records") }[n];
      const top = ui.cardTop(title, going ? _("set this year, against {start} to {end} of every year since {year}", { start: ML[0], end: ML[t.upTo], year: t.ws }) : _("set this year, against every year since {year}", { year: t.ws }), { eyebrow: _("Personal bests"), size: n > 4 ? 96 : undefined });
      // three to five records in rows of two: the new figure, what it counts, the old best; the note on bulk days takes the empty cell, else goes under the rows
      const rows = Math.ceil(n / 2), y0 = top.y + 112, pitch = Math.min(220, (786 + ui.DY - y0) / (rows - 1)), note = _("Titles marked in bulk on one day are left out.");
      let b = top.svg;
      recs.forEach((r, i) => { const x = 80 + (i % 2) * 480, y = y0 + Math.floor(i / 2) * pitch, d = 300 + i * 120; b += ui.T(x, y, r.fmt(r.v), 60, { weight: 800, ls: -1.8, cls: "rise", d }) + ui.T(x, y + 44, r.label, 34, { weight: 700, cls: "rise", d: d + 40 }) + ui.T(x, y + 82, _("previous best {value} in {year}", { value: r.fmt(r.prev.v), year: r.prev.yr }), 34, { fill: ui.MUTED, cls: "fade", d: d + 100 }); });
      if (t.now.bulk) b += n % 2 ? ui.wrapW(note, 34, 400, 420, 3).map((l, j) => ui.T(560, y0 + (rows - 1) * pitch - 20 + j * 42, l, 34, { fill: ui.MUTED, cls: "fade", d: 900 })).join("") : ui.T(80, 912 + ui.DY, note, 34, { fill: ui.MUTED, cls: "fade", d: 900 });
      return b;
    } };
    const s = base.ok ? shift(t) : null, moved = s && Math.abs(s.d) >= 15;
    const taste = { id: "taste", name: _("How your taste moved"), kind: "computed", interest: base.ok ? (moved ? { ok: true } : { ok: false, reason: s ? _("no shelf moved by 15 points; the largest change is {shelf}, {n} points", { shelf: ui.CAT_LABEL[s.c], n: Math.round(Math.abs(s.d)) }) : _("too few titles to compare") }) : base, bg: () => ui.TINT.coral, body: () => {
      const top = ui.cardTop(_("{shelf} went from {a}% to {b}%", { shelf: ui.CAT_LABEL[s.c], a: Math.round(s.a), b: Math.round(s.b) }), going ? _("of your year so far, against your average since {year}", { year: t.ws }) : _("of your year, against your average since {year}", { year: t.ws }), { eyebrow: _("How your taste moved") });
      return top.svg + columns(t, ui, { top: top.y + 90, share: s }) + ui.legend([{ fill: ui.CAT_COLOR[s.c], label: _("{shelf}, share of each year", { shelf: ui.CAT_LABEL[s.c] }) }, { stripes: ui.CATS.filter(c => c !== s.c).slice(0, 4).map(c => ui.CAT_COLOR[c]), opacity: 0.3, label: _("other shelves") }], 912 + ui.DY);
    } };
    return { yoy, bests, taste };
  }
  window.TREND = { model, cards, introLine, eligible, BULK_DAY };
})();

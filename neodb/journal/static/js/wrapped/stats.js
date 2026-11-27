(function () {
  // the page renders the member's year as JSON; the cards are drawn from it here
  const D = JSON.parse(document.getElementById("wr-data").textContent);
  // text: the English is the key; the server sends the page language's text for each key (nothing for English)
  const LANG = D.lang || document.documentElement.lang || "en", TX = D.text || {};
  const fill = (s, v) => v ? s.replace(/\{(\w+)\}/g, (m, k) => (k in v ? v[k] : m)) : s;
  const _ = (s, v) => fill(TX[s] || s, v);
  // a pair of forms, chosen by n; the table holds [one, other] under the singular, and {n} is filled with n unless v sets it
  const _n = (s, p, n, v) => { const f = TX[s]; return fill(Array.isArray(f) ? f[n === 1 ? 0 : 1] : n === 1 ? s : p, Object.assign({ n }, v)); };
  const fmt = o => { try { return new Intl.DateTimeFormat(LANG, o); } catch (e) { return new Intl.DateTimeFormat("en", o); } };
  if (!D.items.length) { window.WR = { D, empty: true, _, _n }; return; }
  const CATS = ["book", "movie", "tv", "music", "game", "podcast", "performance"];
  const CAT_LABEL = { book: _("Books"), movie: _("Movies"), tv: _("TV"), music: _("Music"), game: _("Games"), podcast: _("Podcasts"), performance: _("Performances") };
  // the shelf as a word inside a sentence
  const CAT_NOUN = { book: _("books"), movie: _("movies"), tv: _("TV"), music: _("music"), game: _("games"), podcast: _("podcasts"), performance: _("performances") };
  const CAT_ICON = { book: "fa-book", movie: "fa-film", tv: "fa-tv", music: "fa-compact-disc", game: "fa-gamepad", podcast: "fa-podcast", performance: "fa-masks-theater" };
  const CAT_COLOR = { book: "#B4D2A5", movie: "#7CBDFE", tv: "#FED37C", music: "#FEA36D", game: "#C5A290", podcast: "#9D6AB0", performance: "#FE7C7C" };
  // month names in the page language
  const monthNames = o => Array.from({ length: 12 }, (x, m) => fmt({ month: o }).format(new Date(2001, m, 15)));
  const MONTHS = monthNames("short"), MONTHS_LONG = monthNames("long"), MONTHS_NARROW = monthNames("narrow");
  const ROLE_LABEL = { director: _("director"), author: _("author"), artist: _("artist"), host: _("host"), developer: _("developer"), designer: _("designer"), actor: _("actor"), person: "" };
  const CREATOR_ROLES = ["director", "author", "artist", "host", "developer", "designer", "person"];

  const site = (D.member.handle.split("@")[2] || "neodb.social");
  const items = D.items.slice().sort((a, b) => a.date.localeCompare(b.date));
  items.forEach(it => { it.credits = it.credits || []; });
  const rated = items.filter(i => i.rating);
  const byCat = {}; CATS.forEach(c => byCat[c] = items.filter(i => i.category === c));
  const cats = CATS.filter(c => byCat[c].length);
  const byMonth = MONTHS.map((_, m) => items.filter(i => +i.date.slice(5, 7) === m + 1));
  // a past year is complete; the current year runs to this month, whatever month the last title was in
  const now = new Date(), lastMonth = D.year < now.getFullYear() ? 11 : Math.max(now.getMonth(), ...items.map(i => +i.date.slice(5, 7) - 1));
  const fiveStar = items.filter(i => i.rating === 10);
  const avg = rated.length ? rated.reduce((s, i) => s + i.rating, 0) / rated.length : null;
  const topCat = cats.slice().sort((a, b) => byCat[b].length - byCat[a].length)[0];
  // the "top shelf": five stars when there are any, else the highest own marks, else the best by the site's average
  let top, topLabel;
  if (fiveStar.length >= 3) { top = fiveStar; topLabel = _("Everything that got five stars this year"); }
  else if (rated.length >= 3) { top = rated.slice().sort((a, b) => b.rating - a.rating || (b.site_rating || 0) - (a.site_rating || 0)).slice(0, 8); topLabel = _("Your best-rated titles of the year"); if (top.length < 8) { items.filter(i => i.site_rating && !top.includes(i)).sort((a, b) => b.site_rating - a.site_rating).slice(0, 8 - top.length).forEach(i => top.push(i)); topLabel = _("Your highest marks, then the titles others rate highest"); } }
  else { top = items.filter(i => i.site_rating).sort((a, b) => b.site_rating - a.site_rating).slice(0, 8); topLabel = _("No stars given this year, so here are the titles others rate highest"); }
  const bestOf = {}; cats.forEach(c => { bestOf[c] = byCat[c].slice().sort((a, b) => (b.rating || 0) - (a.rating || 0) || (b.site_rating || 0) - (a.site_rating || 0))[0]; });
  // the quiet favourite: a top mark that few others gave the title
  const pool9 = fiveStar.length ? fiveStar : rated.filter(i => i.rating >= 9);
  const quiet = pool9.filter(i => i.marks != null).sort((a, b) => a.marks - b.marks)[0] || null;
  const dated = items.filter(i => i.year && i.year > 1800 && i.year <= D.year + 1);
  const oldest = dated.slice().sort((a, b) => a.year - b.year)[0] || null;
  const medianYear = dated.length ? dated.map(i => i.year).sort((a, b) => a - b)[Math.floor(dated.length / 2)] : null;
  const decades = {}; dated.forEach(i => { const d = Math.floor(i.year / 10) * 10; decades[d] = (decades[d] || 0) + 1; });
  const decadeList = Object.keys(decades).map(Number).sort((a, b) => a - b).map(d => ({ decade: d, n: decades[d] }));
  // people
  const people = {};
  items.forEach(i => i.credits.forEach(([role, name]) => { if (!name) return; const key = name.toLowerCase(); if (!people[key]) people[key] = { name, roles: new Set(), items: [] }; people[key].roles.add(role); if (!people[key].items.includes(i)) people[key].items.push(i); }));
  const peopleList = Object.values(people).map(p => ({ name: p.name, roles: [...p.roles], items: p.items, n: p.items.length, creator: [...p.roles].some(r => CREATOR_ROLES.includes(r)) }));
  const creators = peopleList.filter(p => p.creator && p.n >= 2).sort((a, b) => b.n - a.n || b.items[0].rating - a.items[0].rating).slice(0, 6);
  const actors = peopleList.filter(p => !p.creator && p.n >= 2).sort((a, b) => b.n - a.n).slice(0, 6);
  // the essay card falls back to the longest comment
  const essay = items.filter(i => i.comment && i.comment.length > 2).sort((a, b) => b.comment.length - a.comment.length)[0] || null;
  const stats = { items, rated, byCat, cats, byMonth, lastMonth, fiveStar, avg, topCat, top, topLabel, bestOf, quiet, oldest, medianYear, decadeList, creators, actors, essay, site };

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
  function cover(it) {
    const sq = (it.category === "music" || it.category === "podcast") ? " sq" : "";
    return `<a class="dc-cover${sq}" href="${esc(it.url)}" title="${esc(it.title)}"><img src="${esc(it.cover)}" alt="${esc(it.title)}" loading="lazy"></a>`;
  }
  const dayFmt = fmt({ month: "short", day: "numeric" });
  function fmtDate(iso) { return dayFmt.format(new Date(iso + "T12:00:00")); }
  function who(it) { const c = it.credits.find(([r]) => CREATOR_ROLES.includes(r)) || it.credits[0]; return c ? c[1] : ""; }
  function trunc(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  // colours for the cards are fixed because a card is an image
  const SVG = { font: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', 'Noto Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif", ink: "#1b2832", muted: "#5c6f7b", primary: "#0172ad", line: "#dde3e8" };
  window.WR = { D, _, _n, CATS, CAT_LABEL, CAT_NOUN, CAT_ICON, CAT_COLOR, MONTHS, MONTHS_LONG, MONTHS_NARROW, ROLE_LABEL, stats, esc, cover, fmtDate, who, trunc, SVG };
})();

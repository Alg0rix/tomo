/* plugins.js — /extensions: installed rooms, catalog browse, updates, build, catalogs.
   Renders from #pl-data, then refetches /api after every change. */
(function () {
  "use strict";
  var root = document.getElementById("pl");
  if (!root) return;
  var D = JSON.parse(document.getElementById("pl-data").textContent);
  var panel = document.getElementById("pl-panel");
  var modalBox = document.getElementById("pl-modal");
  var ADMIN = !!D.admin;
  var TABS = ["installed", "browse", "updates", "build", "catalogs"];
  // Known catalog categories get a kanji; anything else falls back to 部.
  var NEED_KANJI = {money: "金", finance: "金", developer: "稼", monitoring: "稼", home: "家", productivity: "務", work: "務", life: "日", media: "読", reading: "読", learning: "学", agents: "友", health: "健", travel: "旅", food: "食"};
  var CATALOG_KANJI = {"tomo-official": "友", "tomo-community": "衆"};
  var BUILD_CAPS = [["page", "Sidebar page"], ["card", "Home card"], ["tool", "Agent tools"], ["skill", "Skill for agents"], ["starter", "Chat starter"], ["after", "Runs after each turn"]];
  var CAP_PROMPT = {page: "one or more pages with sidebar navigation", card: "a Home card via api.home_card() with a kanji", tool: "domain tools for Tomo's existing agents", skill: "a skills/<name>/SKILL.md package explaining how agents use it", starter: "a chat starter via api.starter()", after: "an api.on_turn_end() hook"};
  var EXAMPLES = [
    ["金", "ok", "Track spending", "Log purchases from chat and show this month's spending on Home."],
    ["習", "ok", "Habit check-ins", "Daily habits with streaks, nudges and a small Home card."],
    ["稼", "info", "Server uptime", "Ping my workplaces and show uptime on Home."],
    ["読", "earth", "Reading list", "Save articles from chat, track reading progress and search my notes."]
  ];

  var S = {
    tab: "installed", q: "", need: "", src: {}, sort: "", layout: "cards", sel: null, pop: null,
    confirm: false, busy: {}, ghost: null, shown: {}, caps: {page: 1, card: 1, tool: 1}, idea: "",
    ideas: null, ideasBusy: false, ideasNote: "", updates: null, checking: false, checked: false, fresh: null
  };

  // ---------- helpers ----------
  function esc(s) { return Tomo.escapeHtml(s); }
  function $(sel) { return document.querySelector(sel); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }
  function installed() { return D.plugins; }
  function byId(id) { return D.plugins.find(function (p) { return p.id === id; }); }
  function entryKey(e) { return e.id + "@" + e.marketplace; }
  function byKey(key) { return D.catalog.find(function (e) { return entryKey(e) === key; }); }
  function selected() {
    if (!S.sel) return null;
    return S.sel.charAt(0) === "i" ? byId(S.sel.slice(2)) : byKey(S.sel.slice(2));
  }
  function deps(p) { return p.dependencies || {}; }
  function broken(p) { return !!p.error || deps(p).status === "missing" || deps(p).status === "invalid"; }
  function issueText(p) {
    var d = deps(p);
    if (d.status === "missing") return "Missing Python packages: " + (d.missing || []).join(", ");
    if (d.status === "invalid") return d.error || "Its package list can't be read.";
    return p.error || "";
  }
  function upd(p) {
    var u = (S.updates && S.updates[p.id]) || p.update || {};
    return u.status === "available" ? u : null;
  }
  function upLabel(u) { return u.latest_version ? "v" + u.latest_version : "New revision"; }
  function kind(p) {
    if (!p.running) return "off";
    if ((p.pages || []).length) return "room";
    if ((p.tools || []).length) return "tools";
    return "background";
  }
  function who(p) {
    if (p.author) return p.author;
    if (p.publisher) return p.publisher;
    if (p.source === "path") return "Your folder";
    var owner = p.origin && /github\.com[/:]([^/]+)/.exec(p.origin.url || "");
    if (owner) return owner[1];
    return p.source === "git" ? "Git source" : "Tomo team";
  }
  function fromName(p) {
    if (p.publisher) return p.publisher;
    var m = D.marketplaces.find(function (x) { return x.id === p.marketplace; });
    return m ? m.name : p.source === "path" ? "Local folder" : p.source === "git" ? "Git source" : "Bundled with Tomo";
  }
  function tile(p, cls) {
    var inner = p.kanji ? '<span lang="ja">' + esc(p.kanji) + "</span>" : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (D.icons[p.icon] || D.icons.puzzle) + "</svg>";
    return '<span class="pl-kt t-' + esc(p.tint || "warm") + (cls ? " " + cls : "") + '" aria-hidden="true">' + inner + "</span>";
  }
  function groupOf(e) {
    if (e.category) return {key: "c:" + e.category.toLowerCase(), label: e.category, kanji: NEED_KANJI[e.category.toLowerCase()] || "部", tint: e.tint};
    return {key: "m:" + e.marketplace, label: e.publisher, kanji: CATALOG_KANJI[e.marketplace] || "集", tint: "warm"};
  }
  function matchQ(p) {
    var q = S.q.trim().toLowerCase();
    return !q || [p.name, p.description, p.id, p.author, p.publisher, p.category].join(" ").toLowerCase().indexOf(q) !== -1;
  }
  function isSource(q) { return /^(https?:\/\/|git@|\/|~\/)/.test(q.trim()); }
  function toggle(p, label) {
    if (!ADMIN) return "";
    var dis = (!p.running && broken(p)) || S.busy[p.id];
    return '<button class="pl-tg" role="switch" aria-checked="' + !!p.running + '" aria-label="' + (p.running ? "Turn off " : "Turn on ") + esc(label || p.name) + '" data-tg="' + esc(p.id) + '"' + (dis ? " disabled" : "") + "></button>";
  }
  function addsRow(p) {
    var bits = [];
    var pages = (p.pages || []).length, tools = (p.tools || []).length;
    if (pages) bits.push(ICO.page + plural(pages, "page"));
    if (p.home_cards) bits.push(ICO.card + (p.home_cards === 1 ? "Home card" : p.home_cards + " Home cards"));
    if (tools) bits.push(ICO.tool + plural(tools, "tool"));
    if ((p.skills || []).length) bits.push(ICO.skill + plural(p.skills.length, "skill"));
    if (!bits.length) bits.push(ICO.bg + "Runs in the background");
    return bits.map(function (b) { return "<span>" + b + "</span>"; }).join("");
  }
  var ICO = {
    page: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="2" y="2.5" width="12" height="11" rx="2"/><path d="M6 2.5v11"/></svg>',
    card: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="2" y="3" width="12" height="10" rx="2"/><path d="M4.5 10l2.5-2.5 2 1.5 2.5-3"/></svg>',
    tool: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M10.5 2.5a3 3 0 00-3.2 4L2.8 11a1.2 1.2 0 001.7 1.7L9 8.3a3 3 0 004-3.2l-1.8 1.8-1.7-.3-.3-1.7z"/></svg>',
    skill: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M3 3h7l3 3v7H3z"/><path d="M5.5 8h5M5.5 10.5h3"/></svg>',
    bg: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><circle cx="8" cy="8" r="5.5"/><path d="M8 5v3l2 1.5"/></svg>'
  };

  // ---------- pieces ----------
  function header() {
    var u = installed().filter(upd).length;
    var tabs = [["installed", "Installed", installed().length], ["browse", "Browse", D.catalog.length], ["updates", "Updates", u, true], ["build", "Build"], ["catalogs", "Catalogs", D.marketplaces.length]];
    if (!ADMIN) tabs = tabs.filter(function (t) { return t[0] !== "build"; });
    var addMenu = !ADMIN ? "" : '<div class="pl-dd"><button class="btn primary sm" data-pop="add" aria-expanded="' + (S.pop === "add") + '" aria-haspopup="menu">＋ Add plugin</button>' + (S.pop === "add" ? '<div class="pl-pop" role="menu"><button role="menuitem" data-tab="browse"><span>Pick from a catalog<small>' + plural(D.catalog.length, "plugin") + " in " + plural(D.marketplaces.length, "catalog") + '</small></span></button><button role="menuitem" data-tab="build"><span>Build one with an agent<small>Describe it. An agent writes and installs it.</small></span></button><button role="menuitem" data-modal="source"><span>Install from source<small>GitHub repository or a folder on this server</small></span></button><hr><button role="menuitem" data-modal="catalog"><span>Add a catalog</span></button></div>' : "") + "</div>";
    return '<div class="pl-hd"><h1 class="page-title"><span class="pl-kt t-warm pl-h-kt" aria-hidden="true"><span lang="ja">部</span></span>Plugins</h1>' +
      (S.tab === "installed" || S.tab === "browse" ? '<label class="pl-search"><span class="sr-only">Search plugins</span><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/></svg><input id="pl-q" type="search" autocomplete="off" value="' + esc(S.q) + '" placeholder="' + (S.tab === "installed" ? "Find in your " + plural(installed().length, "plugin") : "Search " + plural(D.catalog.length, "plugin") + ", or paste a GitHub URL") + '"><kbd>/</kbd></label>' : '<span></span>') +
      addMenu + "</div>" +
      '<div class="pl-tabs" role="tablist" aria-label="Plugin views">' + tabs.map(function (t) {
        var n = t[2] === undefined || (t[3] && !t[2]) ? "" : ' <span class="n' + (t[3] ? " hot" : "") + '">' + t[2] + "</span>";
        return '<button class="pl-tab" role="tab" id="pl-tab-' + t[0] + '" aria-selected="' + (S.tab === t[0]) + '" tabindex="' + (S.tab === t[0] ? 0 : -1) + '" data-tab="' + t[0] + '">' + t[1] + n + "</button>";
      }).join("") + '<span class="pl-sp"></span><a class="pl-lnk" href="/extensions/guide">How plugins work</a></div>';
  }
  function strip() {
    var rooms = installed().filter(function (p) { return (p.pages || []).length || !p.running; });
    var g = S.ghost && byKey(S.ghost);
    var lab;
    if (S.tab === "browse") lab = g ? "<b>" + esc(g.name) + " would join here</b>Once installed and switched on" : "<b>Your rooms</b>Point at a plugin to see where it lands";
    else lab = "<b>" + plural(installed().filter(function (p) { return p.running && (p.pages || []).length; }).length, "room") + " in your sidebar</b>" + (rooms.some(function (p) { return !p.running; }) ? "Dimmed ones are switched off" : "In sidebar order");
    var stamps = rooms.map(function (p) {
      var dot = broken(p) ? ' <i class="bad"></i>' : upd(p) ? ' <i class="up"></i>' : "";
      return '<button class="pl-stamp" data-open="i:' + esc(p.id) + '" aria-current="' + (S.sel === "i:" + p.id) + '" title="' + esc(p.name) + (p.running ? "" : " (off)") + '">' + tile(p, p.running ? "" : "is-off") + dot + '<span class="sr-only">' + esc(p.name) + "</span></button>";
    }).join("");
    if (S.tab === "browse") stamps += g ? '<span class="pl-stamp is-ghost">' + tile(g) + "</span>" : '<span class="pl-stamp is-slot" aria-hidden="true"><span class="pl-kt">＋</span></span>';
    if (!stamps) stamps = '<span class="pl-faint">No rooms yet. Install a plugin with pages and it shows up here and in your sidebar.</span>';
    var legend = S.tab === "installed" ? '<div class="pl-legend" aria-hidden="true"><span><i class="up"></i>Update</span><span><i class="bad"></i>Can\'t start</span></div>' : "";
    return '<div class="pl-strip"><div class="pl-strip-lab">' + lab + '</div><div class="pl-stamps">' + stamps + "</div>" + legend + "</div>";
  }
  function plug(p, mine) {
    var key = mine ? "i:" + p.id : "c:" + entryKey(p);
    var right = "", body, foot;
    if (mine) {
      var u = upd(p);
      right = (broken(p) ? '<span class="pl-pill bad">Can\'t start</span>' : u ? '<span class="pl-pill up">' + esc(upLabel(u)) + "</span>" : "") + toggle(p);
      body = "<p>" + esc(p.description) + "</p>";
      foot = broken(p) ? '<span class="bad">' + esc(issueText(p)) + "</span>" : !p.running ? "<span>Off. Its pages and tools are hidden.</span>" : addsRow(p);
    } else {
      var have = byId(p.id);
      right = have ? '<span class="pl-pill mine">Yours</span>' : S.busy["c:" + entryKey(p)] ? '<span class="pl-faint">Installing…</span>' : ADMIN ? '<button class="pl-get" data-install="' + esc(entryKey(p)) + '" aria-label="Install ' + esc(p.name) + '">Install</button>' : "";
      body = "<p>" + esc(p.description) + "</p>";
      foot = "<span>" + esc(p.author) + "</span><span class=\"mono\">v" + esc(p.version) + "</span>";
    }
    return '<article class="pl-plug" data-open="' + esc(key) + '"' + (mine ? "" : ' data-hover="' + esc(entryKey(p)) + '"') + ' aria-current="' + (S.sel === key) + '">' +
      '<span class="pl-blk t-' + esc(p.tint || "warm") + (mine && !p.running ? " is-off" : "") + '" aria-hidden="true">' + (p.kanji ? '<span lang="ja">' + esc(p.kanji) + "</span>" : tile(p, "bare")) + "</span>" +
      '<div class="pl-bd"><div class="pl-t"><h3><button class="pl-name" data-open="' + esc(key) + '">' + esc(p.name) + '</button></h3><span class="pl-r">' + right + "</span></div>" + body + '<div class="pl-adds">' + foot + "</div></div></article>";
  }
  function row(p) {
    var u = upd(p);
    var st = broken(p) ? '<span class="bad">Can\'t start</span>' : u ? '<span class="up">' + esc(upLabel(u)) + " ready</span>" : p.running ? "On" : '<span class="pl-faint">Off</span>';
    return '<div class="pl-row" data-open="i:' + esc(p.id) + '" aria-current="' + (S.sel === "i:" + p.id) + '">' + tile(p, p.running ? "" : "is-off") + '<button class="pl-name" data-open="i:' + esc(p.id) + '">' + esc(p.name) + '</button><span class="pl-ds">' + esc(p.description) + '</span><span class="pl-f pl-c-src">' + esc(who(p)) + (p.version ? " · v" + esc(p.version) : "") + '</span><span class="pl-f">' + st + "</span>" + toggle(p) + "</div>";
  }
  function dd(name, label, items) {
    return '<div class="pl-dd"><button class="btn ghost sm" data-pop="' + name + '" aria-expanded="' + (S.pop === name) + '">' + label + ' <span aria-hidden="true">▾</span></button>' + (S.pop === name ? '<div class="pl-pop">' + items + "</div>" : "") + "</div>";
  }
  function sourceFilter(pool) {
    var n = Object.keys(S.src).length;
    var opts = D.marketplaces.map(function (m) {
      return '<label><input type="checkbox" data-src="' + esc(m.id) + '"' + (S.src[m.id] ? " checked" : "") + ">" + esc(m.name) + "<small>" + pool.filter(function (p) { return p.marketplace === m.id; }).length + "</small></label>";
    });
    if (S.tab === "installed") opts.push('<label><input type="checkbox" data-src="_other"' + (S.src._other ? " checked" : "") + ">Local and Git<small>" + pool.filter(function (p) { return !p.marketplace; }).length + "</small></label>");
    return dd("src", "Source" + (n ? ' <span class="pl-cnt">' + n + "</span>" : ""), opts.join(""));
  }
  function srcOk(p) {
    if (!Object.keys(S.src).length) return true;
    return p.marketplace ? !!S.src[p.marketplace] : !!S.src._other;
  }
  function sortFilter(opts) {
    var cur = opts.find(function (o) { return o[0] === S.sort; }) || opts[0];
    return dd("sort", esc(cur[1]), opts.map(function (o) { return '<button data-sort="' + o[0] + '">' + o[1] + (o[0] === cur[0] ? "<small>✓</small>" : "") + "</button>"; }).join(""));
  }
  function order(list, def) {
    var s = S.sort || def;
    var f = {name: function (a, b) { return a.name.localeCompare(b.name); }, author: function (a, b) { return who(a).localeCompare(who(b)) || a.name.localeCompare(b.name); }}[s];
    return f ? list.slice().sort(f) : list;
  }
  function needBar(pool, groups) {
    if (groups.length < 2) return "";
    return '<div class="pl-needs" role="group" aria-label="Filter by group"><button class="pl-need" data-need="" aria-pressed="' + !S.need + '">All <small>' + pool.length + "</small></button>" + groups.map(function (g) {
      return '<button class="pl-need" data-need="' + esc(g.key) + '" aria-pressed="' + (S.need === g.key) + '"><span class="pl-kt t-' + esc(g.tint) + '" aria-hidden="true"><span lang="ja">' + esc(g.kanji) + "</span></span>" + esc(g.label) + " <small>" + g.items.length + "</small></button>";
    }).join("") + "</div>";
  }
  function empty(title, text, actions) { return '<div class="pl-empty"><b>' + title + "</b>" + text + (actions ? '<div class="pl-empty-a">' + actions + "</div>" : "") + "</div>"; }

  // ---------- views ----------
  function viewInstalled() {
    var all = installed();
    var attn = all.filter(function (p) { return broken(p) || upd(p); });
    var h = strip();
    if (attn.length) {
      var ups = attn.filter(upd);
      h += '<section class="pl-lane" aria-label="Needs you"><div class="pl-lh">Needs you<span class="pl-sp"></span>' + (ADMIN && ups.length > 1 ? '<button class="btn primary sm" data-updall' + (S.busy._all ? " disabled" : "") + ">" + (S.busy._all ? "Updating…" : "Update all " + ups.length) + "</button>" : "") + "</div>" + attn.map(function (p) {
        var u = upd(p), bad = broken(p);
        var act = !ADMIN ? "" : bad ? (deps(p).status === "missing" ? '<button class="btn sm" data-act="sync-dependencies" data-id="' + esc(p.id) + '">Install packages</button>' : '<button class="btn sm" data-open="i:' + esc(p.id) + '">See why</button>') : '<button class="btn sm" data-act="update" data-id="' + esc(p.id) + '">Update</button>';
        return '<div class="pl-lr ' + (bad ? "bad" : "up") + '" data-open="i:' + esc(p.id) + '">' + tile(p) + '<span><button class="pl-name" data-open="i:' + esc(p.id) + '">' + esc(p.name) + '</button><span class="pl-why">' + esc(bad ? issueText(p) : upLabel(u) + " is ready.") + "</span></span>" + act + "</div>";
      }).join("") + "</section>";
    }
    if (!all.length) return h + empty("No plugins yet", "Plugins add rooms to your sidebar, cards to Home and tools your agents can use.", '<button class="btn primary sm" data-tab="browse">Browse ' + plural(D.catalog.length, "plugin") + "</button>" + (ADMIN ? '<button class="btn sm" data-tab="build">Build one with an agent</button>' : ""));
    var list = order(all.filter(function (p) { return matchQ(p) && srcOk(p); }), "sidebar");
    h += '<div class="pl-bar"><span class="pl-grow pl-faint">' + plural(all.filter(function (p) { return p.running; }).length, "plugin") + " on, " + all.filter(function (p) { return !p.running; }).length + " off</span>" + sourceFilter(all) + sortFilter([["sidebar", "Sidebar order"], ["name", "Name"], ["author", "Author"]]) +
      '<div class="pl-seg" role="group" aria-label="Layout"><button data-layout="cards" aria-pressed="' + (S.layout === "cards") + '">Cards</button><button data-layout="list" aria-pressed="' + (S.layout === "list") + '">List</button></div></div>';
    if (!list.length) return h + empty("None of your plugins match", "Search every catalog in Browse instead.", '<button class="btn sm" data-clear>Clear filters</button><button class="btn sm" data-tab="browse" data-keepq>Search Browse</button>');
    var groups = [["room", "間", "warm", "Rooms", "Pages in your sidebar"], ["tools", "具", "info", "Agent tools", "No page. Agents use them in chat and routines."], ["background", "裏", "earth", "Background", "Runs quietly. Nothing to open."], ["off", "休", "rose", "Switched off", "Pages and tools hidden. Data kept."]];
    groups.forEach(function (g) {
      var items = list.filter(function (p) { return kind(p) === g[0]; });
      if (!items.length) return;
      h += '<section class="pl-grp"><div class="pl-grp-h"><h2><span class="pl-kt t-' + g[2] + '" aria-hidden="true"><span lang="ja">' + g[1] + "</span></span>" + g[3] + '</h2><span class="pl-faint">' + items.length + " · " + g[4] + "</span></div>" +
        (S.layout === "list" ? '<div class="pl-tbl">' + items.map(row).join("") + "</div>" : '<div class="pl-plugs">' + items.map(function (p) { return plug(p, true); }).join("") + "</div>") + "</section>";
    });
    return h;
  }
  function viewBrowse() {
    var h = strip();
    if (isSource(S.q)) return h + empty("Install from " + esc(S.q) + "?", "It isn't in your catalogs. Tomo downloads it without running any of its code. You switch it on after.", ADMIN ? '<button class="btn primary sm" data-modal="source">Review this source</button>' : "");
    if (!D.catalog.length) return h + empty("Your catalogs are empty", "Refresh them to load official and community plugins.", ADMIN ? '<button class="btn primary sm" data-refresh-all>Refresh catalogs</button><button class="btn sm" data-modal="source">Install from source</button>' : "");
    var pool = D.catalog.filter(function (e) { return matchQ(e) && srcOk(e); });
    var map = {}, groups = [];
    pool.forEach(function (e) { var g = groupOf(e); if (!map[g.key]) { map[g.key] = g; g.items = []; groups.push(g); } map[g.key].items.push(e); });
    groups.sort(function (a, b) { return b.items.length - a.items.length || a.label.localeCompare(b.label); });
    h += '<div class="pl-bar">' + needBar(pool, groups) + '<span class="pl-grow"></span>' + sourceFilter(D.catalog) + sortFilter([["catalog", "Catalog order"], ["name", "Name"], ["author", "Author"]]) + "</div>";
    if (!pool.length) return h + empty("Nothing matches “" + esc(S.q) + "”", "Have an agent build it instead.", ADMIN ? '<button class="btn primary sm" data-tab="build" data-seed="' + esc(S.q) + '">Build “' + esc(S.q) + "”</button>" : "");
    var single = S.need || S.q || groups.length === 1;
    groups.filter(function (g) { return !S.need || g.key === S.need; }).forEach(function (g) {
      var items = order(g.items, "catalog");
      var lim = S.shown[g.key] || (single ? 24 : 4);
      var mine = items.filter(function (e) { return byId(e.id); }).length;
      h += '<section class="pl-grp"><div class="pl-grp-h"><h2><span class="pl-kt t-' + esc(g.tint) + '" aria-hidden="true"><span lang="ja">' + esc(g.kanji) + "</span></span>" + esc(g.label) + '</h2><span class="pl-faint">' + plural(items.length, "plugin") + (mine ? ", " + mine + " yours" : "") + '</span></div><div class="pl-plugs">' + items.slice(0, lim).map(function (e) { return plug(e, false); }).join("") + "</div>" +
        (items.length > lim ? '<button class="btn ghost sm pl-more" data-more="' + esc(g.key) + '">Show ' + Math.min(single ? 24 : items.length - lim, items.length - lim) + " more in " + esc(g.label) + "</button>" : "") + "</section>";
    });
    return h;
  }
  function viewUpdates() {
    var git = installed().filter(function (p) { return p.source === "git"; });
    var u = installed().filter(upd);
    var h = '<div class="pl-intro"><p>Updating reloads a plugin in place. No restart, and its data stays.</p><span class="pl-intro-a">' + (ADMIN ? '<button class="btn sm" data-check' + (S.checking ? " disabled" : "") + ">" + (S.checking ? "Checking…" : "Check again") + "</button>" : "") + (ADMIN && u.length > 1 ? '<button class="btn primary sm" data-updall' + (S.busy._all ? " disabled" : "") + ">" + (S.busy._all ? "Updating…" : "Update all " + u.length) + "</button>" : "") + "</span></div>";
    if (S.checking && !S.checked) return h + empty("Checking " + plural(git.length, "source") + "…", "Asking each Git source for its latest commit.");
    var failed = git.filter(function (p) { var x = S.updates && S.updates[p.id]; return x && x.status === "error"; });
    if (!u.length) h += empty("Everything is up to date", git.length ? plural(git.length, "plugin") + " installed from Git. Local folders update when you reload them." : "No plugins come from Git, so there is nothing to check. Local folders update when you reload them.");
    else h += '<div class="pl-plugs pl-gap">' + u.map(function (p) {
      var x = upd(p);
      return '<article class="pl-plug" data-open="i:' + esc(p.id) + '" aria-current="' + (S.sel === "i:" + p.id) + '"><span class="pl-blk t-' + esc(p.tint) + '" aria-hidden="true">' + (p.kanji ? '<span lang="ja">' + esc(p.kanji) + "</span>" : tile(p, "bare")) + '</span><div class="pl-bd"><div class="pl-t"><h3><button class="pl-name" data-open="i:' + esc(p.id) + '">' + esc(p.name) + '</button></h3><span class="pl-r"><span class="mono pl-faint">v' + esc(p.version || "?") + " → " + esc(upLabel(x)) + "</span>" + (ADMIN ? '<button class="pl-get" data-act="update" data-id="' + esc(p.id) + '"' + (S.busy[p.id] ? " disabled" : "") + ">" + (S.busy[p.id] ? "Updating…" : "Update") + "</button>" : "") + "</span></div><p>" + esc(p.description) + '</p><div class="pl-adds"><span>' + esc(who(p)) + '</span><span class="mono">' + esc((x.installed_commit || p.commit || "").slice(0, 7)) + " → " + esc((x.latest_commit || "").slice(0, 7)) + "</span></div></div></article>";
    }).join("") + "</div>";
    if (failed.length) h += '<p class="pl-note-line bad">Couldn\'t check ' + failed.map(function (p) { return esc(p.name) + " (" + esc(S.updates[p.id].message || "request failed") + ")"; }).join(", ") + ". Try again later.</p>";
    return h;
  }
  function viewBuild() {
    var want = function (k) { return !!S.caps[k]; };
    var ideas = S.ideas || EXAMPLES.map(function (x) { return {kanji: x[0], tint: x[1], label: x[2], prompt: x[3]}; });
    var noAgents = !D.agents.length;
    return '<div class="pl-build"><form id="plugin-build" class="pl-build-form">' +
      '<h2><label for="plugin-idea">Describe the plugin</label></h2><p class="pl-sub">An agent writes it with the plugin SDK, tests it and installs it. You switch it on after reviewing.</p>' +
      '<textarea class="pl-ta" id="plugin-idea" name="idea" required maxlength="8000" placeholder="A reading room: save articles from chat, track how far I got, and show what I\'m halfway through on Home.">' + esc(S.idea) + "</textarea>" +
      '<p class="pl-capq" id="pl-caps-l">It should add</p><div class="pl-caps" role="group" aria-labelledby="pl-caps-l">' + BUILD_CAPS.map(function (c) { return '<button type="button" class="pl-cap" data-cap="' + c[0] + '" aria-pressed="' + want(c[0]) + '">' + c[1] + "</button>"; }).join("") + "</div>" +
      '<div class="pl-capq pl-ideas-h"><span id="pl-ideas-l">Or start from</span><button type="button" class="pl-lnk" id="plugin-ideas-refresh"' + (S.ideasBusy ? " disabled aria-busy=\"true\"" : "") + ">" + (S.ideasBusy ? "Finding ideas…" : "More ideas") + "</button></div>" +
      '<div class="pl-ideas" id="plugin-ideas" role="group" aria-labelledby="pl-ideas-l">' + ideas.map(function (i) { return '<button type="button" class="pl-idea" data-example="' + esc(i.prompt) + '" title="' + esc(i.prompt) + '">' + (i.kanji ? '<span class="pl-kt t-' + esc(i.tint) + '" aria-hidden="true"><span lang="ja">' + esc(i.kanji) + "</span></span>" : "") + esc(i.label) + "</button>"; }).join("") + "</div>" +
      (S.ideasNote ? '<p class="pl-faint pl-small" role="status">' + esc(S.ideasNote) + "</p>" : "") +
      '<div class="pl-brow"><label>Agent <select class="input" id="plugin-agent" name="agent"' + (noAgents ? " disabled" : "") + ">" + D.agents.map(function (a) { return '<option value="' + esc(a.id) + '">' + esc(a.name) + "</option>"; }).join("") + '</select></label><label>Build in <select class="input" id="plugin-workplace" name="workplace"><option value="">Agent work folder</option>' + D.workplaces.map(function (w) { return '<option value="' + esc(w.id) + '">' + esc(w.name) + "</option>"; }).join("") + '</select></label><span class="pl-sp"></span><button class="btn primary" type="submit"' + (noAgents ? " disabled" : "") + ">Build with agent</button></div>" +
      (noAgents ? '<p class="pl-faint pl-small">Switch on an agent in <a href="/agents">Agents</a> to build plugins.</p>' : "") +
      '</form><aside class="pl-sketch" aria-label="What you\'d get"><p class="pl-capq">What you\'d get</p><div class="pl-try"><div class="pl-mrail"><span class="pl-kt t-warm"><span lang="ja">家</span></span><span class="pl-kt t-info"><span lang="ja">話</span></span><span class="pl-mdiv"></span>' + installed().filter(function (p) { return p.running && (p.pages || []).length; }).slice(0, 3).map(function (p) { return tile(p); }).join("") + (want("page") ? '<span class="pl-kt is-pending"><span lang="ja">新</span></span>' : "") + '</div><div class="pl-mhome"><span class="pl-mcap">Home</span>' + (want("card") ? '<div class="pl-ghostcard">Your card, like “3 articles halfway”</div>' : '<span class="pl-faint pl-small">No Home card</span>') + '<span class="pl-mcap">Chat</span>' + (want("tool") ? '<span class="pl-tc"><span class="pl-kt t-warm"><span lang="ja">新</span></span><code>save_article</code><span class="ok">✓</span></span>' : '<span class="pl-faint pl-small">No agent tools</span>') + (want("starter") ? '<span class="pl-bub">Save this for later</span>' : "") + "</div></div><p class=\"pl-faint pl-small\">" + (want("skill") ? "Ships a skill so agents know when to use it. " : "") + (want("after") ? "Checks in after every chat turn. " : "") + "New plugins install switched off, so you can read the code first.</p></aside></div>";
  }
  function viewCatalogs() {
    return '<div class="pl-intro"><p>Catalogs are lists of plugins. Adding or refreshing one only reads the list. Nothing installs or runs.</p>' + (ADMIN ? '<span class="pl-intro-a"><button class="btn sm" data-refresh-all' + (S.busy._refresh ? " disabled" : "") + ">" + (S.busy._refresh ? "Refreshing…" : "Refresh all") + '</button><button class="btn primary sm" data-modal="catalog">＋ Add catalog</button></span>' : "") + "</div>" +
      (D.marketplaces.length ? '<div class="pl-tbl pl-gap">' + D.marketplaces.map(function (m) {
        var n = D.catalog.filter(function (e) { return e.marketplace === m.id; });
        var mine = n.filter(function (e) { return byId(e.id); }).length;
        return '<div class="pl-cat"><span class="pl-kt t-warm pl-cat-kt" aria-hidden="true"><span lang="ja">' + esc(CATALOG_KANJI[m.id] || "集") + '</span></span><span class="pl-cat-b"><b>' + esc(m.name) + "</b><small>" + plural(m.plugin_count, "plugin") + (mine ? ", " + mine + " yours" : "") + ". " + (m.refreshed_at ? "Refreshed " + new Date(m.refreshed_at * 1000).toLocaleString() : "Not refreshed yet") + ".</small><code>" + esc(m.source) + '</code></span><span class="pl-cat-a">' + (m.plugin_count ? '<button class="btn sm" data-browse-src="' + esc(m.id) + '">Browse</button>' : "") + (ADMIN ? '<button class="btn sm" data-mkt="refresh" data-mid="' + esc(m.id) + '"' + (S.busy["m:" + m.id] ? " disabled" : "") + ">" + (S.busy["m:" + m.id] ? "Refreshing…" : "Refresh") + "</button>" + (m.reserved ? "" : '<button class="btn ghost sm" data-mkt="remove" data-mid="' + esc(m.id) + '">Remove</button>') : "") + "</span></div>";
      }).join("") + "</div>" : empty("No catalogs", "Add one to browse its plugins.", ""));
  }

  // ---------- detail panel ----------
  function tryOn(p, mine) {
    var live = mine && p.running;
    var others = installed().filter(function (x) { return x.running && (x.pages || []).length && x.id !== p.id; }).slice(0, 3);
    var pages = (p.pages || []).length;
    var cap = !mine ? "After install" : live ? "Live now" : broken(p) ? "Once it can start" : "When switched on";
    var right;
    if (!live) right = '<p class="pl-small pl-faint">' + (mine ? "Switch it on to see its pages, Home card and agent tools." : "Tomo lists its pages, Home card and agent tools once it's installed and switched on.") + "</p>";
    else right = (p.home_cards ? '<div class="pl-ghostcard is-live">' + tile(p) + "<span>" + plural(p.home_cards, "card") + " available on Home</span></div>" : '<span class="pl-small pl-faint">No Home card</span>') +
      ((p.tools || []).length ? '<span class="pl-tc">' + tile(p) + "<code>" + esc(p.tools[0].name) + "</code>" + (p.tools.length > 1 ? '<span class="pl-faint">+' + (p.tools.length - 1) + "</span>" : '<span class="ok">✓</span>') + "</span>" : "");
    return '<div class="pl-try"><div class="pl-mrail"><span class="pl-kt t-warm"><span lang="ja">家</span></span><span class="pl-kt t-info"><span lang="ja">話</span></span><span class="pl-mdiv"></span>' + others.map(function (x) { return tile(x); }).join("") + (!live || pages ? tile(p, "is-me" + (live ? "" : " is-pending")) : "") + '</div><div class="pl-mhome"><span class="pl-mcap"><span>' + cap + "</span><span>" + (live ? (pages ? plural(pages, "page") : "No page") : "") + "</span></span>" + right + "</div></div>";
  }
  function renderPanel() {
    var p = selected();
    if (!p) { panel.hidden = true; panel.innerHTML = ""; root.classList.remove("has-panel"); return; }
    var mine = S.sel.charAt(0) === "i";
    var have = mine ? p : byId(p.id);
    var u = mine && upd(p), d = deps(p), busy = S.busy[mine ? p.id : "c:" + entryKey(p)];
    var acts = mine ? toggle(p) : have ? '<button class="btn sm" data-open="i:' + esc(have.id) + '">See yours</button>' : ADMIN ? '<button class="btn primary sm" data-install="' + esc(entryKey(p)) + '"' + (busy ? " disabled" : "") + ">" + (busy ? "Installing…" : "Install") + "</button>" : "";
    var menu = "";
    if (mine && ADMIN) {
      menu = '<div class="pl-dd"><button class="pl-ib" data-pop="more" aria-label="More actions" aria-expanded="' + (S.pop === "more") + '">⋯</button>' + (S.pop === "more" ? '<div class="pl-pop">' +
        (p.running && (p.pages || []).length ? '<a href="' + esc(p.pages[0].path) + '">Open ' + esc(p.pages[0].label) + "<small>↗</small></a>" : "") +
        (p.source === "git" ? '<button data-check>Check for updates</button>' : "") +
        (p.running ? '<button data-act="reload" data-id="' + esc(p.id) + '">Reload code<small>no restart</small></button>' : "") +
        ((d.requirements || []).length || d.status === "invalid" ? '<button data-act="sync-dependencies" data-id="' + esc(p.id) + '">Reinstall Python packages</button>' : "") +
        '<button data-agent="change">Ask an agent to change it</button><hr><button class="danger" data-confirm>Uninstall…</button></div>' : "") + "</div>";
    }
    var tools = p.tools || [], skills = p.skills || [];
    var source = mine ? (p.origin ? p.origin.url + (p.origin.subdirectory ? "/" + p.origin.subdirectory : "") + "@" + (p.origin.ref || "main") : p.path || "Bundled with Tomo") : p.source.url + (p.source.subdirectory ? "/" + p.source.subdirectory : "") + "@" + p.source.ref;
    panel.innerHTML = '<div class="pl-pbar"><button class="pl-ib" data-close aria-label="Close details">✕</button><span class="pl-sp"></span>' + menu + "</div>" +
      '<div class="pl-pbody">' + tryOn(p, mine) +
      '<div class="pl-dh"><div><h2 id="pl-panel-title">' + tile(p) + esc(p.name) + (S.fresh === p.id ? '<span class="pl-hanko" aria-hidden="true" lang="ja">済</span>' : "") + '</h2><div class="pl-meta"><span>by ' + esc(who(p)) + "</span>" + (p.category ? "<span>" + esc(p.category) + "</span>" : "") + (p.version ? '<span class="mono">v' + esc(p.version) + "</span>" : "") + '</div></div><div class="pl-acts">' + acts + "</div></div>" +
      (S.confirm ? '<div class="pl-note bad" role="alert"><b>Uninstall ' + esc(p.name) + "?</b><p>Its pages, tools and Home cards go away now. Saved data stays, so reinstalling brings it back.</p><div class=\"pl-na\"><button class=\"btn sm\" data-keep>Keep it</button><button class=\"btn danger sm\" data-act=\"uninstall\" data-id=\"" + esc(p.id) + '">Uninstall</button></div></div>' : "") +
      (mine && broken(p) ? '<div class="pl-note bad"><b>Can\'t switch on</b><p>' + esc(issueText(p)) + "</p>" + (d.environment_error ? "<pre>" + esc(d.environment_error) + "</pre>" : "") + (ADMIN ? '<div class="pl-na">' + (d.status === "missing" || d.status === "invalid" ? '<button class="btn primary sm" data-act="sync-dependencies" data-id="' + esc(p.id) + '">Install packages</button>' : "") + '<button class="btn ghost sm" data-agent="fix">Ask an agent to fix it</button></div>' : "") + "</div>" : "") +
      (u ? '<div class="pl-note up"><b>' + esc(upLabel(u)) + ' is ready</b><p class="mono">' + esc((u.installed_commit || p.commit || "").slice(0, 7)) + " → " + esc((u.latest_commit || "").slice(0, 7)) + "</p>" + (ADMIN ? '<div class="pl-na"><button class="btn primary sm" data-act="update" data-id="' + esc(p.id) + '"' + (S.busy[p.id] ? " disabled" : "") + ">" + (S.busy[p.id] ? "Updating…" : "Update") + "</button></div>" : "") + "</div>" : "") +
      (mine && S.fresh === p.id && !p.running ? '<div class="pl-note warn"><b>Installed, switched off</b><p>Read its code if you like, then switch it on. Its pages and tools appear right away.</p></div>' : "") +
      '<p class="pl-lead">' + esc(p.description) + "</p>" +
      (!mine && !have ? '<p class="pl-note warn"><span>Plugins run inside Tomo with the server\'s permissions. They can read files and reach the network. ' + (p.marketplace === "tomo-official" ? "Made by the Tomo team." : "Made by " + esc(p.author) + ", not reviewed by Tomo.") + "</span></p>" : "") +
      ((p.pages || []).length ? '<section class="pl-sec"><h3>Pages<span>' + p.pages.length + '</span></h3><div class="pl-pages">' + p.pages.map(function (x) { return '<a href="' + esc(x.path) + '">' + esc(x.label) + "</a>"; }).join("") + "</div></section>" : "") +
      (tools.length ? '<section class="pl-sec"><h3>Agent tools<span>' + tools.length + '</span></h3><div class="pl-tools">' + tools.map(function (t) { return "<div><code>" + esc(t.name) + "</code><p>" + esc(t.description) + "</p></div>"; }).join("") + "</div></section>" : "") +
      (skills.length ? '<section class="pl-sec"><h3>Skills<span>' + skills.length + '</span></h3><div class="pl-tools">' + skills.map(function (t) { return "<div><code>" + esc(t.id) + "</code><p>" + esc(t.description) + "</p></div>"; }).join("") + "</div></section>" : "") +
      '<section class="pl-sec"><dl class="pl-kv"><div><dt>From</dt><dd>' + esc(fromName(p)) + "</dd></div><div><dt>Plugin ID</dt><dd class=\"mono\">" + esc(p.id) + '</dd></div><div class="wide"><dt>Source</dt><dd class="mono">' + esc(source) + "</dd></div>" + (mine && p.commit ? '<div><dt>Commit</dt><dd class="mono">' + esc(p.commit.slice(0, 7)) + "</dd></div>" : "") + (mine ? "<div><dt>Python packages</dt><dd>" + esc((d.requirements || []).join(", ") || "None") + "</dd></div><div><dt>Your data</dt><dd>Kept when off or uninstalled</dd></div>" : "") + "</dl></section></div>";
    panel.hidden = false;
    panel.setAttribute("aria-labelledby", "pl-panel-title");
    root.classList.add("has-panel");
  }

  // ---------- modals ----------
  function renderModal(kind, value) {
    if (!kind) { modalBox.innerHTML = ""; return; }
    var src = kind === "source";
    modalBox.innerHTML = '<div class="pl-scrim" data-scrim><form class="pl-modal" role="dialog" aria-modal="true" aria-labelledby="pl-mt" id="' + (src ? "plugin-install" : "marketplace-add") + '"><button type="button" class="pl-ib pl-x" data-mclose aria-label="Close">✕</button>' +
      (src ? '<h2 id="pl-mt">Install from source</h2><p class="pl-sub">A GitHub repository, a catalog reference like <code>money@tomo-official</code>, or a folder on this server.</p><label class="pl-fl" for="plugin-path">Source</label><input class="input pl-in" id="plugin-path" name="path" required value="' + esc(value || "") + '" placeholder="https://github.com/owner/tomo-plugin"><div class="pl-two"><label>Branch, tag or commit<input class="input pl-in" id="plugin-ref" name="ref" value="main"></label><label>Folder in the repo<input class="input pl-in" id="plugin-subdirectory" name="subdirectory" placeholder="Optional"></label></div><p class="pl-warnline">Plugins run inside Tomo with full access to this server. Install only sources you trust. Tomo installs it switched off, so you can read the code first.</p><div class="pl-mact"><button type="button" class="btn ghost" data-mclose>Cancel</button><button class="btn primary" type="submit">Install</button></div>'
        : '<h2 id="pl-mt">Add a catalog</h2><p class="pl-sub">Its plugins show up in Browse. Nothing installs until you choose.</p><label class="pl-fl" for="marketplace-source">Catalog source</label><input class="input pl-in" id="marketplace-source" name="source" required placeholder="https://example.com/marketplace.json"><p class="pl-hint">An HTTPS marketplace.json URL, git:https://github.com/owner/repo@main, or path:/directory</p><div class="pl-mact"><button type="button" class="btn ghost" data-mclose>Cancel</button><button class="btn primary" type="submit">Add catalog</button></div>') +
      "</form></div>";
    var input = modalBox.querySelector("input");
    if (input) input.focus();
  }

  // ---------- render ----------
  function render() {
    var view = {installed: viewInstalled, browse: viewBrowse, updates: viewUpdates, build: viewBuild, catalogs: viewCatalogs}[S.tab];
    var active = document.activeElement, focusId = active && active.id, caret = focusId === "pl-q" ? active.selectionStart : null;
    if (focusId === "plugin-idea") S.idea = active.value;
    root.innerHTML = header() + '<div class="pl-body" role="tabpanel" aria-labelledby="pl-tab-' + S.tab + '">' + view() + "</div>";
    renderPanel();
    if (focusId && document.getElementById(focusId)) {
      var el = document.getElementById(focusId);
      el.focus();
      if (caret !== null) el.setSelectionRange(caret, caret);
    }
    railGhost();
  }
  // Preview the hovered catalog plugin as a dashed tile in the real sidebar.
  function railGhost() {
    var old = document.querySelector(".pl-rail-ghost");
    if (old) old.remove();
    var g = S.tab === "browse" && S.ghost && byKey(S.ghost);
    if (!g || byId(g.id)) return;
    var label = Array.prototype.find.call(document.querySelectorAll("#appRail .app-rail-sec"), function (x) { return /Under the roof/.test(x.textContent); });
    if (!label) return;
    var a = document.createElement("span");
    a.className = "app-rail-item pl-rail-ghost";
    a.setAttribute("aria-hidden", "true");
    a.innerHTML = '<span class="app-rail-ico"><span class="app-rail-room">' + (g.kanji ? '<span lang="ja">' + esc(g.kanji) + "</span>" : "+") + '</span></span><span class="app-rail-label">' + esc(g.name) + "</span>";
    label.parentNode.insertBefore(a, label);
  }
  // Plugin pages live in the server-rendered rail; swap it in after changes.
  async function refreshRail() {
    try {
      var res = await fetch(location.pathname, {credentials: "same-origin"});
      var doc = new DOMParser().parseFromString(await res.text(), "text/html");
      var next = doc.getElementById("appRail"), cur = document.getElementById("appRail");
      if (next && cur) { cur.innerHTML = next.innerHTML; cur.className = next.className; }
    } catch (_) { /* rail catches up on the next page load */ }
  }
  async function reload(withCatalog) {
    var jobs = [Tomo.api("/api/plugins")];
    if (withCatalog) jobs.push(Tomo.api("/api/plugins/catalog"), Tomo.api("/api/marketplaces"));
    var r = await Promise.all(jobs);
    if (Array.isArray(r[0])) D.plugins = r[0];
    if (withCatalog) { if (Array.isArray(r[1])) D.catalog = r[1]; if (Array.isArray(r[2])) D.marketplaces = r[2]; }
    render();
  }
  // ---------- actions ----------
  var VERB = {enable: ["Turning on…", "is on."], disable: ["Turning off…", "is off. Its pages and tools are hidden."], reload: ["Reloading…", "reloaded."], update: ["Updating…", "updated. No restart needed."], "sync-dependencies": ["Installing packages…", "has its Python packages."], uninstall: ["Uninstalling…", "uninstalled. Its data is kept."]};
  async function act(id, action) {
    if (S.busy[id]) return;
    var p = byId(id); var name = p ? p.name : id;
    S.busy[id] = action; S.pop = null; render();
    try {
      await Tomo.api("/api/plugins/" + encodeURIComponent(id) + "/" + action, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
      if (action === "update" && S.updates) delete S.updates[id];
      if (action === "uninstall") { S.sel = null; S.confirm = false; }
      if (action === "enable") S.fresh = null;
      delete S.busy[id];
      await reload(false);
      Tomo.toast(name + " " + VERB[action][1], "ok");
      if (action !== "sync-dependencies") refreshRail();
    } catch (e) {
      delete S.busy[id]; render();
      Tomo.toast(e.message || "Plugin action failed", "err");
    }
  }
  async function updateAll() {
    var ids = installed().filter(upd).map(function (p) { return p.id; });
    S.busy._all = true; render();
    var failed = [];
    for (var i = 0; i < ids.length; i++) {
      try { await Tomo.api("/api/plugins/" + encodeURIComponent(ids[i]) + "/update", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"}); if (S.updates) delete S.updates[ids[i]]; }
      catch (e) { failed.push(byId(ids[i]).name + ": " + (e.message || "failed")); }
    }
    delete S.busy._all;
    await reload(false); refreshRail();
    if (failed.length) Tomo.toast("Some updates failed. " + failed.join("; "), "err");
    else Tomo.toast(plural(ids.length, "plugin") + " updated. No restart needed.", "ok");
  }
  async function install(spec, body) {
    var key = "c:" + spec;
    if (S.busy[key]) return;
    S.busy[key] = true; render();
    try {
      var row = await Tomo.api("/api/plugins/install", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {path: spec})});
      delete S.busy[key]; S.ghost = null;
      await reload(false);
      if (row && row.id) { S.sel = "i:" + row.id; S.fresh = row.id; render(); }
      Tomo.toast((row && row.name ? row.name : "Plugin") + " installed. Switch it on when ready.", "ok");
    } catch (e) {
      delete S.busy[key]; render();
      Tomo.toast(e.message || "Install failed", "err");
    }
  }
  async function checkUpdates(force) {
    if (!ADMIN || S.checking) return;
    S.checking = true; render();
    try {
      var list = await Tomo.api("/api/plugins/check-updates", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({force: force})});
      if (!Array.isArray(list)) throw new Error("Invalid update response");
      S.updates = {};
      list.forEach(function (u) { S.updates[u.id] = u; });
      S.checked = true;
      if (force) {
        var n = list.filter(function (u) { return u.status === "available"; }).length;
        Tomo.toast(n ? plural(n, "update") + " ready." : "Everything is up to date.", "ok");
      }
    } catch (e) { Tomo.toast("Couldn't check for updates: " + (e.message || "request failed"), "err"); }
    S.checking = false; render();
  }
  async function marketplace(kind, id) {
    var key = "m:" + id;
    if (kind === "remove" && !window.confirm("Remove this catalog? Installed plugins stay.")) return;
    S.busy[key] = true; render();
    try {
      await Tomo.api("/api/marketplaces/" + encodeURIComponent(id) + (kind === "remove" ? "" : "/refresh"), {method: kind === "remove" ? "DELETE" : "POST"});
      delete S.busy[key];
      await reload(true);
      Tomo.toast(kind === "remove" ? "Catalog removed." : "Catalog refreshed.", "ok");
    } catch (e) { delete S.busy[key]; render(); Tomo.toast(e.message || "Catalog action failed", "err"); }
  }
  async function refreshAll() {
    S.busy._refresh = true; render();
    var res = await Promise.allSettled(D.marketplaces.map(function (m) { return Tomo.api("/api/marketplaces/" + encodeURIComponent(m.id) + "/refresh", {method: "POST"}); }));
    delete S.busy._refresh;
    var failed = res.flatMap(function (r, i) { return r.status === "rejected" ? [D.marketplaces[i].name + ": " + (r.reason.message || "refresh failed")] : []; });
    await reload(true);
    if (failed.length) Tomo.toast(failed.join("; "), "err"); else Tomo.toast("Catalogs refreshed.", "ok");
  }
  async function loadIdeas(refresh) {
    if (!ADMIN || S.ideasBusy || (S.ideas && !refresh)) return;
    S.ideasBusy = true; S.ideasNote = ""; render();
    try {
      var data = await Tomo.api("/api/plugins/ideas" + (refresh ? "?refresh=true" : ""));
      if (!data || !Array.isArray(data.prompts) || !data.prompts.length || data.prompts.some(function (i) { return !i || typeof i.label !== "string" || !i.label.trim() || typeof i.prompt !== "string" || !i.prompt.trim(); })) throw new Error("Invalid ideas");
      S.ideas = data.prompts.map(function (i) { return {label: i.label, prompt: i.prompt}; });
      S.ideasNote = data.source === "llm" ? "Ideas from your recent activity." : "";
    } catch (_) { S.ideasNote = "Suggestions are unavailable right now. Pick an example or write your own."; }
    S.ideasBusy = false; render();
  }
  function agentChat(prompt) {
    var agent = D.agents[0];
    location.href = "/sessions?" + new URLSearchParams(agent ? {agent: agent.id, q: prompt} : {q: prompt}).toString();
  }

  // ---------- routing ----------
  function go(tab, keepQ) {
    if (TABS.indexOf(tab) === -1 || (tab === "build" && !ADMIN)) tab = "installed";
    var changed = tab !== S.tab;
    S.tab = tab; S.pop = null; S.ghost = null;
    if (changed) { S.need = ""; S.src = {}; S.shown = {}; S.sort = ""; if (!keepQ) S.q = ""; }
    if (location.hash.slice(1) !== tab) history.replaceState(null, "", location.pathname + location.search + "#" + tab);
    render();
    if (tab === "build") loadIdeas(false);
    if (tab === "updates" && !S.checked) checkUpdates(false);
  }
  function open(key) { S.sel = key; S.confirm = false; S.pop = null; render(); var b = panel.querySelector(".pl-pbody"); if (b) b.scrollTop = 0; var c = panel.querySelector("[data-close]"); if (c) c.focus(); }
  function close() {
    var key = S.sel; S.sel = null; S.confirm = false; S.pop = null; render();
    var back = key && root.querySelector('.pl-name[data-open="' + CSS.escape(key) + '"]');
    if (back) back.focus();
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest("button, a, [data-open], [data-scrim]");
    if (!t || !(root.contains(t) || panel.contains(t) || modalBox.contains(t))) { if (S.pop && !(t && t.closest(".pl-dd"))) { S.pop = null; render(); } return; }
    var d = t.dataset;
    if (t.tagName === "A" && !d.open) return;
    if (d.scrim !== undefined) { if (e.target === t) renderModal(null); return; }
    if (d.mclose !== undefined) return renderModal(null);
    if (d.tab) { if (d.seed !== undefined) S.idea = d.seed; return go(d.tab, d.keepq !== undefined); }
    if (d.pop) { S.pop = S.pop === d.pop ? null : d.pop; return render(); }
    if (d.tg) { var p = byId(d.tg); return act(p.id, p.running ? "disable" : "enable"); }
    if (d.act) { if (d.act === "uninstall") S.confirm = false; return act(d.id, d.act); }
    if (d.updall !== undefined) return updateAll();
    if (d.install) return install(d.install);
    if (d.check !== undefined) { S.pop = null; return checkUpdates(true); }
    if (d.close !== undefined) return close();
    if (d.confirm !== undefined) { S.pop = null; S.confirm = true; return render(); }
    if (d.keep !== undefined) { S.confirm = false; return render(); }
    if (d.agent) {
      var sp = selected();
      return agentChat(d.agent === "fix" ? "The Tomo plugin " + sp.name + " (" + sp.id + ") can't start: " + issueText(sp) + "\n\nFind the cause and fix it with plugin_manager. Don't restart Tomo." : "I want to change the Tomo plugin " + sp.name + " (" + sp.id + ", source " + (sp.path || "") + "). Ask me what to change, then edit it with the plugin-development skill and reload it with plugin_manager. Don't restart Tomo.");
    }
    if (d.modal) { S.pop = null; render(); return renderModal(d.modal, d.modal === "source" && isSource(S.q) ? S.q.trim() : ""); }
    if (d.sort) { S.sort = d.sort; S.pop = null; return render(); }
    if (d.layout) { S.layout = d.layout; try { localStorage.setItem("tomo.plugins.layout", d.layout); } catch (_) {} return render(); }
    if (d.need !== undefined) { S.need = d.need; S.shown = {}; return render(); }
    if (d.more) { S.shown[d.more] = (S.shown[d.more] || 4) + 24; return render(); }
    if (d.clear !== undefined) { S.q = ""; S.need = ""; S.src = {}; return render(); }
    if (d.browseSrc) { go("browse"); S.src = {}; S.src[d.browseSrc] = 1; return render(); }
    if (d.mkt) return marketplace(d.mkt, d.mid);
    if (d.refreshAll !== undefined) return refreshAll();
    if (d.cap) { if (S.caps[d.cap]) delete S.caps[d.cap]; else S.caps[d.cap] = 1; return render(); }
    if (d.example) { S.idea = d.example; render(); var ta = document.getElementById("plugin-idea"); ta.focus(); return; }
    if (t.id === "plugin-ideas-refresh") return loadIdeas(true);
    if (d.open && !e.target.closest(".pl-tg, .pl-get, [data-act], [data-install]")) return open(d.open);
  });
  document.addEventListener("change", function (e) {
    var s = e.target.dataset && e.target.dataset.src;
    if (s) { if (e.target.checked) S.src[s] = 1; else delete S.src[s]; render(); }
  });
  document.addEventListener("input", function (e) {
    if (e.target.id === "pl-q") { S.q = e.target.value; S.shown = {}; render(); }
    if (e.target.id === "plugin-idea") S.idea = e.target.value;
  });
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (f.id === "plugin-install") {
      e.preventDefault();
      var body = {path: f.elements.path.value.trim(), ref: f.elements.ref.value.trim() || "main", subdirectory: f.elements.subdirectory.value.trim()};
      renderModal(null); S.q = ""; go("installed");
      return install(body.path, body);
    }
    if (f.id === "marketplace-add") {
      e.preventDefault();
      var btn = f.querySelector("[type=submit]"); btn.disabled = true; btn.textContent = "Adding…";
      Tomo.api("/api/marketplaces", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({source: f.elements.source.value.trim()})})
        .then(function () { renderModal(null); return reload(true); })
        .then(function () { Tomo.toast("Catalog added. Its plugins are in Browse.", "ok"); })
        .catch(function (err) { btn.disabled = false; btn.textContent = "Add catalog"; Tomo.toast(err.message || "Couldn't add catalog", "err"); });
      return;
    }
    if (f.id === "plugin-build") {
      e.preventDefault();
      var idea = f.elements.idea.value.trim();
      if (!idea || !f.elements.agent.value) return;
      var adds = BUILD_CAPS.filter(function (c) { return S.caps[c[0]]; }).map(function (c) { return CAP_PROMPT[c[0]]; });
      var prompt = "Build a Tomo plugin for this request:\n\n" + idea + "\n\nIt should add: " + (adds.length ? adds.join("; ") : "whatever fits the request") + ".\n\nUse the plugin-development skill and Tomo's current plugin SDK. Create a trusted Python plugin with tomo-plugin.json (including a suitable named icon and a single-kanji \"kanji\") and plugin.py. Keep data private per user; use api.user_data_dir() correctly. Build in the selected local workplace (or your Tomo work directory). Declare Python dependencies in requirements.txt or project.dependencies in pyproject.toml using distribution names (cv2 needs opencv-python-headless). Test the pages and tools, then install, sync_dependencies, and enable the finished plugin with plugin_manager. Do not restart Tomo or modify its core for this plugin. Explain what was built and link its pages.";
      location.href = "/sessions?" + new URLSearchParams({agent: f.elements.agent.value, wp: f.elements.workplace.value, q: prompt}).toString();
    }
  });
  var hovT;
  root.addEventListener("mouseover", function (e) {
    if (S.tab !== "browse") return;
    var c = e.target.closest("[data-hover]");
    var key = c ? c.dataset.hover : null;
    if (key === S.ghost) return;
    clearTimeout(hovT);
    hovT = setTimeout(function () {
      S.ghost = key;
      var s = root.querySelector(".pl-strip");
      if (s) s.outerHTML = strip();
      railGhost();
    }, key ? 60 : 250);
  });
  document.addEventListener("keydown", function (e) {
    var tag = (document.activeElement || {}).tagName;
    if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(tag) && document.getElementById("pl-q")) { e.preventDefault(); document.getElementById("pl-q").focus(); }
    if (e.key === "Escape") {
      if (modalBox.innerHTML) return renderModal(null);
      if (S.pop) { S.pop = null; return render(); }
      if (S.confirm) { S.confirm = false; return render(); }
      if (S.sel) return close();
    }
    var tab = e.target.closest && e.target.closest(".pl-tab");
    if (tab && ["ArrowLeft", "ArrowRight", "Home", "End"].indexOf(e.key) !== -1) {
      e.preventDefault();
      var names = Array.prototype.map.call(root.querySelectorAll(".pl-tab"), function (x) { return x.dataset.tab; });
      var i = names.indexOf(tab.dataset.tab);
      var next = e.key === "Home" ? names[0] : e.key === "End" ? names[names.length - 1] : names[(i + (e.key === "ArrowRight" ? 1 : -1) + names.length) % names.length];
      go(next); document.getElementById("pl-tab-" + next).focus();
    }
    if (e.key === "Enter" && e.target.matches && e.target.matches(".pl-plug, .pl-row, .pl-lr")) open(e.target.dataset.open);
  });
  window.addEventListener("hashchange", function () { go(location.hash.slice(1)); });

  try { S.layout = localStorage.getItem("tomo.plugins.layout") === "list" ? "list" : "cards"; } catch (_) {}
  S.q = new URLSearchParams(location.search).get("q") || "";
  var start = location.hash.slice(1) || (installed().length ? "installed" : "browse");
  if (start === "create") start = "build";
  if (start === "discover") start = "browse";
  go(start, true);
  // Quietly fetch cached update status so the Installed lane and Updates count are current.
  if (ADMIN && installed().some(function (p) { return p.source === "git"; }) && S.tab !== "updates") checkUpdates(false);
})();

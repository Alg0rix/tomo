(function () {
  "use strict";
  async function submit(path, body, button, method) {
    if (button && button.disabled) return;
    var label = button ? button.textContent : "";
    if (button) { button.disabled = true; button.textContent = "Working…"; button.setAttribute("aria-busy", "true"); }
    try {
      await Tomo.api(path, {method: method || "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
      if (path === "/api/plugins/install" && document.querySelector(".plugin-hub")) location.hash = "installed";
      window.location.reload();
    } catch (error) {
      Tomo.toast(error.message || "Plugin action failed", "err");
      if (button) { button.disabled = false; button.textContent = label; button.removeAttribute("aria-busy"); }
    }
  }
  var form = document.getElementById("plugin-install");
  if (form) form.addEventListener("submit", function (event) {
    event.preventDefault();
    submit("/api/plugins/install", {path: form.elements.path.value.trim(), ref: form.elements.ref ? form.elements.ref.value.trim() || "main" : "main", subdirectory: form.elements.subdirectory ? form.elements.subdirectory.value.trim() : ""}, form.querySelector("button[type=submit]"));
  });
  var addMarketplace = document.getElementById("marketplace-add");
  if (addMarketplace) addMarketplace.addEventListener("submit", function (event) {
    event.preventDefault();
    submit("/api/marketplaces", {source: addMarketplace.elements.source.value}, addMarketplace.querySelector("button"));
  });
  document.querySelectorAll("[data-marketplace]").forEach(function (button) {
    button.addEventListener("click", function () {
      var remove = button.dataset.marketplaceAction === "remove";
      submit("/api/marketplaces/" + encodeURIComponent(button.dataset.marketplace) + (remove ? "" : "/refresh"), {}, button, remove ? "DELETE" : "POST");
    });
  });
  document.querySelectorAll("[data-catalog-install]").forEach(function (button) {
    button.addEventListener("click", function () { submit("/api/plugins/install", {path: button.dataset.catalogInstall}, button); });
  });
  document.querySelectorAll("[data-plugin][data-action]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (button.dataset.action === "uninstall" && !window.confirm("Uninstall this plugin? Its saved data will be kept.")) return;
      submit("/api/plugins/" + encodeURIComponent(button.dataset.plugin) + "/" + button.dataset.action, {}, button);
    });
  });
  document.querySelectorAll("[data-open-dialog]").forEach(function (button) {
    button.addEventListener("click", function () { document.getElementById(button.dataset.openDialog).showModal(); });
  });
  document.querySelectorAll("[data-close-dialog]").forEach(function (button) {
    button.addEventListener("click", function () { button.closest("dialog").close(); });
  });
  if (!document.querySelector(".plugin-hub")) return;
  var search = document.getElementById("hub-search");
  var filter = document.getElementById("hub-filter");
  var toolbar = document.querySelector(".hub-toolbar");
  var sort = document.getElementById("hub-sort");
  var view = "discover";
  var publisherOptions = filter.innerHTML;
  var params = new URLSearchParams(location.search);
  search.value = params.get("q") || "";
  function applyFilter() {
    var panel = document.getElementById("hub-" + view);
    if (view === "create") return;
    var count = 0;
    var grid = panel.querySelector(".hub-grid");
    Array.from(grid.querySelectorAll("[data-hub-card]")).sort(function (a, b) {
      var result = a.querySelector(".hub-title").textContent.localeCompare(b.querySelector(".hub-title").textContent);
      return sort.value === "desc" ? -result : result;
    }).forEach(function (card) { grid.appendChild(card); });
    panel.querySelectorAll("[data-hub-card]").forEach(function (card) {
      var matches = card.dataset.search.includes(search.value.trim().toLowerCase()) && (filter.value === "all" || (view === "installed" ? card.dataset.status : card.dataset.publisher) === filter.value);
      card.hidden = !matches;
      if (matches) count++;
    });
    panel.querySelector("[data-hub-empty]").hidden = count > 0;
    var query = new URLSearchParams(location.search);
    if (search.value) query.set("q", search.value); else query.delete("q");
    history.replaceState(null, "", location.pathname + (query.size ? "?" + query.toString() : "") + location.hash);
  }
  function switchView() {
    document.querySelectorAll("dialog[open]").forEach(function (dialog) { dialog.close(); });
    var requested = location.hash.slice(1);
    view = ["installed", "create"].includes(requested) && document.getElementById("hub-" + requested) ? requested : "discover";
    ["discover", "installed", "create"].forEach(function (name) {
      var panel = document.getElementById("hub-" + name);
      if (panel) panel.hidden = name !== view;
    });
    document.querySelectorAll("[data-hub-tab]").forEach(function (tab) {
      var active = tab.dataset.hubTab === view;
      tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
    });
    toolbar.hidden = view === "create";
    filter.innerHTML = view === "installed" ? '<option value="all">All statuses</option><option value="running">Running</option><option value="disabled">Disabled</option>' : publisherOptions;
    filter.setAttribute("aria-label", view === "installed" ? "Filter by status" : "Filter by publisher");
    applyFilter();
    if (view === "create") loadIdeas(false);
  }
  document.querySelectorAll("[data-hub-tab]").forEach(function (tab) {
    tab.addEventListener("click", function () { location.hash = tab.dataset.hubTab; });
    tab.addEventListener("keydown", function (event) {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      var names = Array.from(document.querySelectorAll("[data-hub-tab]")).map(function (item) { return item.dataset.hubTab; });
      var index = names.indexOf(tab.dataset.hubTab);
      var target = event.key === "Home" ? names[0] : event.key === "End" ? names[names.length - 1] : names[(index + (event.key === "ArrowRight" ? 1 : -1) + names.length) % names.length];
      document.getElementById("tab-" + target).focus(); location.hash = target;
    });
  });
  document.querySelectorAll("[data-hub-create]").forEach(function (button) { button.addEventListener("click", function () { location.hash = "create"; }); });
  window.addEventListener("hashchange", switchView);
  search.addEventListener("input", applyFilter); filter.addEventListener("change", applyFilter); sort.addEventListener("change", applyFilter);
  var refresh = document.getElementById("hub-refresh");
  if (refresh) refresh.addEventListener("click", async function () {
    var feedback = document.getElementById("hub-feedback");
    refresh.disabled = true; refresh.textContent = "Refreshing…"; feedback.hidden = true;
    var marketplaces = JSON.parse(document.getElementById("hub-marketplaces").textContent);
    var results = await Promise.allSettled(marketplaces.map(function (marketplace) { return Tomo.api("/api/marketplaces/" + encodeURIComponent(marketplace.id) + "/refresh", {method: "POST"}); }));
    var failures = results.flatMap(function (result, i) { return result.status === "rejected" ? [marketplaces[i].name + ": " + (result.reason.message || "Refresh failed")] : []; });
    if (!failures.length && marketplaces.length) window.location.reload();
    else { feedback.textContent = failures.length ? failures.join(" · ") + ". Successful refreshes are saved; reload this page to view them." : "Connect a marketplace in Settings first."; feedback.hidden = false; refresh.disabled = false; refresh.textContent = "↻ Refresh catalogs"; }
  });
  var ideasLoaded = false;
  var ideasBusy = false;
  async function loadIdeas(refresh) {
    var container = document.getElementById("plugin-ideas");
    if (!container || ideasBusy || (ideasLoaded && !refresh)) return;
    ideasBusy = true;
    var button = document.getElementById("plugin-ideas-refresh");
    var status = document.getElementById("plugin-ideas-status");
    button.disabled = true; button.setAttribute("aria-busy", "true");
    status.textContent = "Finding ideas for you…";
    try {
      var data = await Tomo.api("/api/plugins/ideas" + (refresh ? "?refresh=true" : ""));
      if (!data || !Array.isArray(data.prompts) || data.prompts.length !== 3 || data.prompts.some(function (idea) { return !idea || typeof idea.label !== "string" || !idea.label.trim() || typeof idea.prompt !== "string" || !idea.prompt.trim(); })) throw new Error("Invalid ideas");
      var items = data.prompts.map(function (idea) {
        var item = document.createElement("button"); item.type = "button"; item.dataset.pluginExample = idea.prompt;
        var title = document.createElement("strong"); title.textContent = idea.label;
        var description = document.createElement("span"); description.textContent = idea.prompt;
        item.append(title, description); return item;
      });
      container.replaceChildren.apply(container, items);
      ideasLoaded = true;
      status.textContent = data.source === "llm" ? "Inspired by your recent activity." : "A few ideas to get you started.";
    } catch (_) { status.textContent = "Suggestions are unavailable. You can use these examples or write your own idea."; }
    finally { ideasBusy = false; button.disabled = false; button.removeAttribute("aria-busy"); }
  }
  var ideasRefresh = document.getElementById("plugin-ideas-refresh");
  if (ideasRefresh) ideasRefresh.addEventListener("click", function () { loadIdeas(true); });
  var build = document.getElementById("plugin-build");
  if (build) {
    document.getElementById("plugin-ideas").addEventListener("click", function (event) {
      var button = event.target.closest("[data-plugin-example]");
      if (!button) return;
      build.elements.idea.value = button.dataset.pluginExample; build.elements.idea.focus();
    });
    build.addEventListener("submit", function (event) {
      event.preventDefault();
      var idea = build.elements.idea.value.trim();
      if (!idea || !build.elements.agent.value) return;
      var prompt = "Build a Tomo plugin for this request:\n\n" + idea + "\n\nUse the plugin-development skill and Tomo's current plugin SDK. Create a trusted Python plugin with tomo-plugin.json (including a suitable named icon) and plugin.py, multiple pages where useful, sidebar navigation, domain tools for Tomo's existing agents, and a skills/<name>/SKILL.md package explaining how agents use it. Keep data private per user; use api.user_data_dir() correctly. Build in the selected local workplace (or your Tomo work directory). Test the pages and tools, then install and enable the finished plugin with plugin_manager. Do not restart Tomo or modify its core for this plugin. Explain what was built and link its pages.";
      var query = new URLSearchParams({agent: build.elements.agent.value, wp: build.elements.workplace.value, q: prompt});
      window.location.href = "/sessions?" + query.toString();
    });
  }
  switchView();
})();

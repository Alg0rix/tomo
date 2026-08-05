/* workplaces.js — create/edit/Connect for workplaces */
(function () {
  "use strict";

  var formCard = document.getElementById("workplaceFormCard");
  var kindEl = document.getElementById("wpKind");
  var modeEl = document.getElementById("wpFormMode");

  function syncKindFields() {
    var kind = kindEl ? kindEl.value : "local";
    document.querySelectorAll(".wp-local-only").forEach(function (el) {
      el.style.display = kind === "local" ? "" : "none";
    });
    document.querySelectorAll(".wp-ssh-only").forEach(function (el) {
      el.style.display = kind === "ssh" ? "" : "none";
    });
    document.querySelectorAll(".wp-tunnel-only").forEach(function (el) {
      el.style.display = kind === "tunnel" ? "" : "none";
    });
    document.querySelectorAll(".wp-tunnel-ish").forEach(function (el) {
      el.style.display = kind === "tunnel" ? "" : "none";
    });
    syncInstallToggle();
  }

  function syncInstallToggle() {
    var tog = document.getElementById("wpInstallToggle");
    var on = !!(tog && tog.checked);
    document.querySelectorAll(".wp-ssh-install").forEach(function (el) {
      el.style.display = on ? "" : "none";
    });
  }

  if (kindEl) kindEl.addEventListener("change", syncKindFields);
  var installToggle = document.getElementById("wpInstallToggle");
  if (installToggle) installToggle.addEventListener("change", syncInstallToggle);
  syncKindFields();

  var newBtn = document.getElementById("newWorkplaceBtn");
  if (newBtn && formCard) {
    newBtn.addEventListener("click", function () {
      modeEl.value = "add";
      document.getElementById("workplaceFormTitle").textContent = "New workplace";
      var idEl = document.getElementById("wpId");
      if (idEl) { idEl.value = ""; }
      document.getElementById("wpName").value = "";
      kindEl.value = "local";
      document.getElementById("wpRootPath").value = "";
      document.getElementById("wpSshHost").value = "";
      document.getElementById("wpSshPort").value = "22";
      document.getElementById("wpSshUser").value = "";
      document.getElementById("wpSshPassword").value = "";
      document.getElementById("wpSshKey").value = "";
      var ps = document.getElementById("wpPwdStatus"); if (ps) ps.textContent = "";
      var ks = document.getElementById("wpKeyStatus"); if (ks) ks.textContent = "";
      var it = document.getElementById("wpInstallToggle"); if (it) it.checked = false;
      var is = document.getElementById("wpInstallStatus"); if (is) is.hidden = true;
      var itxt = document.getElementById("wpInstallStatusText"); if (itxt) itxt.textContent = "";
      if (document.getElementById("wpInstallSshHost")) document.getElementById("wpInstallSshHost").value = "";
      if (document.getElementById("wpInstallSshPort")) document.getElementById("wpInstallSshPort").value = "22";
      if (document.getElementById("wpInstallSshUser")) document.getElementById("wpInstallSshUser").value = "";
      if (document.getElementById("wpInstallSshPassword")) document.getElementById("wpInstallSshPassword").value = "";
      syncKindFields();
      formCard.classList.remove("hidden");
    });
  }

  var editBtn = document.getElementById("wpEditBtn");
  if (editBtn && formCard) {
    editBtn.addEventListener("click", function () {
      formCard.classList.remove("hidden");
      syncKindFields();
    });
  }

  var cancel = document.getElementById("wpCancel");
  if (cancel && formCard) {
    cancel.addEventListener("click", function () { formCard.classList.add("hidden"); });
  }

  function collectBody() {
    var body = {
      name: document.getElementById("wpName").value.trim(),
      kind: kindEl.value,
      root_path: document.getElementById("wpRootPath").value.trim(),
      ssh_host: document.getElementById("wpSshHost").value.trim(),
      ssh_port: parseInt(document.getElementById("wpSshPort").value, 10) || 22,
      ssh_user: document.getElementById("wpSshUser").value.trim(),
    };
    var pwd = document.getElementById("wpSshPassword").value;
    if (pwd) body.ssh_password = pwd;
    var key = document.getElementById("wpSshKey").value;
    if (key && key.trim()) body.ssh_key = key;
    return body;
  }

  function setInstallStatus(text, show) {
    var s = document.getElementById("wpInstallStatus");
    var t = document.getElementById("wpInstallStatusText");
    if (s) s.hidden = !show;
    if (t) t.textContent = text || "";
  }

  async function runInstallViaSsh(btn) {
    var host = document.getElementById("wpInstallSshHost").value.trim();
    var user = document.getElementById("wpInstallSshUser").value.trim();
    var name = document.getElementById("wpName").value.trim();
    if (!host || !user) {
      Tomo.toast("SSH host and user are required to install via SSH", "err");
      return;
    }
    var payload = {
      name: name || (user + "@" + host),
      ssh_host: host,
      ssh_port: parseInt(document.getElementById("wpInstallSshPort").value, 10) || 22,
      ssh_user: user,
    };
    var pwd = document.getElementById("wpInstallSshPassword").value;
    if (pwd) payload.ssh_password = pwd;
    if (btn) btn.disabled = true;
    setInstallStatus("Connecting…", true);
    try {
      // Poll the async install job. The endpoint returns immediately with a job
      // id, then we poll /status until it reaches a terminal state.
      var job = await Tomo.api("/api/workplaces/install-via-ssh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      var jobId = job && (job.job_id || job.id);
      if (!jobId) {
        // Synchronous completion (created directly).
        Tomo.toast("Connector installed", "ok");
        setTimeout(function () { location.reload(); }, 500);
        return;
      }
      var done = false;
      while (!done) {
        var st = await Tomo.api("/api/workplaces/install-via-ssh/" + encodeURIComponent(jobId), {
          method: "GET",
        });
        if (st && st.status === "installing") {
          setInstallStatus("Installing connector…", true);
        } else if (st && st.status === "pairing") {
          setInstallStatus("Pairing…", true);
        } else if (st && (st.status === "done" || st.status === "ok")) {
          setInstallStatus("", false);
          Tomo.toast("Connector installed and paired", "ok");
          setTimeout(function () { location.reload(); }, 500);
          done = true;
        } else if (st && (st.status === "error" || st.status === "failed")) {
          setInstallStatus("", false);
          Tomo.toast((st.message || "Install failed"), "err");
          done = true;
        } else {
          // Unknown status → treat as in-progress; keep polling.
          setInstallStatus("Working…", true);
        }
        if (!done) await new Promise(function (r) { setTimeout(r, 1500); });
      }
    } catch (e) {
      setInstallStatus("", false);
      Tomo.toast((e && e.message) || "Install via SSH failed", "err");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  var save = document.getElementById("wpSave");
  if (save) {
    save.addEventListener("click", async function () {
      var body = collectBody();
      var mode = modeEl ? modeEl.value : "add";
      // Tunnel + "Install via SSH" → call the provision endpoint instead of plain create.
      var installToggle = document.getElementById("wpInstallToggle");
      if (mode === "add" && body.kind === "tunnel" && installToggle && installToggle.checked) {
        await runInstallViaSsh(save);
        return;
      }
      try {
        if (mode === "add") {
          var created = await Tomo.api("/api/workplaces", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          Tomo.toast("Workplace created", "ok");
          setTimeout(function () {
            location.href = "/workplaces/" + encodeURIComponent(created.id);
          }, 400);
        } else {
          var wid = document.getElementById("wpId").value;
          await Tomo.api("/api/workplaces/" + encodeURIComponent(wid), {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          Tomo.toast("Workplace saved", "ok");
          setTimeout(function () { location.reload(); }, 400);
        }
      } catch (e) {
        Tomo.toast((e && e.message) || "Could not save", "err");
      }
    });
  }

  var connectBtn = document.getElementById("wpConnectBtn");
  if (connectBtn) {
    connectBtn.addEventListener("click", async function () {
      var wid = connectBtn.dataset.id;
      var msg = document.getElementById("wpConnectMsg");
      connectBtn.disabled = true;
      try {
        var result = await Tomo.api("/api/workplaces/" + encodeURIComponent(wid) + "/connect", {
          method: "POST",
        });
        if (msg) msg.textContent = result.message || "";
        Tomo.toast(result.ok ? "Connected" : (result.message || "Connect failed"), result.ok ? "ok" : "err");
        setTimeout(function () { location.reload(); }, 600);
      } catch (e) {
        Tomo.toast((e && e.message) || "Connect failed", "err");
        connectBtn.disabled = false;
      }
    });
  }

  // Enable / disable (non-local workplaces)
  function toggleEnabled(wid, enabled) {
    var action = enabled ? "enable" : "disable";
    var btn = document.getElementById(enabled ? "wpEnableBtn" : "wpDisableBtn");
    if (btn) btn.disabled = true;
    return Tomo.api("/api/workplaces/" + encodeURIComponent(wid) + "/" + action, {
      method: "POST",
    }).then(function (result) {
      Tomo.toast(enabled ? "Workplace enabled" : "Workplace disabled", "ok");
      setTimeout(function () { location.reload(); }, 400);
      return result;
    }).catch(function (e) {
      Tomo.toast((e && e.message) || "Could not " + action, "err");
      if (btn) btn.disabled = false;
      throw e;
    });
  }

  var disableBtn = document.getElementById("wpDisableBtn");
  if (disableBtn) {
    disableBtn.addEventListener("click", function () {
      var wid = disableBtn.dataset.id;
      if (!window.confirm("Disable this workplace? Its tunnel/connector will be disconnected and it will refuse new connections until re-enabled.")) {
        return;
      }
      toggleEnabled(wid, false);
    });
  }

  var enableBtn = document.getElementById("wpEnableBtn");
  if (enableBtn) {
    enableBtn.addEventListener("click", function () {
      toggleEnabled(enableBtn.dataset.id, true);
    });
  }

  var genPair = document.getElementById("wpGenPairing");
  if (genPair) {
    genPair.addEventListener("click", async function () {
      var wid = genPair.dataset.id;
      genPair.disabled = true;
      try {
        var result = await Tomo.api(
          "/api/workplaces/" + encodeURIComponent(wid) + "/pairing-code",
          { method: "POST" }
        );
        Tomo.toast("Pairing code ready", "ok");
        setTimeout(function () { location.reload(); }, 400);
      } catch (e) {
        Tomo.toast((e && e.message) || "Could not generate code", "err");
        genPair.disabled = false;
      }
    });
  }

  var copyBtn = document.getElementById("wpCopyCode");
  if (copyBtn) {
    copyBtn.addEventListener("click", async function () {
      var code = copyBtn.dataset.code || "";
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(code);
        } else {
          var ta = document.createElement("textarea");
          ta.value = code;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
        }
        Tomo.toast("Code copied", "ok");
      } catch (e) {
        Tomo.toast("Copy failed", "err");
      }
    });
  }

  // List filter
  var wpSearch = document.getElementById("wpSearch");
  var wpList = document.getElementById("workplaceTiles");
  var wpMeta = document.getElementById("wpSearchMeta");
  var wpEmpty = document.getElementById("wpSearchEmpty");
  if (wpSearch && wpList) {
    var rows = Array.prototype.slice.call(wpList.querySelectorAll(".wp-row, .tile"));
    function hay(el) {
      return (el.getAttribute("data-search") || el.textContent || "").toLowerCase();
    }
    function run() {
      var q = (wpSearch.value || "").trim().toLowerCase();
      var n = 0;
      rows.forEach(function (r) {
        var show = !q || hay(r).indexOf(q) !== -1;
        r.hidden = !show;
        r.style.display = show ? "" : "none";
        if (show) n++;
      });
      if (wpMeta) {
        if (q) {
          wpMeta.hidden = false;
          wpMeta.textContent = n + " shown";
        } else {
          wpMeta.hidden = true;
          wpMeta.textContent = "";
        }
      }
      if (wpEmpty) {
        var none = q && n === 0;
        wpEmpty.hidden = !none;
        wpEmpty.classList.toggle("hidden", !none);
        wpEmpty.textContent = none ? 'No workplaces match “' + q + '”.' : "";
      }
    }
    wpSearch.addEventListener("input", run);
    wpSearch.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        wpSearch.value = "";
        run();
      }
    });
  }
})();

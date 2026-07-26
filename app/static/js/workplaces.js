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
  }

  if (kindEl) kindEl.addEventListener("change", syncKindFields);
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

  var save = document.getElementById("wpSave");
  if (save) {
    save.addEventListener("click", async function () {
      var body = collectBody();
      var mode = modeEl ? modeEl.value : "add";
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
})();

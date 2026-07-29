/* folder_picker.js — VS Code–style local folder browser + auto-register workplace. */
(function () {
  "use strict";

  function esc(s) {
    return Tomo.escapeHtml(s);
  }

  /**
   * Open a modal to browse the server filesystem and pick a directory.
   * On success, auto-registers (or reuses) a local workplace and resolves:
   *   { workplace_id, workplace, path, created }
   * Rejects on cancel.
   */
  Tomo.pickLocalFolder = function (opts) {
    opts = opts || {};
    var startPath = opts.path || "";
    var title = opts.title || "Open folder";

    return new Promise(function (resolve, reject) {
      var existing = document.getElementById("folderPickerModal");
      if (existing) existing.remove();

      var modal = document.createElement("div");
      modal.id = "folderPickerModal";
      modal.className = "modal";
      modal.setAttribute("aria-hidden", "false");
      modal.innerHTML =
        '<div class="modal-backdrop" data-fp-close="1"></div>' +
        '<div class="modal-card modal-wide folder-picker-card">' +
          '<div class="modal-head"><h3>' + esc(title) + '</h3></div>' +
          '<div class="modal-body folder-picker-body">' +
            '<p class="modal-lead">Browse this server’s local disks (like VS Code Open Folder). Selecting a folder registers it as a workplace automatically.</p>' +
            '<div class="folder-picker-toolbar">' +
              '<button type="button" class="btn ghost sm" data-fp-home title="Home">Home</button>' +
              '<button type="button" class="btn ghost sm" data-fp-up title="Parent">↑ Up</button>' +
              '<input type="text" class="input folder-picker-path" data-fp-path spellcheck="false" placeholder="/absolute/path">' +
              '<button type="button" class="btn ghost sm" data-fp-go>Go</button>' +
            '</div>' +
            '<div class="folder-picker-search-row">' +
              '<input type="search" class="input" data-fp-q placeholder="Filter folders in this directory…" spellcheck="false">' +
            '</div>' +
            '<div class="folder-picker-crumbs mono faint" data-fp-crumbs></div>' +
            '<div class="folder-picker-list" data-fp-list></div>' +
            '<p class="faint folder-picker-hint" data-fp-hint></p>' +
          '</div>' +
          '<div class="modal-foot">' +
            '<button class="btn ghost" type="button" data-fp-close="1">Cancel</button>' +
            '<button class="btn primary" type="button" data-fp-select>Use this folder</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(modal);

      var pathInput = modal.querySelector("[data-fp-path]");
      var listEl = modal.querySelector("[data-fp-list]");
      var crumbsEl = modal.querySelector("[data-fp-crumbs]");
      var hintEl = modal.querySelector("[data-fp-hint]");
      var qInput = modal.querySelector("[data-fp-q]");
      var currentPath = startPath || "";
      var parentPath = null;
      var homePath = "";
      var closed = false;
      var searchTimer = null;

      function close() {
        if (closed) return;
        closed = true;
        modal.remove();
      }

      function cancel() {
        close();
        reject(new Error("cancelled"));
      }

      function setHint(msg, isErr) {
        if (!hintEl) return;
        hintEl.textContent = msg || "";
        hintEl.classList.toggle("err", !!isErr);
      }

      function renderCrumbs(path) {
        if (!crumbsEl) return;
        var parts = String(path || "").split("/").filter(Boolean);
        var acc = "";
        var html = '<button type="button" class="folder-crumb" data-fp-jump="/">/</button>';
        parts.forEach(function (part) {
          acc += "/" + part;
          html +=
            '<span class="folder-crumb-sep">/</span>' +
            '<button type="button" class="folder-crumb" data-fp-jump="' +
            esc(acc) +
            '">' +
            esc(part) +
            "</button>";
        });
        crumbsEl.innerHTML = html;
        crumbsEl.querySelectorAll("[data-fp-jump]").forEach(function (btn) {
          btn.addEventListener("click", function () {
            load(btn.getAttribute("data-fp-jump") || "/");
          });
        });
      }

      function renderList(entries) {
        if (!entries.length) {
          listEl.innerHTML =
            '<div class="empty">No matching folders here</div>';
          return;
        }
        listEl.innerHTML = entries
          .map(function (e) {
            return (
              '<button type="button" class="folder-picker-item" data-fp-enter="' +
              esc(e.path) +
              '">' +
              '<span class="folder-picker-icon" aria-hidden="true">📁</span>' +
              '<span class="folder-picker-name">' +
              esc(e.name) +
              "</span>" +
              '<span class="folder-picker-path mono faint">' +
              esc(e.path) +
              "</span>" +
              "</button>"
            );
          })
          .join("");
        listEl.querySelectorAll("[data-fp-enter]").forEach(function (btn) {
          btn.addEventListener("click", function () {
            load(btn.getAttribute("data-fp-enter") || "");
          });
          btn.addEventListener("dblclick", function () {
            load(btn.getAttribute("data-fp-enter") || "");
          });
        });
      }

      async function load(path, q) {
        setHint("Loading…");
        listEl.innerHTML = '<div class="empty">Loading…</div>';
        try {
          var url =
            "/api/fs/browse?path=" +
            encodeURIComponent(path || "") +
            (q ? "&q=" + encodeURIComponent(q) : "");
          var data = await Tomo.api(url);
          if (!data) throw new Error("no data");
          currentPath = data.path || path || "";
          parentPath = data.parent || null;
          homePath = data.home || homePath;
          pathInput.value = currentPath;
          renderCrumbs(currentPath);
          renderList(data.entries || []);
          setHint(
            (data.entries || []).length +
              " folder(s)" +
              (data.capped ? " (capped)" : "") +
              " · Select this folder to register as a workplace"
          );
        } catch (err) {
          listEl.innerHTML =
            '<div class="empty">Could not open folder</div>';
          setHint(
            (err && err.message) || "Browse failed",
            true
          );
        }
      }

      async function selectCurrent() {
        if (!currentPath) {
          setHint("No path selected", true);
          return;
        }
        setHint("Registering workplace…");
        try {
          var res = await Tomo.api("/api/workplaces/ensure-local", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: currentPath }),
          });
          if (!res || !res.workplace) throw new Error("register failed");
          var wp = res.workplace;
          close();
          resolve({
            workplace_id: wp.id,
            workplace: wp,
            path: wp.root_path || currentPath,
            created: !!res.created,
          });
        } catch (err) {
          setHint(
            (err && (err.body && err.body.detail)) ||
              (err && err.message) ||
              "Could not register folder",
            true
          );
        }
      }

      modal.querySelectorAll("[data-fp-close]").forEach(function (el) {
        el.addEventListener("click", cancel);
      });
      modal.querySelector("[data-fp-home]").addEventListener("click", function () {
        load(homePath || "");
      });
      modal.querySelector("[data-fp-up]").addEventListener("click", function () {
        if (parentPath) load(parentPath);
      });
      modal.querySelector("[data-fp-go]").addEventListener("click", function () {
        load(pathInput.value.trim());
      });
      pathInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          load(pathInput.value.trim());
        }
      });
      qInput.addEventListener("input", function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
          load(currentPath, qInput.value.trim());
        }, 200);
      });
      modal.querySelector("[data-fp-select]").addEventListener("click", selectCurrent);

      load(startPath || "");
    });
  };
})();

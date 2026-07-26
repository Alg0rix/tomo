/* plugins.js — enable/disable plugin metadata */
(function () {
  "use strict";
  document.querySelectorAll(".tile[data-plugin-id]").forEach(function (tile) {
    var input = tile.querySelector(".plugin-toggle input[type='checkbox']");
    if (!input) return;
    input.addEventListener("change", async function () {
      var id = tile.getAttribute("data-plugin-id");
      try {
        await Tomo.api("/api/plugins/" + encodeURIComponent(id), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !!input.checked }),
        });
        Tomo.toast(input.checked ? "Plugin enabled" : "Plugin disabled", "ok");
      } catch (e) {
        input.checked = !input.checked;
        Tomo.toast((e && e.message) || "Could not update plugin", "err");
      }
    });
  });
})();

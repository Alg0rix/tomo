/* modules.js — enable/disable Tomo modules */
(function () {
  "use strict";

  document.querySelectorAll(".tile[data-module-id]").forEach(function (tile) {
    var input = tile.querySelector(".module-toggle input[type='checkbox']");
    if (!input) return;
    input.addEventListener("change", async function () {
      var id = tile.getAttribute("data-module-id");
      try {
        await Tomo.api("/api/modules/" + encodeURIComponent(id), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !!input.checked }),
        });
        Tomo.toast(input.checked ? "Module enabled" : "Module disabled");
      } catch (e) {
        input.checked = !input.checked;
        Tomo.toast((e && e.message) || "Could not update module", "err");
      }
    });
  });
})();

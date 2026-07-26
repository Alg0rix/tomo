/* scheduler.js — create / enable / disable schedules */
(function () {
  "use strict";

  var formCard = document.getElementById("scheduleFormCard");
  var newBtn = document.getElementById("newScheduleBtn");
  if (newBtn && formCard) {
    newBtn.addEventListener("click", function () {
      formCard.classList.remove("hidden");
    });
  }

  var cancel = document.getElementById("schCancel");
  if (cancel && formCard) {
    cancel.addEventListener("click", function () {
      formCard.classList.add("hidden");
    });
  }

  var save = document.getElementById("schSave");
  if (save) {
    save.addEventListener("click", async function () {
      var name = (document.getElementById("schName").value || "").trim();
      var agentId = document.getElementById("schAgent").value;
      var interval = parseInt(document.getElementById("schInterval").value, 10) || 0;
      var message = (document.getElementById("schMessage").value || "").trim();
      var enabled = !!document.getElementById("schEnabled").checked;
      if (!name) {
        Tomo.toast("Name is required", "err");
        return;
      }
      if (interval < 5) {
        Tomo.toast("Interval must be at least 5 seconds", "err");
        return;
      }
      try {
        await Tomo.api("/api/schedules", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: name,
            agent_id: agentId,
            interval_seconds: interval,
            message: message,
            enabled: enabled,
          }),
        });
        Tomo.toast("Schedule created", "ok");
        setTimeout(function () {
          location.reload();
        }, 400);
      } catch (e) {
        Tomo.toast((e && e.message) || "Could not create schedule", "err");
      }
    });
  }

  document.querySelectorAll(".sch-toggle").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      var id = btn.getAttribute("data-id");
      var enabled = btn.getAttribute("data-enabled") === "1";
      try {
        await Tomo.api("/api/schedules/" + encodeURIComponent(id), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !enabled }),
        });
        Tomo.toast(enabled ? "Disabled" : "Enabled", "ok");
        setTimeout(function () {
          location.reload();
        }, 350);
      } catch (e) {
        Tomo.toast((e && e.message) || "Could not update", "err");
      }
    });
  });
})();

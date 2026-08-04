/* scheduler.js — create / pause / resume / run-now schedules */
(function () {
  "use strict";

  function scheduleApiPath(id, action) {
    const base = "/api/schedules/" + encodeURIComponent(id);
    return action ? base + "/" + action : base;
  }

  const formCard = document.getElementById("scheduleFormCard");
  const newBtn = document.getElementById("newScheduleBtn");
  if (newBtn && formCard) {
    newBtn.addEventListener("click", function () {
      formCard.classList.remove("hidden");
    });
  }

  const cancel = document.getElementById("schCancel");
  if (cancel && formCard) {
    cancel.addEventListener("click", function () {
      formCard.classList.add("hidden");
    });
  }

  const save = document.getElementById("schSave");
  if (save) {
    save.addEventListener("click", async function () {
      const name = (document.getElementById("schName").value || "").trim();
      const agentId = document.getElementById("schAgent").value;
      const schedule = (document.getElementById("schSchedule").value || "").trim();
      const message = (document.getElementById("schMessage").value || "").trim();
      const enabled = !!document.getElementById("schEnabled").checked;
      if (!name) {
        Tomo.toast("Name is required", "err");
        return;
      }
      if (!schedule) {
        Tomo.toast("Schedule is required (e.g. every 1h)", "err");
        return;
      }
      try {
        await Tomo.api("/api/schedules", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: name,
            agent_id: agentId,
            schedule: schedule,
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
      const id = btn.getAttribute("data-id");
      const enabled = btn.getAttribute("data-enabled") === "1";
      const action = enabled ? "pause" : "resume";
      try {
        await Tomo.api(scheduleApiPath(id, action), { method: "POST" });
        Tomo.toast(enabled ? "Paused" : "Resumed", "ok");
        setTimeout(function () {
          location.reload();
        }, 350);
      } catch (e) {
        Tomo.toast((e && e.message) || "Could not update", "err");
      }
    });
  });

  document.querySelectorAll(".sch-run").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      const id = btn.getAttribute("data-id");
      btn.disabled = true;
      try {
        const res = await Tomo.api(scheduleApiPath(id, "run"), { method: "POST" });
        Tomo.toast(
          res && res.status === "ok" ? "Run finished" : "Run ended: " + ((res && res.status) || "?"),
          res && res.status === "ok" ? "ok" : "err"
        );
        setTimeout(function () {
          location.reload();
        }, 500);
      } catch (e) {
        Tomo.toast((e && e.message) || "Run failed", "err");
        btn.disabled = false;
      }
    });
  });
})();

/* agents.js — new-agent dialog + tab switching */
(function () {
  "use strict";
  const btn = document.getElementById("newAgentBtn");
  const dlg = document.getElementById("newDlg");
  const form = document.getElementById("newForm");
  const cancel = document.getElementById("newCancel");
  const backBtn = document.getElementById("newBack");
  const generateBtn = document.getElementById("newGenerate");
  const createBtn = document.getElementById("newCreate");
  const llmPanel = document.getElementById("newAgentLlm");
  const advPanel = document.getElementById("newAgentAdvanced");
  const llmInput = document.getElementById("newAgentLlmInput");
  const llmPreview = document.getElementById("newAgentLlmPreview");
  const briefEl = document.getElementById("newAgentBrief");
  const modeTabs = document.querySelectorAll(".agent-new-modes .pill-tab");

  var mode = "llm";
  var llmStep = "input";
  var draft = null;
  var generating = false;

  function generateLabel() {
    return draft && llmStep === "input" ? "Regenerate" : "Generate";
  }

  function setGenerateLabel(text) {
    if (!generateBtn) return;
    var label = generateBtn.querySelector(".btn-label");
    if (label) label.textContent = text;
    else generateBtn.textContent = text;
  }

  function setGenerateLoading(on) {
    generating = !!on;
    if (generateBtn) {
      generateBtn.disabled = on;
      generateBtn.classList.toggle("is-loading", on);
      generateBtn.setAttribute("aria-busy", on ? "true" : "false");
      setGenerateLabel(on ? "Generating…" : generateLabel());
    }
    if (cancel) cancel.disabled = on;
    if (briefEl) briefEl.disabled = on;
    modeTabs.forEach(function (tab) {
      tab.disabled = on;
    });
  }

  function setHidden(el, hidden) {
    if (!el) return;
    el.classList.toggle("hidden", !!hidden);
  }

  function setMode(next) {
    mode = next;
    modeTabs.forEach(function (tab) {
      var on = tab.dataset.mode === mode;
      tab.classList.toggle("active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
    });
    setHidden(llmPanel, mode !== "llm");
    setHidden(advPanel, mode !== "advanced");
    syncFooter();
  }

  function setLlmStep(step) {
    llmStep = step;
    setHidden(llmInput, step !== "input");
    setHidden(llmPreview, step !== "preview");
    syncFooter();
  }

  function syncFooter() {
    var isLlm = mode === "llm";
    var isPreview = isLlm && llmStep === "preview";
    setHidden(backBtn, !isPreview);
    setHidden(generateBtn, !isLlm || isPreview);
    setHidden(createBtn, isLlm && !isPreview);
    if (createBtn) {
      createBtn.textContent = isLlm ? "Create agent" : "Create";
    }
    if (generateBtn && !isPreview && !generating) {
      setGenerateLabel(generateLabel());
    }
  }

  function fillPreview(data) {
    var nameEl = document.getElementById("previewName");
    var roleEl = document.getElementById("previewRole");
    var idEl = document.getElementById("previewId");
    var descEl = document.getElementById("previewDesc");
    var sysEl = document.getElementById("previewSystem");
    if (nameEl) nameEl.textContent = data.name || "—";
    if (roleEl) roleEl.textContent = data.role || "—";
    if (idEl) idEl.textContent = data.suggested_id || "auto from name";
    if (descEl) descEl.textContent = data.description || "—";
    if (sysEl) sysEl.textContent = data.system_prompt || "—";
  }

  function applyDraftToAdvanced() {
    if (!draft) return;
    var nameInput = form.querySelector('[name="name"]');
    var roleInput = form.querySelector('[name="role"]');
    var descInput = form.querySelector('[name="description"]');
    var idInput = form.querySelector('[name="id"]');
    if (nameInput) nameInput.value = draft.name || "";
    if (roleInput) roleInput.value = draft.role || "";
    if (descInput) descInput.value = draft.description || "";
    if (idInput) idInput.value = draft.suggested_id || "";
  }

  function resetDialog() {
    setGenerateLoading(false);
    form.reset();
    draft = null;
    mode = "llm";
    setMode("llm");
    setLlmStep("input");
    if (briefEl) briefEl.focus();
  }

  function buildCreateBody() {
    if (mode === "llm" && draft) {
      return {
        name: String(draft.name || "").trim(),
        role: String(draft.role || "").trim(),
        description: String(draft.description || "").trim(),
        model_id: null,
        system_prompt: String(draft.system_prompt || "").trim() || null,
      };
    }
    const fd = new FormData(form);
    const body = {
      name: String(fd.get("name") || "").trim(),
      role: String(fd.get("role") || "").trim(),
      description: String(fd.get("description") || "").trim(),
      model_id: String(fd.get("model_id") || "").trim() || null,
    };
    var id = String(fd.get("id") || "").trim();
    if (id) body.id = id.toLowerCase().replace(/-/g, "_");
    var wid = String(fd.get("workplace_id") || "").trim();
    if (wid) {
      body.workplace_id = wid;
      body.workplace_ids = [wid];
      body.workplace_scope = "single";
    }
    return body;
  }

  async function createAgent() {
    const body = buildCreateBody();
    if (!body.name) {
      Tomo.toast("Name is required", "err");
      return;
    }
    if (createBtn) createBtn.disabled = true;
    try {
      const created = await Tomo.api("/api/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      Tomo.toast('Agent "' + (created.name || body.name) + '" created', "ok");
      if (window.Tomo && Tomo.renderRail) Tomo.renderRail();
      dlg.close();
      setTimeout(function () {
        location.href = "/agents/" + encodeURIComponent(created.id);
      }, 500);
    } catch (err) {
      Tomo.toast(
        (err && err.body && err.body.detail) ||
          (err && err.message) ||
          "Could not create agent",
        "err"
      );
      if (createBtn) createBtn.disabled = false;
    }
  }

  if (btn && dlg && form) {
    btn.addEventListener("click", function () {
      resetDialog();
      dlg.showModal();
    });
    if (cancel) {
      cancel.addEventListener("click", function () {
        dlg.close();
      });
    }
    if (backBtn) {
      backBtn.addEventListener("click", function () {
        setLlmStep("input");
        if (briefEl) briefEl.focus();
      });
    }
    dlg.addEventListener("click", function (e) {
      if (e.target === dlg && !generating) dlg.close();
    });
    modeTabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        if (generating) return;
        var next = tab.dataset.mode || "llm";
        if (next === mode) return;
        if (next === "advanced" && draft) applyDraftToAdvanced();
        setMode(next);
        if (next === "llm" && briefEl) briefEl.focus();
        if (next === "advanced") {
          var nameInput = form.querySelector('[name="name"]');
          if (nameInput) nameInput.focus();
        }
      });
    });
    if (generateBtn) {
      generateBtn.addEventListener("click", async function () {
        var brief = String((briefEl && briefEl.value) || "").trim();
        if (brief.length < 3) {
          Tomo.toast("Describe the agent in a few words", "err");
          if (briefEl) briefEl.focus();
          return;
        }
        setGenerateLoading(true);
        try {
          draft = await Tomo.api("/api/agents/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ brief: brief }),
          });
          fillPreview(draft);
          setLlmStep("preview");
        } catch (err) {
          Tomo.toast(
            (err && err.body && err.body.detail) ||
              (err && err.message) ||
              "Could not generate agent",
            "err"
          );
        } finally {
          setGenerateLoading(false);
          syncFooter();
        }
      });
    }
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      await createAgent();
    });
  }

  document.querySelectorAll("#agentTabs .pill-tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll("#agentTabs .pill-tab").forEach(function (t) {
        t.classList.remove("active");
      });
      tab.classList.add("active");
      document.getElementById("tabAgents").style.display =
        tab.dataset.tab === "agents" ? "block" : "none";
      document.getElementById("tabWorkplaces").style.display =
        tab.dataset.tab === "workplaces" ? "block" : "none";
    });
  });
})();

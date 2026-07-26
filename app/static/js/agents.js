/* agents.js — new-agent dialog + tab switching */
(function () {
  "use strict";
  const btn = document.getElementById('newAgentBtn'), dlg = document.getElementById('newDlg'),
    form = document.getElementById('newForm'), cancel = document.getElementById('newCancel');
  if (btn && dlg) {
    btn.addEventListener('click', function () { dlg.showModal(); });
    cancel.addEventListener('click', function () { dlg.close(); });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      const fd = new FormData(form);
      const body = { id: fd.get('id'), name: fd.get('name'), role: fd.get('role') || '', description: fd.get('description'), model_id: fd.get('model_id') || '' };
      try {
        await Tomo.api('/api/agents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        Tomo.toast('Agent "' + body.name + '" created', 'ok');
        setTimeout(function () { location.href = '/agents/' + encodeURIComponent(body.id); }, 600);
      } catch (err) { Tomo.toast(err.message || 'Could not create agent', 'err'); }
    });
  }
  document.querySelectorAll('#agentTabs .pill-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      document.querySelectorAll('#agentTabs .pill-tab').forEach(function (t) { t.classList.remove('active'); });
      tab.classList.add('active');
      document.getElementById('tabAgents').style.display = tab.dataset.tab === 'agents' ? 'block' : 'none';
      document.getElementById('tabWorkplaces').style.display = tab.dataset.tab === 'workplaces' ? 'block' : 'none';
    });
  });
})();

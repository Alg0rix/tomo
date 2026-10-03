/* Settings self-update controls and service restart monitoring. */
(function () {
  'use strict';

  var field = document.getElementById('selfUpdateField');
  if (!field) return;
  var updateBtn = document.getElementById('selfUpdateBtn');
  var checkBtn = document.getElementById('selfUpdateCheckBtn');
  var statusEl = document.getElementById('selfUpdateStatus');
  var hintEl = document.getElementById('selfUpdateHint');
  var detailsEl = document.getElementById('selfUpdateDetails');
  var versionEl = document.getElementById('tomoVersion');
  var current = null;
  var busy = false;

  function status(text) { statusEl.textContent = text; }

  function controls() {
    field.setAttribute('aria-busy', String(busy));
    checkBtn.disabled = busy || !!(current && !current.can_update);
    updateBtn.disabled = busy || !current || !current.can_update || current.updating || !(current.commits_behind > 0);
    updateBtn.textContent = current && current.updating ? 'Updating…' : 'Update now';
    checkBtn.textContent = current && current.updating ? 'Check update status' : 'Check for updates';
  }

  function paint(data) {
    current = data;
    if (data.version) versionEl.textContent = data.version + (data.head ? ' · ' + data.head : '');
    detailsEl.textContent = 'Branch: ' + (data.branch || 'unknown') + ' · Installed: ' + (data.head || 'unknown') +
      (data.remote_head ? ' · Latest: ' + data.remote_head : '');
    updateBtn.hidden = checkBtn.hidden = !data.can_update;
    if (!data.can_update) {
      hintEl.textContent = data.reason === 'container' ?
        'Container install. Update the image and recreate the container to upgrade Tomo.' :
        'Development install. Update this checkout from your terminal.';
      status('Update via UI is available for script installs.');
    } else {
      hintEl.textContent = 'Installs available updates and restarts Tomo. Active chats may be interrupted.';
      if (data.updating) status('Update is running. Waiting for the service to restart…');
      else if (data.commits_behind == null) status('Ready to check for updates.');
      else if (data.commits_behind > 0) status(data.commits_behind + ' new commit' + (data.commits_behind === 1 ? '' : 's') + ' available.');
      else status('Tomo is up to date.');
    }
    controls();
  }

  async function waitForRestart() {
    var previousInstance = current && current.instance_id;
    var started = Date.now();
    while (Date.now() - started < 180000) {
      await new Promise(function (resolve) { setTimeout(resolve, 1500); });
      var controller = new AbortController();
      var timeout = setTimeout(function () { controller.abort(); }, 5000);
      var data;
      try {
        var res = await fetch('/api/update', {
          headers: { 'Accept': 'application/json' },
          credentials: 'same-origin',
          signal: controller.signal,
        });
        if (res.status === 401) { window.location.reload(); return; }
        if (!res.ok) throw new Error('Service unavailable');
        data = await res.json();
      } catch (e) {
        status('Waiting for Tomo to reconnect…');
        continue;
      } finally {
        clearTimeout(timeout);
      }
      current = data;
      controls();
      // HEAD can change before dependency installation finishes. The running
      // service clears its update flag only after restart (or lockout expiry).
      if (data.updating) {
        status('Update is running. Waiting for the service to restart…');
        continue;
      }
      if (previousInstance && data.instance_id === previousInstance) {
        status('Update stopped without restarting Tomo. Check the service log, then check for updates again.');
        return;
      }
      status('Service is online. Verifying the update…');
      var checked = await Tomo.api('/api/update/check', { method: 'POST' });
      if (!checked) return;
      paint(checked);
      if (checked.commits_behind === 0) {
        Tomo.toast('Tomo updated successfully', 'ok');
        window.location.reload();
      } else {
        status('Update did not finish. Check the service log, then try again.');
        Tomo.toast('Update did not finish', 'err');
      }
      return;
    }
    status('Update is taking longer than expected. Check update status again or inspect the service log.');
  }

  async function checkUpdate() {
    if (busy) return;
    busy = true;
    controls();
    status('Checking for updates…');
    try {
      var data = await Tomo.api('/api/update');
      if (!data) return;
      paint(data);
      if (!data.can_update) return;
      if (data.updating) { await waitForRestart(); return; }
      var checked = await Tomo.api('/api/update/check', { method: 'POST' });
      if (checked) paint(checked);
    } catch (e) {
      if (current) current.commits_behind = null;
      status('Could not check updates: ' + (e.message || 'Please try again.'));
    } finally {
      busy = false;
      controls();
    }
  }

  checkBtn.addEventListener('click', checkUpdate);
  updateBtn.addEventListener('click', async function () {
    if (updateBtn.disabled || busy) return;
    if (!window.confirm('Install ' + current.commits_behind + ' new commit(s) from ' + current.branch +
        ' and restart Tomo? Active chats may be interrupted.')) return;
    busy = true;
    controls();
    status('Starting update…');
    try {
      var result = await Tomo.api('/api/update', { method: 'POST' });
      if (!result) return;
      current.updating = true;
      controls();
      Tomo.toast('Update started', 'info');
      await waitForRestart();
    } catch (e) {
      // Keep the last known in-flight flag; checking status can recover from a
      // lost response without allowing a second update to start.
      try {
        var latest = await Tomo.api('/api/update');
        if (latest) paint(latest);
      } catch (statusError) {
        if (current) current.updating = true;
      }
      status('Could not confirm update: ' + (e.message || 'Check update status again.'));
      Tomo.toast(e.message || 'Could not confirm update', 'err');
    } finally {
      busy = false;
      controls();
    }
  });

  checkUpdate();
})();

/* Shared by every wok fixture: page-error capture and assertions that throw
   with a readable message (a failed `eval` step prints it in the wok report). */
window.__errors = [];
window.addEventListener('error', function (e) { window.__errors.push(String(e.message)); });

window.check = (function () {
  function fail(msg, detail) { throw new Error((msg ? msg + ': ' : '') + detail); }
  return {
    ok: function (value, msg) { if (!value) fail(msg, 'expected truthy, got ' + JSON.stringify(value)); return true; },
    equal: function (actual, expected, msg) {
      var a = JSON.stringify(actual), b = JSON.stringify(expected);
      if (a !== b) fail(msg, 'expected ' + b + ', got ' + a);
      return true;
    },
    match: function (actual, re, msg) { if (!re.test(String(actual))) fail(msg, JSON.stringify(actual) + ' does not match ' + re); return true; },
    noMatch: function (actual, re, msg) { if (re.test(String(actual))) fail(msg, JSON.stringify(actual) + ' should not match ' + re); return true; },
    noErrors: function () { return this.equal(window.__errors, [], 'page errors'); },
  };
})();

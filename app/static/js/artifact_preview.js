/* Load HTML on an opaque data origin so nested srcdoc frames can share it. */
(function (global) {
  "use strict";

  function setContent(frame, text) {
    // allow-same-origin is safe ONLY on the data URL below, never on srcdoc
    // or a Tomo-origin URL. The artifact still cannot access Tomo's document,
    // cookies or storage, but can read its own nested iframe previews.
    frame.removeAttribute("srcdoc");
    frame.removeAttribute("src");
    frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.src = "data:text/html;charset=utf-8," + encodeURIComponent(text);
  }

  global.TomoArtifactPreview = { setContent: setContent };
})(window);

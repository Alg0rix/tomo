"""Supervised browser automation boundary.

Two paths, one rule: internal/control endpoints are never reachable.

* **Offline** (always available, no internet): a ``data:``/owned-file/raw-HTML
  document is rendered inside the caller's per-chat restricted container
  (``network=none``) with the bundled CloakBrowser/Chromium binary. The
  in-container script refuses ``http(s)`` URLs outright, so an offline render
  can never become a network fetch.
* **Online** (operator-gated): an ``http(s)`` URL is fetched in the
  coordinator through the scoped-egress boundary (see
  :mod:`app.runtime.browser.fetch`), then the fetched bytes are rendered
  offline in the container. Redirect hops are SSRF-checked one by one;
  subresources and websockets are never retrieved live — the render sees a
  single fetched document with no network, so a malicious page cannot pivot
  to internal endpoints through images, scripts, XHR or ``ws://``.

There is no ``--network host/bridge`` fallback: containers stay
``network=none``. Online retrieval that needs more than a single document
(fetch more pages, drive a live app) goes through explicit additional
``browser`` calls, each re-authorized and re-checked.
"""

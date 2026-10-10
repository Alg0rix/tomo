# Intelligent answers in Tomo

Tomo adapts [OpenIntelligentUI](https://github.com/CopilotKit/OpenIntelligentUI)'s
sandbox document contract and includes its theme, SVG classes, and form styles.
It uses Tomo's configured models, agent tool execution, SSE, and saved chat history.
No separate CopilotKit, Next.js, Typesafe router, or API key is required.

Enable **Render UI** for the agent, then ask for an interactive calculator,
comparison, diagram, or simulation. Simple answers stay text; basic tables and
charts use native components. Custom interactive answers use a sandbox node:

```json
{
  "ui_id": "calculator",
  "tree": {
    "type": "sandbox",
    "title": "Double a number",
    "initialHeight": 240,
    "html": "<label>Number <input id='number' type='number' value='5'></label><p id='result'></p>",
    "css": "#result { font-size: 32px; }",
    "jsFunctions": "function update() { document.getElementById('result').textContent = Number(document.getElementById('number').value) * 2; }",
    "jsExpressions": "document.getElementById('number').addEventListener('input', update); update();"
  }
}
```

JavaScript runs locally as classic scripts. The import map provides `three`,
`d3`, `gsap`, and `chart.js` through esm.sh; use `import()` inside an async
function. CDN libraries and HTTPS images require network access. Standalone
calculators and inline SVG work offline. Answers appear directly in the conversation without an Interactive label or outer card.
The sandbox background is transparent and its text colors follow Tomo's theme.
Generated layouts group related controls and results using spacing and typography,
without card backgrounds or borders.

`sendPrompt({text: "Explain this result for 5 people"})` dispatches a typed
`sendPrompt` UI action to the current Tomo session. Include the selected values
in the text because arbitrary widget state is local. `openLink({url: "https://…"})`
opens an HTTPS link. The upstream `Websandbox.connection.remote` spelling is also
supported for these two callbacks.

Custom HTML and JavaScript run only inside an iframe with `sandbox="allow-scripts"`.
The frame has an opaque origin and cannot read Tomo's document, cookies, or
storage. CSP blocks Tomo API requests, forms, frames, and plugins. Scripts and fetch
requests can load only from the four allowed library CDNs. Bridge messages are matched to the exact
iframe window, prompt lengths and resize heights are bounded, and removed
frames cannot dispatch actions. Native nodes never execute generated HTML.

Saved history rebuilds each answer after reload. Identical repeated delivery
keeps the current widget alive; a changed sandbox tree rebuilds it. Custom control
state resets on reload or replacement. Native declarative controls retain their
existing session state. Use `save_artifact` for downloadable apps/files.

Each content field allows 20,000 characters; the complete tool payload is limited
to 60,000 characters so it remains valid through the agent's result limit. Frames
start at 120–1200 pixels and automatically resize within that range.

Verification:

```bash
uv run pytest tests/unit/test_generative_ui.py -n 0
NODE_PATH=/tmp/tomo-pw/node_modules node tests/browser/intelligent_ui.cjs
```

The browser test renders a validated bill splitter inside the actual Tomo chat
page with mocked conversation history, checks interactions and isolation, and
writes desktop/mobile screenshots under `tmp/`.

Upstream revision and MIT attribution are recorded in
[the vendored asset README](../app/static/vendor/open-intelligent-ui/README.md).


## Maps and animated itineraries

The sandbox supports Leaflet 1.9.4, real USGS tile images, and sourced destination
photos. Load Leaflet's script from jsDelivr and fetch its CSS into an inline style
(the upstream map setup contract). Arbitrary API calls remain blocked; geographic
tiles are image requests. Show visible USGS attribution, load/error feedback, and
photo creator/license/source links. Connections illustrate a proposed itinerary;
they do not establish driving directions or current road conditions.

The upstream `window.createTripAnimator` controller provides `play`, `pause`,
`replay`, `seek`, and `dispose`. It respects reduced motion. Use inner numbered
pin elements so animation does not overwrite Leaflet's marker positioning.

Run the network-dependent renderer verification:

```bash
NODE_PATH=/tmp/tomo-pw/node_modules node tests/browser/intelligent_ui_map.cjs
```

It checks live tiles, actual Wikimedia photos, attribution, pause/replay,
stop selection, zoom, and mobile layout inside the actual Tomo chat page.
It uses a prepared, validated component and mocked chat history; it does not
verify a model generating the itinerary. Photo metadata and the example payload
are in `tests/browser/fixtures/coastal-*`. Screenshots are written under `tmp/`.


## Verified scene coverage

| Scene | Verification |
| --- | --- |
| 3D explanations | Real Three.js/WebGL rendering, pitch/roll/yaw axes, slider angles, reset, orbit dragging, stable camera during demonstrations, reduced motion, responsive sizing, and readable library-load failure. |
| Tables → charts | Native comparison table, user-clicked follow-up action, a new chart received over SSE, preserved source table, source values in agent context, and durable history replay. |
| Interactive tools | Bill total, group size, and tip changes recompute locally; invalid input shows an error, and rounding differences are allocated so shares sum to the total. |
| Maps | Live USGS tiles, credited Wikimedia photos, numbered pins, pause/replay, stop selection, zoom, and responsive layout. |

Run the scene tests:

```bash
uv run pytest tests/integration/test_chat.py -n 0
NODE_PATH=/tmp/tomo-pw/node_modules node tests/browser/intelligent_ui.cjs
NODE_PATH=/tmp/tomo-pw/node_modules node tests/browser/intelligent_ui_scenes.cjs
NODE_PATH=/tmp/tomo-pw/node_modules node tests/browser/intelligent_ui_map.cjs
```

The 3D and map tests load actual external libraries, tiles, and photos. Browser
conversation data and chart SSE are controlled fixtures. The agent integration
test uses the real runtime/tool/history stack with a scripted LLM provider.
These tests establish rendering and transport support, not the accuracy or
quality of arbitrary live-model-generated components. They do not imply full
CopilotKit/AG-UI/A2UI parity or progressive custom-component streaming.

# Tomo Browser Extension

Chrome MV3 extension that executes Tomo **browser.*** agent tools on the
user's device via `chrome.debugger` (CDP).

## Architecture

```
Tomo Agent → BrowserGateway → WebSocket → Tomo Web → extension → Chrome
```

The agent never talks to Chrome directly. Virtual `tab_*` ids are mapped to
Chrome tab ids only inside this extension.

## Install (dev)

1. Open `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → select this `extension/` directory.
3. Copy the extension **ID**.
4. Set env for Tomo:

   ```bash
   export TOMO_BROWSER_EXTENSION_ID=<extension-id>
   ```

5. Restart Tomo, open Chat, expand **Browser Control**, and connect.
6. By default **Control all tabs** is on (every normal http/https tab).  
   That preference is stored in ``chrome.storage.local`` and **survives** closing
   the popup / restarting the service worker. Turn it off in the popup to pick
   tabs one-by-one instead.

Privileged pages stay blocked either way: `chrome://`, `chrome-extension://`,
`devtools://`, `file://`, etc.

## Origins

`externally_connectable.matches` is intentionally narrow:

- `http(s)://127.0.0.1/*`, `localhost/*`
- `https://app.tomo.dev/*`, `https://staging.tomo.dev/*`

Add your deploy origin before shipping — never use `*://*/*`.

## Permissions

| Permission   | Why |
|-------------|-----|
| `debugger`  | CDP for snapshot / click / type / navigate |
| `tabs`      | List/authorize tabs |
| `storage`   | Session authorization set |
| `activeTab` | Popup “current tab” affordance |
| `scripting` | Inject control-wave overlay on the tab Tomo is driving |

While a tool runs, the controlled tab shows a top **wave bar + LIVE pill**
(“Tomo · Reading page”), similar to browser-agent UIs.

## V1 tools

`browser_tabs`, `browser_attach`, `browser_snapshot`, `browser_click`,
`browser_type`, `browser_press`, `browser_select`, `browser_scroll`,
`browser_navigate`, `browser_back`, `browser_forward`, `browser_wait`,
`browser_screenshot`, `browser_extract`

Not exposed: raw CDP, `eval` JS, cookies, network interception.

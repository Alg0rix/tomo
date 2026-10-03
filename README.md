<p align="center">
  <img src="app/static/mark.png" width="160" height="160" alt="Tomo">
</p>

# Tomo (友達)

**Tomodachi** — a general-purpose agent swarm that learns, coordinates, and acts on your behalf.

Tomo starts broad: a **coordinator** plus a team of agents that can talk to you, connect to your machines, use tools, and **get smarter over time**. You shape it into whatever you need — ops automation, customer support, research, coding, personal assistant — by adding agents, skills, and knowledge. The platform stays the same; the use case is yours to define.

---

## Why Tomo?

Most agent frameworks give you a chatbot or a coding copilot. Tomo gives you a **foundation**:

| Goal | How Tomo approaches it |
|------|------------------------|
| **General first** | One platform for any task — automate workflows, answer questions, run commands, manage files. Specialize later with skills and agent roles |
| **Agents that learn** | Memory, knowledge base, and a learning loop — after successful turns, a background review can distill skills/facts; agents also save mid-turn via tools |
| **Swarm coordination** | Opt into a team per message. The coordinator creates session-local specialists or uses configured agents, schedules dependent tasks, shares findings, and combines results |
| **Talk from anywhere** | Web UI today; Telegram bot available (token in Settings); WhatsApp planned |
| **Reach any machine** | Workplaces over WebSocket tunnel, SSH, or local path jail — same tools everywhere |
| **Easy to extend** | Tools = JSON schema + Python backend (register in the tool registry); skills are filesystem playbooks |

---

## Getting started

Tomo's **Alpha is live** — SQLite store, multi-model profiles, swarm delegation, bash/file tools on path-jailed or remote workplaces, Markdown vault memory, interval scheduler, and Telegram (Settings). Configure models in System → Models; chat over SSE from the dashboard or Chat page.

Chats start with one agent. To use a team in a new or existing chat, turn on **Team** for the next message or ask for multiple agents in the message itself. Tomo may suggest a task split for an ordinary request; reply **gas** or **ya** to approve it. The bundled policy is the [Tomo skill](skills/internal/tomo/SKILL.md) (`references/swarm.md`), alongside skills from `~/.agents/skills`. A team run can mix configured agents with specialists that exist only in that chat. The coordinator selects enabled tools for each task, including `bash` and `portal` where appropriate. The chat shows actual task progress instead of a generic swarm label.

Active web and Telegram tasks automatically continue after Tomo restarts, using
the saved conversation and tool results. Telegram replies return to the original
chat/topic, with the original sender retaining control. Stopped and finished tasks
are not rerun. Pending approvals must be requested again; a tool interrupted before
its result was saved has an unknown outcome, so the agent must check its effects
before retrying it. Recovery requires the same Tomo database and, for Telegram,
the same bot token with the chat still allowed. Waiting Telegram queue items are currently
kept in memory and do not survive a restart.

### Install (Linux, systemd user)

Recommended for a lasting install. Requires `git`; installs `uv` if missing.

```bash
curl -fsSL https://raw.githubusercontent.com/Alg0rix/tomo/main/scripts/install.sh | bash
# from a checkout: bash scripts/install.sh
# options: --no-start   --branch NAME
```

| Path | Role |
|------|------|
| `~/.local/share/tomo/app` | Managed git checkout + `.venv` (code) |
| `~/.config/systemd/user/tomo.service` | User unit (`WorkingDirectory` = install tree) |
| `~/.local/bin/tomo` | CLI symlink |
| `~/.tomo` (`$TOMO_HOME`) | Config, DB, secrets |
| `~/tomo` (`$TOMO_WORK`) | Per-agent tool workspaces |

The unit sets `TOMO_HOME` and `TOMO_WORK` explicitly. UI: [http://127.0.0.1:8787](http://127.0.0.1:8787).

```bash
tomo update                 # fetch + ff-only (or hard reset) + uv sync + restart
                            # also: System → Instinct → Update (script install, not Docker)
tomo service status|start|stop|restart
tomo uninstall              # remove service + code; keep data
tomo uninstall --purge -y   # also delete ~/.tomo and ~/tomo
journalctl --user -u tomo -f
```

Headless hosts: `loginctl enable-linger $USER` so the unit survives logout.

### Deploy with Docker Compose

```bash
cp .env.example .env   # set TOMO_SESSION_SECRET + TOMO_ADMIN_PASSWORD
docker compose up -d   # pulls ghcr.io/alg0rix/tomo:latest
# or: docker compose up -d --build
# UI: http://127.0.0.1:8787
```

Images are published to GHCR on `main` and `v*` tags. Persists `$TOMO_HOME` /
`$TOMO_WORK` in named volumes. Two variants: `latest` (slim) and `full`
(LibreOffice, ffmpeg, CloakBrowser headless browser, Node.js, and common
agent CLI tools — `TOMO_IMAGE=ghcr.io/alg0rix/tomo:full`).
Full guide: [docs/deployments.md](docs/deployments.md).

### Install connector (tunnel workplaces)

On each remote device (or re-run to **update**):

```bash
curl -fsSL https://raw.githubusercontent.com/Alg0rix/tomo/main/scripts/install-connector.sh | bash
tomo-connector pair --code X7KQ2M --server https://your-coordinator.example.com
tomo-connector service install   # systemd --user (Linux)
```

Downloads the matching binary from [`latest` release](https://github.com/Alg0rix/tomo/releases) into `~/.local/bin`. Pin with `TOMO_CONNECTOR_VERSION=v0.1.3`. Create the pairing code in the UI (Workplaces → New → tunnel). Details: [Machine connectivity](#machine-connectivity).

### Skills

Skills are folders with a `SKILL.md` (agentskills.io style). Tomo discovers:

| Path | Role |
|------|------|
| `<install>/skills/internal` | Bundled [Tomo](skills/internal/tomo/SKILL.md) skill (read-only) |
| `$TOMO_HOME/library/skills` | Managed installs |
| `~/.agents/skills` | Shared user skills (default) |
| `~/.agent/skills` | Alternate shared path |
| `~/.tomo/skills` | Tomo-home peer skills dir |
| `~/.claude/skills` | Claude Code skills (often symlinks) |

```bash
tomo skills sync
tomo skills list
tomo skills install ./my-skill-dir
tomo skills uninstall <id>     # library installs only
```

Override external roots with `TOMO_SKILLS_EXTERNAL_DIRS` (colon-separated; empty disables). Agents load skill bodies via `list_skills` / `use_skill` (optional `file=` for package support docs).

### Develop from source

```bash
git clone https://github.com/Alg0rix/tomo.git
cd tomo
uv sync
uv run python -m app.main   # http://127.0.0.1:8787
```

Do not edit the managed install tree for day-to-day development — use a normal clone. `tomo update` always targets `~/.local/share/tomo/app`.

### Chat workspace & local terminals

Open **Workspace** in a chat to browse files/artifact previews or use **Terminal → New terminal**. Each chat owns up to eight independent shells. They run directly on the **Tomo host**, never through a tunnel/connector; a remote browser does not run commands on its own device. The starting directory is the chat's local workplace, or `$TOMO_HOME/sessions/<id>/workspace` when no local workplace is selected.

Switching chats, closing the panel, or reloading the browser keeps shells and their recent output alive. A visible countdown closes terminals after **30 minutes idle**: input/output resets it, and running foreground commands pause it. Closing a terminal manually, deleting its chat, or shutting down Tomo stops the shell and its jobs. Shell exit is shown in the tab; use New terminal to start another. Terminals do not survive a Tomo restart.

Local terminals require a POSIX host and Tomo's default **single server worker**. Access requires the chat owner's login; commands have the permissions of the Tomo process. xterm.js and its fit addon are bundled locally, so the renderer needs no CDN connection.

Browser regression: with Playwright/Chromium installed, run `node tests/browser/local_terminals.cjs`. It starts an isolated Tomo instance with a temporary database; `CHROME_PATH` can select an existing Chromium binary and `TOMO_BROWSER_ARTIFACTS` selects the screenshot folder.

### Configuration

**Tomo Home (`$TOMO_HOME`)** — writable config/state root (default `~/.tomo`).
**Tomo Work (`$TOMO_WORK`)** — agent tool cwd root (default `~/tomo`; per agent `$TOMO_WORK/<agent_id>`). Separate from Home; the systemd unit sets both. There is no `TOMO_WORKDIR`.

Set `export TOMO_HOME=/path/to/tomo` (and optionally `TOMO_WORK`) to relocate. On first start the Home tree is created and seeded from the shipped `defaults/`:

```text
$TOMO_HOME/
├── tomo.yaml          # non-secret prefs only (never API keys / master key)
├── .env               # bootstrap secrets (TOMO_SESSION_SECRET, TOMO_ADMIN_PASSWORD; install/update/start seed if missing)
├── .secret_key        # master key for at-rest encryption (chmod 600; auto-created)
├── SOUL.md            # global default persona
├── library/{skills,memory}
├── agents/<id>/{SYSTEM.md,SOUL.md}
├── memory/vault/<user-id>/{entities,timeline,index.md}
├── workplaces/
└── state/tomo.db      # SQLite (secret settings encrypted at rest)
```

Agent tool cwd is **not** under Home — it is `$TOMO_WORK/<agent_id>` (default `~/tomo/<id>`), or a bound local workplace root.

Persona/prompt files use the familiar names `SOUL.md` (persona) and `SYSTEM.md`
(agent system prompt). Durable facts use `memory/vault/<user-id>/entities/<type>/<slug>.md`. Edit them under `$TOMO_HOME` to customize Tomo without
touching the git tree; the coordinator loads `$TOMO_HOME/SOUL.md` plus each
agent's `SYSTEM.md` / `SOUL.md` and reads vault context live at turn time.

**Secrets policy** — UI-managed secrets (LLM API key, …) are stored **encrypted
at rest** in the SQLite `settings` table, never as plaintext. A master key
encrypts/decrypts them (Fernet, `cryptography`):

- **Master key sources** (first match wins): process env `TOMO_SECRET_KEY`
  (preferred for containers / CI), else `$TOMO_HOME/.secret_key` (auto-created
  on first run, `chmod 600`, never overwritten).
- `tomo.yaml` never holds secrets or the master key.
- GET settings returns **masked** values + `*_set` flags; a blank PUT keeps the
  existing key. Decrypted secrets never travel over HTTP/HTML.
- **Back up `.secret_key` / `TOMO_SECRET_KEY`** with the same care as the DB —
  losing it makes encrypted secrets unrecoverable.
- `$TOMO_HOME/.env` (dotfile, `0600`) holds plaintext bootstrap values
  (`TOMO_SESSION_SECRET`, `TOMO_ADMIN_PASSWORD`). `scripts/install.sh`,
  `tomo update`, and process start seed missing keys (never overwrite). Loaded
  with `override=False` (process env wins). Prefer moving durable secrets into
  encrypted SQLite via the UI. Never name it `secrets.env`.

**LLM** — open **System → Models → New profile**. Choose **OpenCode Go** or
**OpenCode Zen**, paste your token, and save: the live model catalog becomes
available in chat. **Custom endpoint** still accepts a Base URL, API key, and
model ID (e.g. `gpt-4o-mini`). Keys are encrypted before touching SQLite.

The composer’s model picker works before the first message and saves model /
reasoning choices per chat. Changes apply to the next turn; swarm workers keep
their separately assigned profiles. **Follow main model** clears the chat override.
Under **System → General**, background tasks (memory extraction/consolidation,
chat titles, compaction, learning review, smart approvals, agent generation, and
suggested prompts) default to **Follow main model**, with optional overrides.
Chat-related tasks follow that chat’s model; global tasks follow the default.
Vision follows the main model when it supports images, otherwise uses a
vision-capable fallback. Max tool iterations also live under **System → General**.

**Database** — state lives in SQLite at `$TOMO_HOME/state/tomo.db` by default
(`TOMO_DB_PATH` / `TOMO_VAR_DIR` override). The directory and DB are created on
first run. To keep a legacy `var/tomo.db`, set
`export TOMO_DB_PATH=var/tomo.db` (there is no automatic migration).

**Server** — `TOMO_HOST` (default `127.0.0.1`), `TOMO_PORT` (default `8787`),
`TOMO_RELOAD` (default `false`). Session cookies are signed with
`TOMO_SESSION_SECRET` (managed installs auto-generate into `$TOMO_HOME/.env`).
Bootstrap admin password: `TOMO_ADMIN_PASSWORD` (same). Non-loopback bind
refuses known insecure defaults. Note: `TOMO_SECRET_KEY` is the **at-rest
master key** (see Secrets policy), not the session secret.

**MCP servers** — open **System → MCP** to add [Model Context Protocol](https://modelcontextprotocol.io)
servers, distinct from the [Connector tunnel](#machine-connectivity) above
(the Connector bridges Tomo to a remote *machine*; MCP servers give agents
new *tools*, resources, and prompts from a local command or a remote
endpoint):

- **`stdio`** — a local command Tomo launches directly (argument vector,
  never a shell string), e.g. `npx @modelcontextprotocol/server-filesystem`.
- **Streamable HTTP** — a remote MCP endpoint, reached with optional custom
  headers (bearer tokens, API keys).

Every environment value (`stdio`) and header value (Streamable HTTP) is
encrypted at rest the same way as the LLM API key. A server can be disabled
without losing its discovered tools/resources/prompts, and each capability
can be toggled independently of the server. Per-agent tool enablement (Agent
Studio → Tools) is layered on top and controlled the same way as built-in
tools — disabling a server or a capability globally always wins over a
per-agent "on".

### Tests

```bash
uv run pytest                         # parallel (pytest-xdist -n auto)
uv run pytest -m "not integration"    # fast local loop
uv run pytest --lf                    # last failures only
uv run pytest -n0                     # serial (pdb / debugging)
uv run ruff check app cli tests       # same rules as CI lint
```

CI workflows:

| Workflow | When | What |
|----------|------|------|
| [`ci.yml`](.github/workflows/ci.yml) | push/PR + `v*` tags | pytest (3.12/3.13), Python wheel, connector cross-builds; `v*` tags only → GitHub Release + GHCR `ghcr.io/alg0rix/tomo` |
| [`lint.yml`](.github/workflows/lint.yml) | push/PR | ruff (E/F), `gofmt`/`go vet`, `bash -n` on install scripts |
| [`security.yml`](.github/workflows/security.yml) | push/PR + weekly | `pip-audit` on the lockfile, CodeQL (Python + Go) |

Dependabot (`.github/dependabot.yml`) opens weekly PRs for `pip`, `gomod`, and Actions.
> **Note:** Alpha (slices 0→H) is complete. Connector, learning loop, memory (Markdown vault + FTS), portals, interval scheduler, and Telegram (code) are implemented — see Roadmap. Next: richer channels (WhatsApp, multi-agent routing, media tools).

---

## Architecture

```
   Telegram*   WhatsApp†    Web UI
       │           │           │
       └───────────┴─────┬─────┘
                         ▼
              ┌──────────────────────┐
              │       Channels       │
              │   (your interface)   │
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │     Coordinator      │
              │    (routes work)     │
              └──────────┬───────────┘
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
 ┌───────────┐     ┌───────────┐     ┌───────────┐
 │  Agent A  │     │  Agent B  │     │  Agent C  │
 └─────┬─────┘     └─────┬─────┘     └─────┬─────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
              ┌──────────────────────┐
              │ Tools · Skills ·     │
              │ Memory · KB          │
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │      Workplaces      │
              │ local · tunnel · ssh │
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │ Hosts · edge devices │
              └──────────────────────┘
```

\* Telegram: implemented (Settings token + long-poll). † WhatsApp: planned.

**Channels** — how users reach agents. Web UI today; Telegram available; WhatsApp later — same agent underneath.

**Coordinator** — routes tasks, manages agent lifecycle, and tracks state across the swarm.

**Agents** — independent workers. Each has its own model, tools, skills, and channels; account-scoped vault facts. You define their role when you need to — or leave them general-purpose until a pattern emerges.

**Memory & learning** — agents remember past conversations, store facts in the Markdown vault, and can distill repeated workflows into skills (mid-turn tools + background review). See [Learning](#learning-agents-that-get-smarter).

**Tools** — atomic actions (run a script, edit files, search the web). JSON schema + Python backend registered in the tool registry.

**Skills** — higher-level playbooks composed from tools + prompts. Install from disk or let agents distill them from experience.

**Workplaces** — where execution happens. Local path jail, WebSocket tunnel, or SSH — agents don't care which transport is used.

---

## Learning — agents that get smarter

Tomo agents don't reset every session. They **learn** — from you, from each other, and from their own runs.

```
  Observe          Distill           Reuse            Refine
     │                │                 │                │
  multi-step    ──►  skill/KB     ──►  next time    ──►  feedback
  task runs         entry              load skill        improves it
```

### What agents remember

| Layer | What it stores | Example |
|-------|----------------|---------|
| **Markdown vault** | Per-account entity pages and timelines; memory add/list/search/replace/remove | Profiles, agent lessons, projects, people, tools, and uploaded references |
| **Conversation memory** | Turns and summaries; session_search | Past task discussions |
| **Experiences** | Structured episodes; record_episode / recall_episodes | What worked and failed |
| **Artifacts** | Per-session files | Reports and exports |
| **Skills** | Reusable procedures | How-to playbooks |
| **Agent state** | Structured KV | Machine-readable keys |

All durable facts live under `$TOMO_HOME/memory/vault/<user-id>/`. Markdown is authoritative; SQLite indexes are rebuildable. There is no storage character quota. Profile and matching facts are read each turn with bounded context excerpts. Use `memory` with `entity=type/slug`, including `user/profile` for identity/preferences. The Memory page supports inspection, correction, moving and forgetting facts. System → Memory saves facts and uploads references directly to vault pages.

Startup performs a one-way migration of old profile/agent/workplace files and SQLite KB rows, verifies their content, then deletes the source files and drops the KB tables. Retired tools and endpoints are removed. Automatic turn timelines and nightly consolidation remain configurable.

### The learning loop

1. **Observe** — agent completes a turn (tool calls, decisions, your corrections)
2. **Distill** — a background review (counter-based nudges, not similarity search) may call `memory` / `manage_skill` when durable prefs or procedures appear; agents can also save mid-turn
3. **Reuse** — vault facts are refreshed each turn; matching skills can be loaded with `use_skill`
4. **Refine** — later reviews can patch skills; you can edit or delete the Markdown / skill files by hand

Skills are inspectable files — not black-box weight updates. Toggle **Settings → Learning loop** to enable/disable the background distill pass (agents can still call `manage_skill` / `memory` mid-turn).

### Cross-agent learning

Knowledge isn't siloed. Agents in the swarm can:

- Inspect peers with **`agent_info`** — roster (`action=list`), or one agent's enabled tools, linked/shared skills, account vault facts and agent state (`action=get`)
- **`delegate`** subtasks to the agent best suited for them
- Share **artifacts** and **portal** files across workplaces
- Use the **shared** skill catalog (`list_skills`); each agent has its own tool allowlist, and vault facts are account-scoped

A general-purpose agent might hand off to a specialist — or load a skill another agent created.

---

## Use cases — you decide

Tomo ships as a **general platform**. These are examples of what you can build — not built-in modes:

| Use case | What you'd configure |
|----------|---------------------|
| **Personal assistant** | One agent, Telegram channel, calendar/search tools, personal KB |
| **Team ops** | Ops agent + tunnel workplaces, bash/runpy, deploy skills |
| **Customer support** | Support agent, WhatsApp channel, FAQ knowledge base, ticket tools |
| **Research** | Research agent, web fetch tools, artifact storage, summarization skills |
| **Coding** | Dev agent, file read/write/patch tools, codebase KB — same primitives as [OpenHands](https://github.com/All-Hands-AI/OpenHands) or [Aider](https://github.com/Aider-AI/aider) |
| **Multi-domain swarm** | Coordinator routes to specialized agents; each learns its own domain |

Start with one general agent. Add tools, skills, and roles as your needs become clear.

---

## Machine connectivity

SSH is one option — not the only one. Tomo uses **Workplaces**: a unified execution environment that hides the transport layer from agents.

```
Agent (coordinator)  ──── WebSocket (outbound) ──── Tomo Connector (your device)
        │                                                    │
        │                                                    ├── bash / python
        │                                                    ├── file read/write
        └── same tools work everywhere ──────────────────────┘
```

### Three connection modes

| Mode | Best for | How it works |
|------|----------|--------------|
| **Local** | Dev, same-host work | `bash` / `runpy` / file tools as a **path-jailed host process** under `$TOMO_WORK/<agent>` or a bound local workplace root (not Docker) |
| **Tunnel** | Home labs, edge devices, NAT/firewall | Lightweight connector binary makes an **outbound WebSocket** to the coordinator — no public IP, no port forwarding, no SSH |
| **SSH** | Existing servers, jump hosts | Auto-connect with stored credentials; same tools, traditional transport |

### Why WebSocket tunnel is the default recommendation

SSH requires inbound access, key management, and often manual setup per host. A **reverse WebSocket tunnel** flips the model:

- **Device initiates** the connection outbound → works behind NAT, home routers, corporate firewalls
- **Pair once** with a short-lived code; connector auto-reconnects on restart
- **Same tool surface** — `bash`, `runpy`, file ops — regardless of whether the workplace is local, tunneled, or SSH
- **Persistent channel** — coordinator always knows which devices are online (green/red status)

Install + pair steps live under [Getting started → Install connector](#install-connector-tunnel-workplaces). On the coordinator, create a tunnel workplace and copy the pairing code (or `POST /api/workplaces/{id}/pairing-code`).

Once paired, agents assigned to that workplace run `bash` / file tools on the device as if they were local. Status is green only while the WebSocket is live. To build from source: `cd connector && make build`.

### SSH as a fallback

For machines that already have SSH and you don't want to install a connector, Tomo supports **SSH workplaces** with stored credentials. Same tool surface as local/tunnel.

### Portals — file bridge across workplaces

The **portal** system lets agents copy files between workplaces and the coordinator workspace (`/_portal/<name>/...`), including large binary transfers with background jobs and progress polling. Handy for pulling build artifacts off a remote device or pushing configs to an edge node.

Agents use the `portal` tool:

```
# Pull a build off a tunnel/SSH/local workplace into coordinator staging
portal action=copy src=<workplace_id>:dist/app.tar.gz dst=/_portal/edge/app.tar.gz

# Push a staged config onto another host
portal action=copy src=/_portal/edge/app.toml dst=<workplace_id>:etc/app.toml

# Large files return a transfer id — poll until done
portal action=status id=xfer_1
portal action=list
portal action=cancel id=xfer_1
```

Locations are either `/_portal/<name>/relative/path` (on the Tomo host under `$TOMO_WORK/_portal/`) or `<workplace_id|name>:<path>`. Small copies finish inline; larger ones run in the background with byte progress.

---

## Channels — talk to your swarm from anywhere

> **Alpha:** Web UI is live. Telegram bot is implemented (Settings token + long-poll; e2e not fully verified). WhatsApp and other adapters remain planned.

Tomo agents aren't locked to a terminal. **Channels** bridge your swarm and the messaging apps you already use.

```
You (Telegram)  ──►  Channel  ──►  Agent  ──►  Workplace
     📱                  │            │              🖥️
                    same agent,     tools · skills
                    any interface   · memory · KB
```

The agent doesn't change underneath. You pick the interface; Tomo handles routing, sessions, and replies.

### Example flow

1. Message on Telegram: *"Check disk on the staging tunnel and summarize."*
2. Your agent uses tools on that workplace and replies on Telegram
3. (Planned) attachments / voice / multi-agent routing on one bot

### Channels today

| Channel | Status | Notes |
|---------|--------|-------|
| **Web UI** | ✅ Alpha | Dashboard, chat with optional team runs (SSE), agent studio |
| **Telegram** | ✅ Code shipped | Bot token in System → Channels; long-poll. Currently routes to the **coordinator**. E2e not fully verified. |
| **WhatsApp** | 🔜 Planned | WhatsApp Web bridge |
| **Discord / Slack** | 🔜 Planned | |
| **CLI as chat channel** | 🔜 Planned | Today's `tomo` CLI is install/update/skills only |

### The mobile pattern

**You're on your phone; the swarm runs on a server.** Chat from Telegram while work happens elsewhere. No laptop required.

### Access control (planned)

Each channel will support two modes:

| Mode | Who can chat |
|------|--------------|
| **Open** | Anyone who messages the bot |
| **Restricted** | Only users on the allowlist (default for new channels) |

Restricted mode will use **pairing codes** — a short code a new user receives on first contact; an admin approves it onto the allowlist. *(Not shipped yet. Workplace connector pairing codes are separate and already work.)*

### Multi-agent on one channel (planned)

Today a Telegram bot talks to the **coordinator**. Planned: one bot/number serving a team of agents, with routing by skills/availability (or `@mention`).

### Channel-native tools (planned)

Planned messaging tools (not in `app/tools/` yet):

- **send_file** — deliver PDFs, images, spreadsheets as attachments
- **read_attachment** — process files users upload via chat
- **transcribe_audio** — voice memos → text
- **describe_image** — vision on photos sent in chat

Web and Telegram chats support session attachments. Telegram receives photos, documents, voice/audio, video, animations, and stickers, with a 20 MB download bound and album grouping. Enable voice transcription in **System → Channels** and configure a compatible speech service to turn audio into a user message. Original files remain attached to the conversation.

Enable **Rich answers** in the same panel for native tables, headings, lists, and fenced math blocks. Private and group chats stream into one editable preview; tool commentary appears once, and the final answer arrives as a new message at the bottom. Definite API rejections fall back to balanced HTML, while ambiguous failures avoid duplicate sends. Telegram turns also get channel-specific context and a `telegram_send_file` tool: save a screenshot/report with `save_artifact`, then upload its filename directly to the same chat/topic instead of sending web-only image tags or Files-panel references. See [Telegram interaction UX](docs/telegram-ux.md) for configuration and limits.

Schedules created in Telegram retain their chat/topic and automatically deliver final replies there, with file delivery available during the job. Completed finals survive restart; delivery status and receipts are separate from execution status. Web schedules remain local. See [Scheduled channel delivery](docs/scheduled-delivery.md) for recovery guarantees and the provider contract for adding channels.

Sessions are keyed by `(agent_id, user_id)` (Telegram uses `tg_<chat_id>` as the user). Separate histories per chat identity.

### Primary channel (planned)

Each agent will be able to designate a **primary** channel for proactive outbound alerts (task done, escalation, cron). Not implemented yet — replies stay on the channel that received the message.

### Adding a channel

**Today:** configure Telegram under **System → Channels** (encrypted token; blank PUT keeps existing). Enable long-poll, save, and send `/id` to the bot. Add the returned ID to **Allowed chat IDs** and save again. An empty list blocks all agent access; `/id` still works without running the model. Approving a group ID allows every member of that group to use Tomo.

To share your web account's memory, open **System → Accounts**, inspect your account, select **Link Telegram**, and send the generated `/link <code>` in a private DM to the bot. The code expires in 10 minutes and is single-use. Linking uses the immutable account ID—not the username—and merges existing Telegram memory with backups while keeping web and Telegram conversations separate. Groups cannot be linked to personal accounts. See [Telegram identity linking](docs/telegram-ux.md#link-a-telegram-identity-to-a-login-account).

Approved chats can use `/help`, `/new`, `/stop`, `/status`, `/manual`, and `/smart`. Tasks show typing, an editable activity card with tool and plan progress, streamed formatted answers, and inline approval / clarification controls. Always allow requires a separate confirmation; controls are bound to the initiating person and prompt. New conversations retain approval mode and preserve old histories. See [Telegram interaction UX](docs/telegram-ux.md) for behavior and validation. Administrators see Telegram conversations in **Chat**, labeled with their chat ID, and can open and search their histories. The enabled badge describes configuration, not a verified connection. Existing installations must add their allowed chat IDs before using the bot.

**Planned CLI** (not implemented):

```bash
# Not shipped — use System → Channels in the UI for Telegram today
# tomo channel add --agent main --type telegram --token "…"
# tomo channel approve XK4M9Q
```

---

## Extending Tomo

Adding a new capability is intentionally boring (in a good way).

### 1. Define a tool

1. Add `app/tools/<name>.json` (OpenAI-style function schema + `backend` module path).
2. Implement `run(arguments) -> str` in `app/runtime/tools/<name>.py`.
3. Register the backend in `app/runtime/tools/registry.py` (`_BACKENDS`).

JSON alone is not enough today — the registry map is still explicit (dynamic import is later).

### 2. Assign it to an agent

Enable the tool in an agent's Tools panel (or seed allow-list). Peers can discover allowlists with `agent_info`.

### 3. Optionally add a skill

Skills bundle prompts (+ optional scripts) into reusable workflows. Write them yourself, `tomo skills install`, or let agents distill them via `manage_skill` / the learning loop.

---

## Built-in tools (starter set)

General-purpose primitives — enable per agent based on your use case:

| Tool | Purpose |
|------|---------|
| `bash` / `runpy` / `process` | Shell, Python, background jobs |
| `read_file` / `write_file` / `str_replace` / `patch` / `delete_file` / `search_files` / `list_dir` | File ops under the agent sandbox / workplace |
| `web_fetch` / `web_search` | Fetch URLs and search the web |
| `todo` / `session_search` | Lightweight todos and message search (FTS) |
| `list_skills` / `use_skill` / `manage_skill` | Browse, load, and distill skill playbooks |
| `memory` | Account vault facts: add, list, search, replace, remove |
| `list_workplaces` / `agent_info` / `register_workplace` / `create_agent` | Workplaces, peer inspect, register local path, spawn agents |
| `portal` | Copy files across workplaces via `/_portal/<name>/...` (async + progress) |
| `clarify` / `memory` | Ask the user / save, search, correct and remove vault facts |
| `agent_state` / `save_artifact` / `list_artifacts` / `fetch_artifact` | Searchable KB, KV state, durable artifacts |

| `delegate` | Hand a subtask to another agent |

See the `app/tools/` directory for the full catalog.

---

## Project structure

Tomo separates **HTTP surfaces**, **runtime execution**, **persistence**, and **installable extensions**. Alpha wires FastAPI surfaces to SQLite models, runtime loop, tools, channels, and workplaces.

```
tomo/
├── app/                          # FastAPI application
│   ├── main.py                   # App factory + uvicorn entry
│   ├── core/                     # Config, auth, Jinja globals
│   ├── api/                      # JSON REST + SSE (/api/…)
│   ├── web/                      # HTML pages + login
│   ├── schemas/                  # Pydantic request/response models
│   ├── models/                   # DB layer (schema, mixins) — SQLite
│   ├── runtime/                  # Agent execution core (loop, LLM, tools)
│   │   ├── coordinator/          # Swarm routing and delegation
│   │   ├── agent/                # LLM turn loop, context, learning
│   │   ├── memory/               # Markdown vault / FTS / retrieval
│   │   ├── portal/               # Cross-workplace file bridge
│   │   ├── events/               # Event bus stub (placeholder)
│   │   └── tools/                # Built-in Python tool backends
│   ├── tools/                    # Declarative tool JSON (schema + backend ref)
│   ├── channels/                 # Web + Telegram (WhatsApp later)
│   ├── workplaces/               # Local / SSH / tunnel hub + pairing
│   ├── extensions/               # Installable skill loader
│   ├── plugins/                  # Live plugin runtime, SDK, and catalog management
│   ├── services/                 # Store facade + chat/SSE
│   ├── static/                   # CSS + JS (Darkroom UI)
│   └── templates/                # Jinja pages + partials/
│
├── cli/                          # `tomo` CLI (update, uninstall, service, skills, plugins)
├── skills/                       # Installable skill packages
├── skillsets/                    # Preset agent profiles (JSON)
├── defaults/                     # Shipped prompts and KB seeds
├── evaluator/                    # LLM evaluation engine — stub (UI hidden; TOMO_EVAL_UI)
├── connector/                    # Go tomo-connector (WebSocket tunnel agent)
├── tests/                        # unit/ + integration/
├── scripts/                      # install.sh + release helpers
├── Dockerfile                    # Coordinator image (Compose)
├── docker-compose.yml            # Local / server Compose stack
├── docs/                         # Architecture + deployments
├── seed/                         # Dev database seeds
├── tmp/                          # Local scratch (gitignored)
└── var/                          # Runtime state (gitignored)
```

### Layer guide

| Layer | Path | Role |
|-------|------|------|
| **Surface** | `app/api/`, `app/web/` | HTTP APIs and server-rendered UI |
| **Contracts** | `app/schemas/` | API validation and serialization |
| **Persistence** | `app/models/` | SQLite via mixins |
| **Runtime** | `app/runtime/` | Coordinator, agent loop, memory, portals, tools |
| **Integrations** | `app/channels/`, `app/workplaces/` | Messaging and execution environments |
| **Extensions** | `skills/`, `app/plugins/`, `app/extensions/` | Skills + live plugins |
| **Ops** | `cli/`, `scripts/` | Install/update/uninstall, systemd user unit, local tooling |

**Today:** `app/services/store.py` is a facade over SQLite mixins (`app/models/`). Runtime, channels, and workplaces are wired for the Alpha demo path.

See `app/tools/` for declarative tool definitions; Python implementations go in `app/runtime/tools/` and must be listed in the registry.

---

## Roadmap

- [x] Alpha — home, models, swarm handoff, tools, workplaces, KB, Web UI
- [x] Learning loop — mid-turn tools + background review (counter nudges); `manage_skill` / `memory`
- [x] Memory engine — Markdown vault + rebuildable FTS5 indexes
- [x] Portals — file bridge across workplaces with chunked binary + progress
- [x] Tomo Connector — WebSocket tunnel agent for remote workplaces (Go `connector/`)
- [x] Interval scheduler — SQLite schedules + background runner + UI
- [x] Telegram channel — Settings token + long-poll (routes to coordinator; e2e polish TBD)
- [ ] Channel adapters — WhatsApp, Discord, Slack; CLI-as-chat; multi-agent routing; media tools
- [x] Skills — filesystem discover (`~/.agents/skills` + library), install CLI, `use_skill` body load
- [ ] Skill registry — community marketplace / remote install
- [ ] Observability — traces, cost tracking per agent
- [ ] Eval / evaluator UI (gated today via `TOMO_EVAL_UI`)
- [ ] Local Docker isolation (today: path-jailed host process)

---

## Philosophy

**友達 (tomodachi)** means *friend* in Japanese. Tomo agents are collaborators that learn your world — not oracles behind a single prompt box.

Start general. Add one agent, one channel, a few tools. Let it learn your workflows. When patterns emerge, split into specialized agents, write skills, grow the knowledge base. The platform doesn't dictate your use case — you shape it over time.

Build the swarm. Let it learn. Let the coordinator handle the rest.

---

## License

Apache License 2.0. See [LICENSE](LICENSE) for the full text.

Copyright 2026 Tomo contributors

### Live plugins

Build trusted Python plugins with multiple pages and tools that Tomo agents can use.
Open **Plugins** to install, enable, disable, reload, or update them without restarting
the server. See [the plugin guide](docs/plugins.md) and the
[Money plugin](https://github.com/Alg0rix/tomo-plugins/tree/main/plugins/money).

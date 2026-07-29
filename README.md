# Tomo (友達)

> **Alpha is complete** — end-to-end demo path works: home chat → swarm handoff → tools on workplace → recall → schedule. Eval UI stays gated (`TOMO_EVAL_UI=1` to re-enable).

**Tomodachi** — a general-purpose agent swarm that learns, coordinates, and acts on your behalf.

Tomo starts broad: a **coordinator** plus a team of agents that can talk to you, connect to your machines, use tools, and **get smarter over time**. You shape it into whatever you need — ops automation, customer support, research, coding, personal assistant — by adding agents, skills, and knowledge. The platform stays the same; the use case is yours to define.

---

## Alpha

What ships in Alpha (slices 0→H):

| Area | Status |
|------|--------|
| **`$TOMO_HOME`** | Tree + encrypted secrets (`SOUL.md` / `SYSTEM.md`, `.secret_key`) |
| **Models** | Multi-profile catalog, default + per-agent model |
| **Swarm** | `@mention` and `delegate` handoff in chat (SSE) |
| **Tools** | `bash`, `read_file`, `write_file`, `str_replace`, `search_files`, `delete_file`, `web_fetch`, `web_search`, `process`, `todo`, `session_search`, `list_skills`, `use_skill`, `clarify`, `forget_memory`, `recall`, `remember`, `delegate` |
| **Workplaces** | Local + SSH + **Tomo Connector** (WebSocket tunnel) |
| **Memory / KB** | SQLite knowledge entries + recall/remember tools |
| **Channels** | Web UI (ready); Telegram/WhatsApp planned |
| **Scheduler** | Not ready — design in progress; interval schedules not wired |
| **Platform** | Skills/plugins/schedules in SQLite |
| **Eval** | Hidden by default (`TOMO_EVAL_UI`) |

**Demo path:** Dashboard home chat → session with Main+Ops handoff → tools on a workplace → ask a KB fact → Telegram ping → create a short-interval schedule.



---

## Why Tomo?

Most agent frameworks give you a chatbot or a coding copilot. Tomo gives you a **foundation**:

| Goal | How Tomo approaches it |
|------|------------------------|
| **General first** | One platform for any task — automate workflows, answer questions, run commands, manage files. Specialize later with skills and agent roles |
| **Agents that learn** | Memory, knowledge base, and a learning loop — agents observe tasks, distill reusable skills, and improve from feedback across sessions |
| **Swarm coordination** | Multiple specialized agents delegate to each other; the coordinator routes work without a single bottleneck |
| **Talk from anywhere** | Web UI today; Telegram and WhatsApp planned — message your swarm from your phone while it works on a server |
| **Reach any machine** | Workplaces over WebSocket tunnel, SSH, or local sandbox — same tools everywhere |
| **Easy to extend** | Tools and skills are declarative — drop in JSON definitions, assign to agents, done |

---

## Architecture

```
   Telegram    WhatsApp     Web UI      CLI
       │           │           │          │
       └───────────┴─────┬─────┴──────────┘
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
              │ Machines · Docker    │
              └──────────────────────┘
```

**Channels** — how users reach agents. Telegram, WhatsApp, web UI — same agent underneath, any interface you prefer.

**Coordinator** — routes tasks, manages agent lifecycle, and tracks state across the swarm.

**Agents** — independent workers. Each has its own model, tools, skills, memory, knowledge base, and channels. You define their role when you need to — or leave them general-purpose until a pattern emerges.

**Memory & learning** — agents remember past conversations, store facts in a knowledge base, and can turn repeated workflows into reusable skills. See [Learning](#learning-agents-that-get-smarter).

**Tools** — atomic actions (run a script, query data, send a file, search the web). Declarative JSON — add what your use case needs.

**Skills** — higher-level playbooks composed from tools + prompts. Install community skills or let agents create their own from experience.

**Workplaces** — where execution happens. Local sandbox, WebSocket tunnel, or SSH — agents don't care which transport is used.

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
| **Conversation memory** | Recent turns, summarized history | "Last week we discussed the Q3 budget" |
| **Knowledge base** | Documents, notes, wiki-style links | Company policies, API docs, personal preferences |
| **Artifacts** | Files and outputs from past tasks | Generated reports, exported data |
| **Skills** | Reusable procedures distilled from experience | "How to onboard a new customer" playbook |
| **Agent state** | Cross-session facts about users and context | Your timezone, preferred language, ongoing projects |

### The learning loop

1. **Observe** — agent completes a multi-step task (tool calls, decisions, your corrections)
2. **Distill** — after similar tasks repeat, the agent proposes a skill or KB entry capturing the procedure
3. **Reuse** — next time a matching task arrives, the agent loads the skill instead of reasoning from scratch
4. **Refine** — feedback (explicit or implicit) updates the skill; bad paths get pruned

Skills are inspectable files — not black-box weight updates. You can read, edit, share, or delete what the agent learned.

### Cross-agent learning

Knowledge isn't siloed. Agents in the swarm can:

- Query each other's tools, skills, and KB via `agent_info`
- Delegate subtasks to the agent best suited for them
- Share artifacts and portal files across workplaces

A general-purpose agent might hand off to a specialist — or pick up a skill another agent created.

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
| **Local** | Dev, sandboxed runs | Docker-isolated `bash` and `runpy` on the coordinator host |
| **Tunnel** | Home labs, edge devices, NAT/firewall | Lightweight connector binary makes an **outbound WebSocket** to the coordinator — no public IP, no port forwarding, no SSH |
| **SSH** | Existing servers, jump hosts | Auto-connect with stored credentials; same tools, traditional transport |

### Why WebSocket tunnel is the default recommendation

SSH requires inbound access, key management, and often manual setup per host. A **reverse WebSocket tunnel** flips the model:

- **Device initiates** the connection outbound → works behind NAT, home routers, corporate firewalls
- **Pair once** with a short-lived code; connector auto-reconnects on restart
- **Same tool surface** — `bash`, `runpy`, file ops — regardless of whether the workplace is local, tunneled, or SSH
- **Persistent channel** — coordinator always knows which devices are online (green/red status)

```bash
# On the coordinator: create a tunnel workplace in the UI (Workplaces → New → tunnel)
# and copy the pairing code (or POST /api/workplaces/{id}/pairing-code).

# On the target device: build the Go connector, pair, run
cd connector && make build
./tomo-connector pair --code X7KQ2M --server https://your-coordinator.example.com
# stays connected; later / after reboot:
./tomo-connector run    # auto-reconnect with saved token (~/.tomo-connector)
```

Once paired, agents assigned to that workplace run `bash` / file tools on the device as if they were local. Status is green only while the WebSocket is live.

### SSH as a fallback

For machines that already have SSH and you don't want to install a connector, Tomo still supports direct SSH workplaces — or session-level `sshc` for ad-hoc connections. Useful for one-off ops on servers you already manage via keys.

### Portals — file bridge across workplaces

The **portal** system lets agents copy files between workplaces and the coordinator workspace (`/_portal/<name>/...`), including large binary transfers with background jobs and progress polling. Handy for pulling build artifacts off a remote device or pushing configs to an edge node.

---

## Channels — talk to your swarm from anywhere

> **Alpha:** Web UI and Telegram bot are live. WhatsApp and other adapters remain planned.

Tomo agents aren't locked to a terminal. **Channels** bridge your swarm and the messaging apps you already use — inspired by multi-platform agents like [Hermes](https://github.com/NousResearch/hermes-agent).

```
You (Telegram)  ──►  Channel  ──►  Agent  ──►  Workplace
     📱                  │            │              🖥️
                    same agent,     tools · skills
                    any interface   · memory · KB
```

The agent doesn't change underneath. You pick the interface; Tomo handles routing, sessions, and replies.

### Example flow

1. Message on Telegram: *"Remind me about the vendor call tomorrow and pull last month's invoice"*
2. Your agent checks memory/KB, uses tools to fetch the invoice, schedules the reminder
3. Replies on Telegram — or sends the PDF as an attachment

Same pattern works for ops deploys, research summaries, or code reviews. The channel is just how you talk to it.

### Channels today

| Channel | Status | Notes |
|---------|--------|-------|
| **Web UI** | ✅ Alpha | Dashboard, swarm chat (SSE), agent studio |
| **Telegram** | ✅ Alpha | Bot token in System → Channels; long-poll |
| **WhatsApp** | 🔜 Planned | WhatsApp Web bridge |
| **Discord / Slack / CLI** | 🔜 Planned | |

### The mobile pattern

**You're on your phone; the swarm runs on a server.** Inspired by agents like [Hermes](https://github.com/NousResearch/hermes-agent) — chat from Telegram while work happens elsewhere. No laptop required.

### Access control (planned)

Each channel will support two modes:

| Mode | Who can chat |
|------|--------------|
| **Open** | Anyone who messages the bot |
| **Restricted** | Only users on the allowlist (default for new channels) |

Restricted mode uses **pairing codes** — a 6-character code a new user receives on first contact. An admin approves it; the user is added to the allowlist. Same flow for Telegram, WhatsApp, and future channels.

```bash
# Approve a pending user
tomo channel approve XK4M9Q
```

### Multi-agent on one channel

A single WhatsApp number or Telegram bot can serve a **team of agents**. When a message arrives, agents coordinate internally to decide who handles it based on skills and availability.

### Channel-native tools

Agents interact with messaging platforms through dedicated tools:

- **send_file** — deliver PDFs, images, spreadsheets as attachments
- **read_attachment** — process files users upload via chat
- **transcribe_audio** — voice memos → text
- **describe_image** — vision on photos sent in chat

Sessions are scoped per `(agent, channel, user)` — the same person on Telegram and WhatsApp gets separate conversation histories by design.

### Primary channel

Each agent can designate one channel as **primary** for outbound notifications. When an agent needs to proactively alert you (task done, escalation, cron reminder), it sends through the primary channel. If none is set, it replies on whichever channel you last used.

### Adding a channel (target CLI)

```bash
# Telegram — attach to any agent
tomo channel add --agent main --type telegram \
  --name "Tomo Bot" \
  --token "123456:ABC-DEF..."

# WhatsApp (scan QR on first connect)
tomo channel add --agent main --type whatsapp \
  --name "Tomo WhatsApp"
```

Configure Telegram under **System → Channels** (encrypted token; blank PUT keeps existing). Agents → Channels shows per-agent status.

---

## Extending Tomo

Adding a new capability is intentionally boring (in a good way).

### 1. Define a tool

Create a JSON file in `app/tools/`:

```json
{
  "id": "my_tool",
  "name": "My Tool",
  "description": "What this tool does.",
  "function": {
    "name": "my_tool",
    "description": "Detailed description for the model.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": { "type": "string", "description": "Input parameter." }
      },
      "required": ["query"]
    }
  }
}
```

### 2. Assign it to an agent

Enable the tool in an agent's configuration. The coordinator and other agents can discover it via the agent-info introspection tool.

### 3. Optionally add a skill

Skills bundle tools + prompts into reusable workflows. Write them yourself, install from a registry, or let agents distill them from repeated tasks.

No recompilation. No framework fork. Start general; specialize when you're ready.

---

## Built-in tools (starter set)

General-purpose primitives — enable per agent based on your use case:

| Tool | Purpose |
|------|---------|
| `bash` | Run shell commands (optional `background` + `process` for jobs) |
| `read_file` / `write_file` / `str_replace` / `delete_file` / `search_files` | File ops under the agent sandbox |
| `web_fetch` / `web_search` | Fetch URLs and search the web |
| `todo` / `session_search` | Lightweight todos and message search |
| `list_skills` / `use_skill` | Browse and load skill descriptions |
| `clarify` / `forget_memory` | Ask the user / delete knowledge entries |
| `recall` / `remember` / `delegate` | Memory and swarm handoff |

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
│   │   ├── agent/                # LLM turn loop, context
│   │   ├── memory/               # Recall and knowledge adapters
│   │   ├── events/               # Internal event bus
│   │   └── tools/                # Built-in Python tool backends
│   ├── tools/                    # Declarative tool JSON (schema + backend ref)
│   ├── channels/                 # Web + Telegram (WhatsApp later)
│   ├── workplaces/               # Local / SSH / tunnel hub + pairing
│   ├── extensions/               # Skill + plugin loaders
│   ├── services/                 # Store facade + chat/SSE
│   ├── static/                   # CSS + JS (Darkroom UI)
│   ├── templates/                # Jinja pages + partials/
│   └── data/                     # Local JSON persistence (dev)
│
├── cli/                          # `tomo` command (start, agent, workplace, …) — stub
├── skills/                       # Installable skill packages
├── plugins/                      # Event-driven platform extensions
├── skillsets/                    # Preset agent profiles (JSON)
├── defaults/                     # Shipped prompts and KB seeds
├── evaluator/                    # LLM evaluation engine — stub (UI hidden; TOMO_EVAL_UI)
├── connector/                    # Go tomo-connector (WebSocket tunnel agent)
├── tests/                        # unit/ + integration/
├── scripts/                      # Dev and release helpers
├── docs/                         # Architecture notes
├── seed/                         # Dev database seeds
├── tmp/                          # Local scratch (gitignored); e.g. hermes-agent clone
└── var/                          # Runtime state (gitignored)
```

### Layer guide

| Layer | Path | Role |
|-------|------|------|
| **Surface** | `app/api/`, `app/web/` | HTTP APIs and server-rendered UI |
| **Contracts** | `app/schemas/` | API validation and serialization |
| **Persistence** | `app/models/` | SQLite (or other) via mixins — replaces JSON store long-term |
| **Runtime** | `app/runtime/` | Coordinator, agent loop, memory, built-in tools |
| **Integrations** | `app/channels/`, `app/workplaces/` | Messaging and execution environments |
| **Extensions** | `skills/`, `plugins/`, `app/extensions/` | Drop-in packages + loaders |
| **Ops** | `cli/`, `scripts/`, `var/` | Lifecycle, tooling, local runtime dirs |

**Today:** `app/services/store.py` is a facade over SQLite mixins (`app/models/`). Runtime, channels, and workplaces are wired for the Alpha demo path.

See `app/tools/` for declarative tool definitions; Python implementations go in `app/runtime/tools/`. Local reference clone: `tmp/hermes-agent/` (NousResearch/hermes-agent).

---

## Getting started

Tomo's **Alpha is live** — SQLite store, multi-model profiles, swarm delegation, bash/file tools on workplaces, KB recall, Telegram, and interval scheduler. Configure models in System → Models; chat over SSE from the dashboard or Chat page.

```bash
git clone https://github.com/Alg0rix/tomo.git
cd tomo
uv sync                    # create .venv and install dependencies
uv run python -m app.main  # start the web UI at http://127.0.0.1:8787
```

### Configuration

**Tomo Home (`$TOMO_HOME`)** — Tomo's writable user root (default `~/.tomo`).
Set `export TOMO_HOME=/path/to/tomo` to relocate it. On first start the tree is
created and seeded from the shipped `defaults/`:

```text
$TOMO_HOME/
├── tomo.yaml          # non-secret prefs only (never API keys / master key)
├── .env               # optional bootstrap secrets (dotfile; never auto-created)
├── .secret_key        # master key for at-rest encryption (chmod 600; auto-created)
├── SOUL.md            # global default persona
├── library/{skills,memory}
├── agents/<id>/{SYSTEM.md,SOUL.md,knowledge,work}
├── workplaces/
└── state/tomo.db      # SQLite (secret settings encrypted at rest)
```

Persona/prompt files use the familiar names `SOUL.md` (persona) and `SYSTEM.md`
(agent system prompt). Edit them under `$TOMO_HOME` to customize Tomo without
touching the git tree; the coordinator loads `$TOMO_HOME/SOUL.md` plus each
agent's `SYSTEM.md` / `SOUL.md` at turn time.

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
- Optional `$TOMO_HOME/.env` (dotfile, `0600`) may hold plaintext bootstrap
  values (loaded with `override=False`, process env wins). Prefer moving durable
  secrets into encrypted SQLite via the UI. Never name it `secrets.env`.

**LLM** — open **System → Models** and set Base URL, API key, and model id
(e.g. `gpt-4o-mini`). The API key is encrypted before it touches SQLite. Until a
key is saved, chat returns a clear error pointing at that page. Max tool
iterations live under **System → General**.

**Database** — state lives in SQLite at `$TOMO_HOME/state/tomo.db` by default
(`TOMO_DB_PATH` / `TOMO_VAR_DIR` override). The directory and DB are created on
first run. To keep a legacy `var/tomo.db`, set
`export TOMO_DB_PATH=var/tomo.db` (there is no automatic migration).

**Server** — `TOMO_HOST` (default `127.0.0.1`), `TOMO_PORT` (default `8787`),
`TOMO_RELOAD` (default `false`). Session cookies are signed with
`TOMO_SESSION_SECRET` (default dev value; set a stable secret in any real
deploy). Admin password: `TOMO_ADMIN_PASSWORD`. Note: `TOMO_SECRET_KEY` is the
**at-rest master key** (see Secrets policy), not the session secret.

### Tests

```bash
uv run pytest
```

> **Note:** Alpha (slices 0→H) is complete. **Tomo Connector** (Go tunnel agent) is implemented — see `connector/README.md`. Next: deeper learning loop, more channels — see Roadmap.

---

## Roadmap

- [x] Alpha — home, models, swarm handoff, tools, workplaces, KB, Web UI
- [ ] Learning loop — observe → distill → reuse → refine; autonomous skill creation
- [ ] Memory engine — semantic search / vector retrieval beyond keyword KB
- [x] Tomo Connector — WebSocket tunnel agent for remote workplaces (Go `connector/`)
- [ ] Channel adapters — Telegram, WhatsApp, Discord, Slack, CLI
- [ ] Skill registry — install and share community skills
- [ ] Observability — traces, artifact browser, cost tracking per agent
- [ ] Eval / evaluator UI (gated today via `TOMO_EVAL_UI`)

---

## Philosophy

**友達 (tomodachi)** means *friend* in Japanese. Tomo agents are collaborators that learn your world — not oracles behind a single prompt box.

Start general. Add one agent, one channel, a few tools. Let it learn your workflows. When patterns emerge, split into specialized agents, write skills, grow the knowledge base. The platform doesn't dictate your use case — you shape it over time.

Build the swarm. Let it learn. Let the coordinator handle the rest.

---

## License

Apache License 2.0. See [LICENSE](LICENSE) for the full text.

Copyright 2026 Tomo contributors

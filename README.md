# Tomo (友達)

> **Work in progress** — UI and platform APIs are functional stubs. Coordinator runtime, channel adapters, and production deployment are still being built.

**Tomodachi** — a general-purpose agent swarm that learns, coordinates, and acts on your behalf.

Tomo starts broad: a **coordinator** plus a team of agents that can talk to you, connect to your machines, use tools, and **get smarter over time**. You shape it into whatever you need — ops automation, customer support, research, coding, personal assistant — by adding agents, skills, and knowledge. The platform stays the same; the use case is yours to define.

---

## Why Tomo?

Most agent frameworks give you a chatbot or a coding copilot. Tomo gives you a **foundation**:

| Goal | How Tomo approaches it |
|------|------------------------|
| **General first** | One platform for any task — automate workflows, answer questions, run commands, manage files. Specialize later with skills and agent roles |
| **Agents that learn** | Memory, knowledge base, and a learning loop — agents observe tasks, distill reusable skills, and improve from feedback across sessions |
| **Swarm coordination** | Multiple specialized agents delegate to each other; the coordinator routes work without a single bottleneck |
| **Talk from anywhere** | WhatsApp, Telegram, web UI — message your swarm from your phone while it works on a server |
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
# On the coordinator: create a tunnel workplace, get a pairing code
tomo workplace create --name "raspberry-pi" --type tunnel

# On the target device: install connector, pair, run
tomo-connector pair --code X7KQ2M --server https://your-coordinator.example.com
tomo-connector run    # auto-reconnect
```

Once paired, agents assigned to that workplace run commands on the device as if they were local.

### SSH as a fallback

For machines that already have SSH and you don't want to install a connector, Tomo still supports direct SSH workplaces — or session-level `sshc` for ad-hoc connections. Useful for one-off ops on servers you already manage via keys.

### Portals — file bridge across workplaces

The **portal** system lets agents copy files between workplaces and the coordinator workspace (`/_portal/<name>/...`), including large binary transfers with background jobs and progress polling. Handy for pulling build artifacts off a remote device or pushing configs to an edge node.

---

## Channels — talk to your swarm from anywhere

> **Status:** Not implemented yet. This section describes the **target design** for how Tomo will connect to messaging apps.

Tomo agents aren't locked to a terminal. **Channels** will be the bridge between your swarm and the messaging apps you already use — inspired by multi-platform agents like [Hermes](https://github.com/NousResearch/hermes-agent) that let you chat from Telegram while the agent works on a cloud VM.

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

### Planned channels

| Channel | Status | Notes |
|---------|--------|-------|
| **Telegram** | 🔜 Planned | Bot token via BotFather; text, files, images |
| **WhatsApp** | 🔜 Planned | WhatsApp Web bridge; multi-agent dispatch, group awareness |
| **Web UI** | 🔜 Planned | Dashboard for agent management and direct chat |
| **Discord** | 🔜 Planned | |
| **Slack** | 🔜 Planned | |
| **CLI** | 🔜 Planned | Terminal interface for local interaction |

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

Or via the web UI: Agents → Channels → Add Channel. *(Planned — not available yet.)*

---

## Extending Tomo

Adding a new capability is intentionally boring (in a good way).

### 1. Define a tool

Create a JSON file in `tools/`:

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
| `bash` | Run shell scripts (local, remote, or sandboxed) |
| `runpy` | Execute Python with persistent session state |
| `read_file` / `write_file` / `str_replace` / `patch` | File operations (coding, config, documents) |
| `sshc` | Ad-hoc SSH session |
| `portal_copy` / `copy_status` | Move files between workplaces |
| `agent_info` | Inspect another agent's capabilities |
| `save_artifact` / `list_artifacts` / `fetch_artifact` | Persistent outputs across sessions |
| `send_file` / `read_attachment` | Channel file exchange |
| `describe_image` / `transcribe_audio` | Vision and voice input |
| `get_weather` / `calculator` / `get_current_date` | Utility tools (examples of easy extensions) |

See the `tools/` directory for the full catalog.

---

## Project structure

```
tomo/
├── .vscode/
│   └── tasks.json      # VS Code: Run Task → Tomo: dev server
├── app/
│   ├── main.py         # FastAPI entry + app factory
│   ├── core/           # config, auth, templates
│   ├── schemas/        # Pydantic request/response models
│   ├── services/       # store + chat engine (stub)
│   ├── api/            # REST + SSE + platform APIs
│   ├── web/            # HTML pages + login
│   ├── static/         # CSS + JS
│   ├── templates/      # Jinja2 pages + partials/
│   │   └── partials/   # toggle, settings (incl. HMADS/users/channels), sessions, agent studio, evaluate, modals
│   └── data/           # local JSON persistence
├── tools/              # Declarative tool definitions (JSON)
├── LICENSE
└── README.md
```

---

## Getting started

```bash
git clone <repo-url>
cd tomo
```

> **Note:** Tomo is under active development. The coordinator runtime, agent definitions, and deployment guides are coming next.

---

## Roadmap

- [ ] Coordinator service — task routing, agent lifecycle, swarm orchestration
- [ ] Learning loop — observe → distill → reuse → refine; autonomous skill creation
- [ ] Memory engine — conversation history, semantic search, knowledge graph
- [ ] Tomo Connector — WebSocket tunnel agent for remote workplaces
- [ ] Agent definitions — YAML/JSON config for roles, models, and toolsets
- [ ] Channel adapters — Telegram, WhatsApp, Web UI (first wave); Discord, Slack, CLI
- [ ] Skill registry — install and share community skills
- [ ] Observability — traces, artifact browser, cost tracking per agent

---

## Philosophy

**友達 (tomodachi)** means *friend* in Japanese. Tomo agents are collaborators that learn your world — not oracles behind a single prompt box.

Start general. Add one agent, one channel, a few tools. Let it learn your workflows. When patterns emerge, split into specialized agents, write skills, grow the knowledge base. The platform doesn't dictate your use case — you shape it over time.

Build the swarm. Let it learn. Let the coordinator handle the rest.

---

## License

Apache License 2.0. See [LICENSE](LICENSE) for the full text.

Copyright 2026 Tomo contributors

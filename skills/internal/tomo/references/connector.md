# Connector

Use this when connecting a machine to Tomo, working through an existing connector, repairing an offline tunnel, or configuring an external MCP server. Follow the user's language when explaining commands and results.

## Choose the connection

| Need | Connection | Where work executes |
|------|------------|---------------------|
| Shell, Python, files, or background jobs on another machine | Tomo Connector, workplace `kind=tunnel` | Paired machine, as the connector's OS user |
| Remote machine already accessible over SSH | Workplace `kind=ssh`, or SSH-assisted connector installation | Remote SSH account; connector account after installation |
| Directory on the Tomo coordinator | Workplace `kind=local` | Coordinator host |
| Tools, resources, or prompts from an external service | MCP server in Settings | MCP service; `stdio` subprocess runs on the coordinator |

Tomo Connector opens an outbound WebSocket to the coordinator. It does not require inbound ports on the target machine. An MCP connection is configured separately and does not pair a tunnel workplace.

## Establish context

1. For machine work, call `list_workplaces` to discover registered hosts and their current status. Resolve the user's target to an actual workplace ID. Filesystem searches do not discover workplaces.
2. Reuse the intended workplace. If installing a new connector, establish the target OS/account, root directory, and coordinator URL reachable **from the target**. `localhost` refers to whichever machine executes the command.
3. Check enabled tools and the agent's workplace scope. A coordinator's ability to list a host does not prove the executing agent can operate on it. This skill does not grant tools, credentials, or access.
4. Configure records through the local `tomo` CLI on the coordinator; load [CLI configuration](configuration-cli.md). Use runtime tools for live work and verification. Use an authenticated UI/admin flow when the operation depends on server-owned live state or no coordinator shell is available. Do not invent a connector-install, pairing-code, or MCP-management agent tool. If execution access is missing, provide exact commands and identify the step the user must run on the target.

For “install connector on server X”, the normal path is coordinator CLI → SSH
terminal on X → connector pairing/service → live remote probe. Do not require
web interaction or a Tomo admin API key merely to issue a local pairing code.
CLI creation does not automatically assign the workplace to an agent or select
it in the current session.

## Install or repair

Load `references/connector/setup.md` for binaries, pairing, systemd, root configuration, SSH-assisted installation, updates, and removal. Load `references/connector/troubleshooting.md` for offline hosts, authentication, proxy issues, RPC failures, and reconnect limitations.

Pairing saves credentials; only a running, authenticated WebSocket makes a tunnel online. Verify the server's live status and a harmless command on the selected machine before declaring the connector ready.

## Use a connected machine

Load `references/connector/operations.md` before choosing remote tools, background jobs, or portal locations, and `references/workplaces.md` for how each tool call picks its host. Select the target explicitly on tools that accept `workplace`; otherwise use the session's selected workplace or the available binding mechanism. Verify host and working directory before mutations. Do not switch to a different host because the intended one is offline.

The connector root is the default working directory and a lexical boundary for file tools, **not OS isolation**. Shell and Python execute with the connector user's permissions. Keep commands within the authorized task and use an appropriate OS account; do not promise that the root prevents shell or symlink access outside it.

## Integrate external services

Load `references/connector/mcp.md` for supported transports, configuration, capability discovery, tool enablement, resources, prompts, and error diagnosis. Use actual discovered tool IDs and input schemas. Never infer that a configured server exposes a particular action.

## Finish with evidence

Report the target workplace or server, observed connection status, verification performed, and any remaining blocker. Redact credentials and private data in logs. An installed binary, saved config, successful pairing, or accepted RPC alone does not prove the user's requested operation succeeded.

Load support files with `use_skill(skill_id="tomo", file="references/connector/<name>.md")`. Coordinator install paths are `references/install.md`. Env and secrets are `references/config.md`.

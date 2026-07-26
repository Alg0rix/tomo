# Declarative tool definitions (OpenAI function schema + metadata).
# Python backends live in app/runtime/tools/.
#
# Alpha sandbox cwd: $TOMO_HOME/agents/<id>/work (see app/runtime/tools/sandbox.py).
# Per-agent enablement: SQLite table agent_tools (missing rows = all enabled).

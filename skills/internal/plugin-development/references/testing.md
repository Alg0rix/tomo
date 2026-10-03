# Validate and run the live loop

Read the manifest and compile Python sources without importing the plugin first.
Check that registered page URLs resolve and templates/assets use the plugin base
URL. Exercise domain reads/writes, user isolation, and failure cases appropriate
to the feature. A new CSS color does not need a new unit test.

Use a temporary Tomo home/work/database for runtime tests; do not rebind the
operator's real store. Install is disabled. Sync declared dependencies, verify readiness, then Enable
runs setup. Exercise missing requirements and conflicting version failures. Check pages,
agent tools and skills, sidebar navigation, and mobile/desktop layout. Add a
private user datum, reload, and confirm persistence. Disable must make page/tool/
skill access unavailable. Other plugins and core pages should still work.

Edit source, call Reload, and verify changed behavior. If reload fails, the old
running instance stays active; fix the source and retry. Git snapshots use
Outdated and Update to fetch a new branch commit, even at the same version.
Verify that failed updates retain the old source, tools, pages, skills, and data;
disabled plugins must stay disabled when their source is updated. Busy plugins return a
conflict rather than interrupting requests/tools/turn hooks. Wait for that work
to finish and retry only the requested action. Never work around it by restarting.

Keep setup short. `api.on_turn_end(callback)` observes completed turns with
session/agent IDs, message, and token counts. Handlers must isolate per-user
state. `api.on_dispose(callback)` releases resources during reload/disable or
failed setup. Dispose connections, handles, and listeners you own; do not start
unmanaged background services. Hooks are synchronous and exceptions are logged
without aborting core turn completion.

Before completion report the plugin ID/source, active state, page URLs, tools and
skills, and validation results. Do not claim a UI is verified from syntax alone.
Full examples and feature integration tests belong in `tomo-plugins` or the
plugin author's repository, not in Tomo core.

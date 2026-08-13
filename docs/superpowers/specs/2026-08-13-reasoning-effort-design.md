# Reasoning effort per profile and session

## Goal

Give each OpenAI-compatible LLM profile an ordered, model-specific list of
reasoning-effort values. Let a chat user choose one from the composer and keep
that choice attached to the chat session when switching between sessions.

The value is provider-facing: profile entries are sent exactly as configured
in the `reasoning_effort` request field. The last configured entry is the
profile's default/highest effort. An empty list means the provider receives no
reasoning-effort field and the composer hides the selector.

## Data model

`llm_profiles.reasoning_efforts_json` stores a JSON array of non-empty,
trimmed, unique strings. Public profile responses expose it as
`reasoning_efforts`; create/update validation accepts the same list.

`sessions.reasoning_effort` stores the user's selected provider value. It is
empty when the user has not selected one. The effective value is resolved from
the session's coordinator profile: a valid stored value wins, otherwise the
last configured profile entry is used. If the stored value is no longer in the
profile list after a model/profile edit, the effective value falls back to the
new highest entry rather than sending an invalid value.

For a delegated agent with a different profile, the same session selection is
used only when that target profile supports the value; otherwise that profile's
highest configured value is used. This keeps one session setting stable while
respecting model-specific vocabularies.

Existing databases receive idempotent migrations with empty defaults, so old
profiles and sessions continue to work without a manual migration.

## API and runtime flow

Add `GET /api/sessions/{session_id}/reasoning-effort` returning the current
profile/model, available values, default value, and effective selection. Add
`PUT` for a selected value; the server rejects values not in the active
profile's list and persists valid values to the owned session.

The chat stream resolves the effective session effort before constructing the
LLM client. The runtime passes it through direct, mention, and delegated turns
to `OpenAICompatClient`, which includes `reasoning_effort` in both streaming
and non-streaming payloads only when a value is effective.

## UI

The LLM profile editor gets a one-value-per-line `Reasoning efforts` textarea.
The profile list shows how many values are configured. The shared composer
footer gets a `Reasoning: <value>` select control. It loads when a session is
opened or created, hides when the resolved profile has no configured values,
and saves changes immediately through the session endpoint. A failed save
restores the prior value and shows the existing toast error.

Draft chats without a session keep the selector unavailable until their first
message creates the session; after creation the selector loads the session's
persistent state.

## Testing

Tests cover profile normalization and CRUD persistence, session selection and
fallback, API ownership/validation, OpenAI-compatible payload inclusion for
stream and non-stream calls, and runtime propagation of the selected effort.
Existing profile, session, and stream tests must remain green.

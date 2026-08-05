# Artifact Public Link Sharing

## Goal
Let users share any artifact file from a chat session through a public link, without requiring the recipient to log in. The experience should be similar to Gemini / ChatGPT share links: one persistent link per artifact, revokable by the sharer.

## Context
- Artifacts are stored on disk at `$TOMO_HOME/sessions/<session_id>/artifacts/<filename>`.
- Catalog rows live in the `artifacts` SQLite table (`app/models/schema.py:258-268`).
- Authenticated API endpoints are in `app/api/rest.py` under the `/api` router.
- The authenticated HTML viewer is at `/sessions/{session_id}/artifacts/{filename}/view` (`app/web/pages.py:72-131`).
- There is currently no sharing, public-link, or token mechanism.

## Design

### 1. Capability model
A share is an opaque, unguessable capability token that maps to a single `(session_id, filename)`. Anyone who knows the token can read that file and view it in the public viewer. The token is the only authorization needed.

### 2. Data model
Add a new table `artifact_shares` instead of altering `artifacts`, so sharing stays decoupled from catalog rows and works for any file present in the session artifacts directory.

```sql
CREATE TABLE IF NOT EXISTS artifact_shares (
    token      TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    filename   TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    UNIQUE(session_id, filename)
);
CREATE INDEX IF NOT EXISTS idx_artifact_shares_session_filename
    ON artifact_shares(session_id, filename);
```

- `token`: 32-character URL-safe random string (`secrets.token_urlsafe(24)`), stored plaintext so the existing link can be reshown.
- `UNIQUE(session_id, filename)`: enforces one persistent public link per artifact, matching Gemini/ChatGPT behavior.

### 3. Store layer (`app/services/store.py` / `app/runtime/memory/layers.py`)

```python
def share_artifact(conn, session_id, filename, created_by="") -> dict:
    """Create a share token if missing, or return the existing one."""

def get_artifact_share(conn, token) -> dict | None:
    """Resolve a token to {session_id, filename, created_at, created_by}."""

def revoke_artifact_share(conn, session_id, filename) -> bool:
    """Delete the share for the given file."""
```

All store methods are wrapped by `store.py` with the existing `RLock`.

### 4. API endpoints (authenticated, `app/api/rest.py`)

- `POST /api/sessions/{session_id}/artifacts/{filename}/share`
  - Creates or returns the existing share token for the file.
  - Returns `{ "token": "...", "share_url": "/share/..." }` (relative; the UI copies `window.location.origin + share_url`).
  - 404 if the file does not exist.

- `GET /api/sessions/{session_id}/artifacts/{filename}/share`
  - Returns `{ "shared": true, "share_url": "..." }` if a share exists, else `{ "shared": false }`.

- `DELETE /api/sessions/{session_id}/artifacts/{filename}/share`
  - Revokes the share. Returns `{ "success": true }`.

Access control: reuse the existing `_require_session(session_id)` check. Any authenticated/API-key user can share any session artifact, consistent with current artifact endpoints.

### 5. Public routes (no auth)

- `GET /share/{token}` (`app/web/pages.py`)
  - Resolves the token, validates the file exists, then renders `artifact_view.html`.
  - Passes public file URLs (`/api/share/{token}/raw`) so the template does not need authentication.
  - 404 if token or file is missing.

- `GET /api/share/{token}/raw` (`app/api/rest.py`)
  - Resolves token, serves the file with the same MIME and security rules as the authenticated endpoint:
    - HTML files are forced to `text/plain; charset=utf-8` with `Content-Disposition: attachment` to prevent XSS on the Tomo origin.
    - Other files are served inline.
  - Sets `X-Content-Type-Options: nosniff`.

- `GET /api/share/{token}/download` (`app/api/rest.py`)
  - Same as raw but always sends `Content-Disposition: attachment`.

### 6. Frontend

#### Full-page viewer (`app/templates/artifact_view.html`)
- Add a **Share** button to the header toolbar when the page is rendered for an authenticated owner.
- When clicked:
  - `POST /api/sessions/{session_id}/artifacts/{filename}/share`
  - Copy the returned `share_url` to the clipboard.
  - Toggle to **Copy link** / **Revoke** state if already shared.
- The public viewer (accessed via `/share/{token}`) hides the Share button.

#### Side panel (`app/static/js/artifacts.js`)
- Add a share affordance to the preview chrome (`renderDrill` preview actions) next to the "Open in new tab" button.
- Clicking it calls the share endpoint and copies the link.

### 7. Security
- Tokens are cryptographically random and long enough to be unguessable.
- Public routes do not leak session listings or other artifacts.
- Path traversal is prevented by reusing `validate_filename` and `Path.resolve().relative_to()`.
- HTML is never rendered as an active document on the Tomo origin; it is served as a text attachment and the viewer uses a sandboxed `<iframe srcdoc>`.
- Revocation is immediate: deleting the row invalidates all links.

### 8. Error handling
- 400 for invalid filenames.
- 404 for missing tokens or missing files.
- Share endpoint returns existing token if the file is already shared (idempotent).

### 9. Testing
- Unit tests for `store.share_artifact`, `get_artifact_share`, `revoke_artifact_share`.
- API tests for create/get/delete share endpoints.
- Public route tests verifying that an unauthenticated client can fetch raw/download/viewer by token but cannot list files or access other artifacts.

### 10. Out of scope (MVP)
- Expiring links.
- Multiple share links per artifact.
- Password-protected shares.
- Analytics / view counts.
- Social-media-style preview metadata (OpenGraph).

These can be added later without changing the public URL shape.

## Files to touch
1. `app/models/schema.py` — add `artifact_shares` table and migration.
2. `app/runtime/memory/layers.py` — DB primitives for shares.
3. `app/services/store.py` — public store methods.
4. `app/api/rest.py` — authenticated share endpoints + public raw/download endpoints.
5. `app/web/pages.py` — public viewer page at `/share/{token}`.
6. `app/templates/artifact_view.html` — share button + copy/revoke logic.
7. `app/static/js/artifacts.js` — share affordance in side-panel preview.
8. `tests/unit/...` and `tests/integration/...` — new tests.

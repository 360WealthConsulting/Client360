# Client360 MCP interface (Phase 1 — read-only)

A minimal Model Context Protocol server that lets an assistant (ChatGPT, Claude Desktop, the MCP
Inspector) **read** Client360 clients and documents. It is a thin adapter over the existing service
layers, so it inherits the firm's permission boundaries rather than defining new ones.

It cannot create, change or delete anything. There is no SQL tool, no filesystem tool, no shell tool,
and no URL fetching.

---

## 1. What it exposes

| Tool | Scope required | Returns |
| --- | --- | --- |
| `search_clients` | `client:read` | People, households, businesses, trusts, estates — id, display name, relationship context, status |
| `get_client` | `client:read` | One client's summary: identity, status, household, members, related entities |
| `list_client_documents` | `document:read` | A client's documents (person **and** household union), paginated |
| `get_document` | `document:read` | One document's metadata, ownership, provenance, version identity, OCR state |
| `search_documents` | `document:read` | Documents matching a query, optionally scoped to a client |
| `get_document_text` | `document:content:read` | Text OCR has **already** extracted. Never starts OCR |

### What it deliberately does not return

- **Contact details.** No email addresses or phone numbers, in any tool. Staff have the web UI.
- **Storage internals.** No `storage_path`, `storage_uri`, `stored_name` or `sha256`. Documents carry
  a `download_reference` (`/documents/{id}/download`) — the authenticated route, which re-checks
  permission when a signed-in human follows it.
- **Soft-deleted documents.** Excluded from every listing, search and direct fetch.

---

## 2. Authorization model

Six layers, each of which can only remove access. Default deny throughout.

1. **Feature flag** — `CLIENT360_MCP_ENABLED` must be true, or `/mcp` returns 404.
2. **Token** — a valid, unexpired, unrevoked MCP token whose owner is an active user.
3. **Door capability** — the owner must hold `mcp.access`.
4. **Token scope** — the token must carry the tool's scope.
5. **App capabilities** — the owner must hold the same capabilities the web UI requires
   (`client.read`, `documents.view`).
6. **Record scope** — enforced per row by the services themselves (`accessible_person_ids`,
   `record_in_scope`, the document platform's scope clause).

Migration `mcp01` seeds four capabilities and grants them to **no role at all**. Until an
administrator grants them, every MCP call is denied even with a valid token.

| Capability | Grants |
| --- | --- |
| `mcp.access` | May reach the MCP interface at all |
| `mcp.client.read` | Client summaries and search |
| `mcp.document.read` | Document metadata |
| `mcp.document.content.read` | Already-extracted document text |

MCP tokens live in their own table and are **not** interchangeable with staff browser sessions: an
MCP token cannot open the web UI, and a session cookie cannot call `/mcp`.

### Audit

Every call — success, denial and malformed request — is appended to the existing tamper-evident audit
chain with the timestamp, actor, tool, target entity/document and outcome. Arguments, document text
and tokens are never recorded.

---

## 3. Enabling it

### 3.1 Apply the migration

```bash
python -m alembic upgrade head
```

### 3.2 Grant the capabilities

The four `mcp.*` capabilities exist but belong to no role. Grant them to a role through normal role
administration. Least privilege: give an assistant account `mcp.access` plus only the read
capabilities it genuinely needs — `mcp.document.content.read` is the one that exposes document text
and should be granted last, if at all.

### 3.3 Turn the interface on

```bash
CLIENT360_MCP_ENABLED=true
CLIENT360_MCP_TOKEN_TTL_HOURS=12
```

With the flag unset, `/mcp` 404s and no token works.

### 3.4 Issue a token

```bash
python scripts/mcp_token.py issue --email advisor@firm.com --scopes client:read,document:read --label "ChatGPT dev mode"
```

The token is printed once and stored only as a SHA-256 hash. If it is lost, revoke it and issue
another. The CLI warns if the user does not hold `mcp.access`.

---

## 4. Local testing

### 4.1 Over stdio (MCP Inspector, Claude Desktop)

```bash
CLIENT360_MCP_ENABLED=true CLIENT360_MCP_TOKEN=c360mcp_... python -m app.mcp.stdio
```

The credential comes from the environment, never from argv — a token on a command line is a token in
the process list. Diagnostics go to stderr; stdout carries only the JSON-RPC stream.

With the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector python -m app.mcp.stdio
```

### 4.2 Over HTTP

Start the app, then:

```bash
curl -s -X POST http://127.0.0.1:8000/mcp -H "Authorization: Bearer $CLIENT360_MCP_TOKEN" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

A call:

```bash
curl -s -X POST http://127.0.0.1:8000/mcp -H "Authorization: Bearer $CLIENT360_MCP_TOKEN" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_clients","arguments":{"query":"Ashford"}}}'
```

Expected failures, as a smoke test of the gate:

| Request | Expected |
| --- | --- |
| No `Authorization` header | `401` with `WWW-Authenticate: Bearer` |
| `CLIENT360_MCP_ENABLED` unset | `404` |
| Tool outside the token's scopes | `200` with `isError: true` naming the missing scope |
| `GET /mcp` | `405` — this server does not stream |

### 4.3 Run the tests

```bash
scripts/test.sh run tests/test_mcp_server.py tests/test_mcp_no_mutation.py tests/test_mcp_http_transport.py
```

---

## 5. Connecting ChatGPT Developer Mode

ChatGPT connects to **remote** MCP servers over HTTP, so Client360 must be reachable from OpenAI's
side. Do **not** publish the app to the internet for this — use the Secure MCP Tunnel (§6). For a
development trial against a non-production database, a temporary tunnel to a local instance is
acceptable.

1. Enable the interface and issue a token (§3).
2. In ChatGPT, open **Settings → Connectors → Advanced → Developer mode**.
3. **Create** a connector:
   - **URL**: `https://<your-tunnel-host>/mcp`
   - **Authentication**: Custom header — `Authorization: Bearer <token>`
4. ChatGPT calls `initialize` and `tools/list`; the six tools should appear.
5. Start a chat with the connector enabled and ask something scoped, e.g. *"What tax documents do we
   have for the Ashford household for 2024?"*

Notes:

- The connector acts as **one staff user** — the token's owner. Everything it can see is what that
  person can see. Issue a dedicated, least-privilege account rather than reusing a partner's login.
- Tokens expire (12h by default). A connector that starts returning 401 needs a fresh token.
- Every question the assistant answers leaves an audit trail attributed to that user.

---

## 6. Production: OpenAI Secure MCP Tunnel

The tunnel gives OpenAI an outbound-initiated path to the MCP endpoint without exposing Client360 to
the public internet. Client360 keeps its normal private-network posture; the tunnel process runs
beside it and reaches only `/mcp`.

1. **Do not** add a public DNS record or open an inbound firewall port for MCP.
2. Run the app so it listens on loopback only, e.g. `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
3. Run the tunnel client on the same host, pointed at `http://127.0.0.1:8000/mcp`, per OpenAI's
   current Secure MCP Tunnel instructions. It dials **out**; nothing dials in.
4. Register the tunnel's URL as the connector endpoint, with the `Authorization: Bearer` header.
5. Confirm before going live:
   - `curl` from another host on the network to the app's MCP path **fails** (loopback only).
   - `GET /mcp` through the tunnel returns 405, and an unauthenticated POST returns 401.
   - The audit chain shows the tunnelled calls with the expected actor.

Operational guidance:

- Give the tunnel its own service account with the narrowest capability set that answers the
  questions you actually want answered.
- Rotate the token on a schedule; the TTL makes this the default rather than a chore.
- Review `mcp.tool.call` audit events periodically — that is where an assistant reading more than it
  should would show up first.

---

## 7. Disabling and rollback

Four levels, fastest first. The first is instant and needs no deploy.

**1. Revoke the credential** — stops one client immediately.

```bash
python scripts/mcp_token.py revoke --email advisor@firm.com --all
```

**2. Turn the interface off** — `/mcp` 404s for everyone.

```bash
CLIENT360_MCP_ENABLED=false   # then restart the app
```

**3. Remove the capability** — revoke `mcp.access` from every role. Takes effect on the next call;
capabilities are read live, not cached on the token.

**4. Full rollback** — remove the schema and the capabilities:

```bash
python -m alembic downgrade docnorm01
```

This drops `mcp_access_tokens` (and with it every issued credential) and deletes the four `mcp.*`
capabilities. It touches no client, document or audit data. To also remove the code, revert the
commit; the router is inert without the flag, so leaving it mounted is harmless.

Audit events already written are retained — the chain is append-only by design, and the record of
what an assistant read outlives the connector that read it.

---

## 8. Files

| Path | Role |
| --- | --- |
| `app/mcp/config.py` | Feature flag, limits, protocol version |
| `app/mcp/scopes.py` | Scope vocabulary and scope → capability mapping |
| `app/mcp/tokens.py` | Token issue / resolve / revoke |
| `app/mcp/auth.py` | The default-deny gate |
| `app/mcp/audit.py` | Audit-chain logging |
| `app/mcp/tools.py` | The six tools and the registry |
| `app/mcp/projection.py` | Outbound field allow-list |
| `app/mcp/protocol.py` | JSON-RPC / MCP dispatch |
| `app/mcp/stdio.py` | Local stdio transport |
| `app/routes/mcp.py` | HTTP transport |
| `scripts/mcp_token.py` | Operator CLI |
| `migrations/versions/mcp01_mcp_read_only_access.py` | Capabilities + token table |

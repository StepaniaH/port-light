# Troubleshooting and BYOK

These features are unreleased. The workspace runs in the same application and
uses the same authentication as the dashboard.

## Workflow

Open the troubleshooting workspace from the toolbar or a local port's details.
Choose known ports, one port, selected ports, or a range. The workspace can:

- Suggest checks based on a snapshot of the selected ports.
- Show complete observed changes over 1, 6, or 24 hours.
- Save reports and export JSON. Saved reports keep their original results.
- Compare a new scan with a saved report. Results show which issues remain,
  which are new, and which could not be checked.

The workspace reads the local instance. Open a peer instance to inspect its ports.
TCP and UDP are checked separately. Scanning, captures, saving and rechecks do
not call a model.
The existing CLI/MCP integration remains available under Settings → Automation.

## Optional AI

In Settings → AI, select a provider, model ID and your own API key. OpenAI,
DeepSeek and OpenCode Go adapters are included. Use a model available through
your provider’s Chat Completions API. OpenCode Go is intended for coding-agent use;
check its usage terms before configuring it.

Review the exact selected payload and confirm before each model request. The
request travels directly from the Hub to the provider and is billed to your key.
Requests run only after confirmation and are not retried automatically. Changing
the saved connection requires a new confirmation.
AI may order existing problems and evidence-linked checks; it cannot execute
commands, change services, introduce arbitrary report text or certify health.

An operator may configure an additional Chat Completions service with:

| Environment variable | Meaning |
| --- | --- |
| `PORT_LIGHT_BYOK_BASE_URL` | HTTPS API base URL; `/chat/completions` is appended. No credentials, query or fragment. |
| `PORT_LIGHT_BYOK_NAME` | Optional display name. |
| `PORT_LIGHT_BYOK_TOKEN_PARAMETER` | `max_tokens` (default) or `max_completion_tokens`. |
| `PORT_LIGHT_BYOK_JSON_MODE` | `1` to request JSON-object output; default `0`. |

`PORT_LIGHT_SETTINGS_SOURCE=env` makes AI settings read-only too. These variables
configure the extra endpoint; they do not supply a model key. A malformed endpoint
configuration disables the analysis workspace while leaving the dashboard active.
Plain HTTP local model endpoints are not supported by this adapter.

The adapters use Chat Completions, not the OpenAI Responses or Anthropic Messages
protocols. OpenAI requests use `max_completion_tokens`, JSON-object output and
`store: false`. DeepSeek requests use `max_tokens` and JSON-object output.
OpenCode Go requests use `max_tokens` without JSON mode and include an
`x-opencode-session` identifier for the attempt. Custom endpoints use the token
parameter and JSON mode configured above.

Provider requests do not follow redirects or use proxy environment variables
such as `HTTP_PROXY` and `HTTPS_PROXY`. The Hub needs a direct HTTPS connection
to the configured endpoint.

## Storage and access

Data lives below `PORT_LIGHT_DATA_DIR/analysis`:

- `byok/profile.json`: one connection profile, protected by directory mode `0700`
  and file mode `0600`. The key is not application-encrypted; protect the data volume
  and backups. The API never returns its value or a partial key.
- `state/analysis.sqlite3`: reports and bounded job receipts. Reports survive
  restart but belong to their browser session. Clearing the session cookie or
  switching browsers loses access through the UI. Export reports you need to keep.

Every report read rechecks current access to its referenced resources. Hidden
port protection, Basic Auth and same-origin mutation checks apply. Run one
application worker per data directory.

## Limits and failure behavior

A capture includes at most 1024 port numbers (up to 2048 TCP/UDP resource facts).
Oversized scopes are rejected rather than silently truncated. Workbench reports
have limits of 128 reports, 2 MiB each and 32 MiB total. Legacy single-port report
limits are separate. At capacity, new saves are refused; existing data is not
automatically deleted.

AI receives at most 12 captured problems and 32 KiB of selected evidence. Names,
raw addresses, URLs, paths, labels and keys are excluded from the evidence payload.
The provider still receives the API key as authentication and can see the Hub's
network address. Review the payload and the provider's data policy before sending.

Interrupted requests are not retried automatically; an upstream provider may
already have billed a timed-out request. Temporary single-port previews and
workbench captures expire after 10 minutes; finishing an AI attempt starts a new
10-minute retention period. Expired entries are cleaned up every 30 seconds.
Corrupt report databases are preserved, and storage errors do not stop scanning.


## HTTP API

All paths below are relative to `/analysis/api`. Requests use the dashboard's
Basic Auth when enabled. Keep the browser's HttpOnly, SameSite=Strict session
cookie, scoped to `/analysis`, to access its captures and reports. Mutations
require `X-Port-Light-Analysis: 1` and must pass the same-origin check. Hidden
resources also require the current `X-Hidden-Unlock` header.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/settings` | Read connection settings without the API key. |
| PUT / DELETE | `/settings/ai` | Save or clear the connection. |
| GET | `/workbench/options` | Read available scopes and limits. |
| POST | `/workbench/captures` | Capture observations and suggested checks. |
| GET | `/workbench/captures/{id}` | Read a capture and its current status. |
| POST | `/workbench/captures/{id}/ai` | Start one explicitly confirmed model request. |
| POST | `/workbench/captures/{id}/cancel` | Cancel an attempt. |
| GET / POST | `/workbench/reports` | List or save reports. |
| GET | `/workbench/reports/{id}` | Read a saved report. |
| GET | `/workbench/reports/{id}/export` | Download a saved report as JSON. |
| POST | `/workbench/rechecks` | Compare new observations with a saved report. |

The single-port API also provides `POST /analysis/previews`,
`GET /analysis/{id}`, and `POST /analysis/{id}/start` or `/cancel`.
`GET /analysis/{id}/report` exports a completed temporary result.
Use `GET /reports` to list saved single-port reports, `POST /reports` to save one,
and `GET /reports/{id}` or `/reports/{id}/export` to read or export it.

Submitting the same attempt twice does not trigger a second provider request.
Captures and reports are checked against the requesting session and current port
access on each read. Saving a report does not grant access to hidden ports.

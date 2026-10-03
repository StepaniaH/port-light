# Troubleshooting and BYOK

Available since v0.8.5. The workspace runs in the same application and uses the same authentication as the dashboard.

## Workflow

Open the troubleshooting workspace from the toolbar or a local port's details.
Choose known ports, one port, selected ports, or a range. The workspace can:

- Compare listeners, Docker mappings and Compose declarations for selected ports,
  then show configuration conflicts, mapping mismatches and suggested checks.
- Show complete observed changes over 1, 6, or 24 hours.
- Save reports and export JSON. Saved reports keep their original results.
- Compare a new scan with a saved report. Results show which issues remain,
  which are new, and which could not be checked.

Changing the check conditions keeps the previous result visible until you run
another check. Pending conditions survive a visit to settings; use **Restore
checked conditions** to return to the result's scope, protocol and time window.
Mapping findings list affected ports first, with other project ports under the
supporting details. AI recommendations link to the captured records they use.
Saved reports show their save time and the model used for any included analysis.

The workspace reads the local instance. Open a peer instance to inspect its ports.
TCP and UDP are checked separately. Scanning, captures, saving and rechecks do
not call a model.
The existing CLI/MCP integration remains available under Settings → Automation.

## Optional AI

In Settings → AI, select a provider, model ID and your own API key. OpenAI,
DeepSeek and OpenCode Go adapters are included. Choose **Custom (OpenAI compatible)**
to enter another API base URL and model ID. HTTP and HTTPS addresses are supported;
credentials, query parameters and fragments are rejected. You can paste the full
`/chat/completions` URL; the saved address is normalized to its base URL. Changing
the address requires re-entering the key so it cannot be sent to a different service
without your input. Use a model available through
your provider’s Chat Completions API. OpenCode Go is intended for coding-agent use;
check its usage terms before configuring it.

The saved-connections list shows the provider, model, API address and which
connection is in use. Add up to 16 connections; the first becomes active.
Adding further connections or testing an inactive connection does not change
the current selection. Select its radio button to use it for analysis. Each
connection has its own key and can be edited, tested or deleted independently.
Deleting the active connection leaves none selected until you choose another.
Existing single-connection settings appear in the list with their key retained.

Review the exact selected payload and confirm before each model request. The
request travels directly from the Hub to the provider and is billed to your key.
Requests run only after confirmation and are not retried automatically. Changing
the saved connection requires a new confirmation.
Use **Test connection** to check the current form before saving, or to check a saved
connection. The test sends a small fixed prompt and may be billed by the provider.
It does not save the form, send port evidence or forward a browser session cookie.
The small test can succeed when a model uses its token limit for reasoning;
analysis still requires a complete, valid conclusion.

AI returns a short explanation linked to captured evidence and may order existing
checks. Ordinary port observations can be explained even when no rule flags a
problem. The explanation uses the UI language and is saved with the report.
It cannot execute commands, change services or certify application health.
Empty or invalid conclusions are treated as failed responses.
Incomplete responses have a separate error message. After a failed or cancelled
analysis, **Run check again** captures fresh local data; a new AI request still
requires confirmation. The failed capture can be saved before starting over.

Custom connections default to `max_tokens` without JSON mode. Expand API options
to use `max_completion_tokens` or request JSON output if your service requires it.
The workbench shows the saved address before sending evidence.

The request-scoped API also supports operator configuration with:

| Environment variable | Meaning |
| --- | --- |
| `PORT_LIGHT_BYOK_BASE_URL` | HTTP(S) API base URL; `/chat/completions` is appended. No credentials, query or fragment. |
| `PORT_LIGHT_BYOK_NAME` | Optional display name. |
| `PORT_LIGHT_BYOK_TOKEN_PARAMETER` | `max_tokens` (default) or `max_completion_tokens`. |
| `PORT_LIGHT_BYOK_JSON_MODE` | `1` to request JSON-object output; default `0`. |

`PORT_LIGHT_SETTINGS_SOURCE=env` makes AI settings read-only too. These variables
configure the request-scoped endpoint; they do not supply a model key. Saved
connections use their own saved address and options. Earlier saved custom profiles
need an address and a new key entry; preset profiles remain compatible. A malformed endpoint
configuration disables the analysis workspace while leaving the dashboard active.

The adapters use Chat Completions, not the OpenAI Responses or Anthropic Messages
protocols. OpenAI requests use `max_completion_tokens`, JSON-object output and
`store: false`. DeepSeek requests use `max_tokens` and JSON-object output.
OpenCode Go requests use `max_tokens` without JSON mode and include an
`x-opencode-session` identifier for the attempt. For `deepseek-v4.1-flash` on
OpenCode Go, `reasoning_effort: none` reserves the bounded output budget for the
answer rather than internal reasoning. Internal source pointers are omitted from
the model payload so citations refer to the supplied facts. The model selects
checks; their relationship to each problem comes from the captured rules.
In model input, the historical `public` binding category is named
`wildcard_or_global_address` to distinguish it from verified external access.
Saved source observations retain their original values.
Custom endpoints use the token
parameter and JSON mode saved in their connection, or the environment options
above for request-scoped calls.

Provider requests do not follow redirects. Set `PORT_LIGHT_AI_PROXY` to an HTTP(S)
proxy address when the Hub needs a proxy to reach the provider. Keep proxy
credentials in the server environment. A directly launched Hub ignores ambient
proxy variables such as `HTTP_PROXY`, `HTTPS_PROXY` and `ALL_PROXY`.
For local development, `scripts/dev.py serve` uses an existing HTTP(S)
`HTTPS_PROXY` or `HTTP_PROXY` (including lowercase names) unless
`PORT_LIGHT_AI_PROXY` is already set. Set it to an empty value to connect directly.

## Storage and access

Data lives below `PORT_LIGHT_DATA_DIR/analysis`:

- `byok/profile.json`: saved connections and the active selection, protected by directory mode `0700`
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

AI receives at most 12 captured problems, 32 port/protocol states and 32 KiB of
selected evidence. The preview discloses omitted states and problems. Names,
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
| GET | `/settings` | Read saved connections and the active selection without API keys. |
| POST | `/settings/ai/connections` | Add a connection. |
| PUT / DELETE | `/settings/ai/connections/{id}` | Edit or delete one connection with its current `config_revision`. |
| POST | `/settings/ai/active` | Select a saved `profile_id` with its current `config_revision`. |
| PUT / DELETE | `/settings/ai` | Legacy API: edit the active connection or clear all connections. |
| POST | `/settings/ai/test` | Test a saved or draft connection with explicit billing consent. |
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

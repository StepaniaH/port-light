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
already have billed a timed-out request. Temporary captures expire in memory.
Corrupt report databases are preserved, and storage errors do not stop scanning.

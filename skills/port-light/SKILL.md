---
name: port-light
description: Check port occupancy and reserve ports before starting servers or writing Docker Compose mappings. Use when selecting a port, investigating a port conflict, or coordinating reservations with other Port-Light clients.
---

# Port-Light

Port-Light combines listening sockets, Docker port mappings, Compose declarations,
and manual entries. Results describe the latest scan; reservations coordinate
Port-Light clients but do not bind operating-system sockets.

## Setup

Install the CLI from the same release as the server. Configure the URL reachable
from the environment where the command runs:

```bash
export PORT_LIGHT_URL="http://127.0.0.1:2100"
# Optional authentication configured on the server:
export PORT_LIGHT_AUTH="user:password"
export PORT_LIGHT_AGENT_TOKEN="agent-token"
```

Keep credentials out of command-line arguments and shared logs. Use a persistent,
private `PORT_LIGHT_STATE_DIR` for release tokens and pending recovery requests.

## Check occupancy

```bash
port-light doctor
port-light check 5432
```

`check` exits 0 when the port is free and 1 when it is used or configured. An
incomplete scan returns an error; do not treat that result as a free port.

## Reserve ports

```bash
port-light reserve --count 1 --start 8000 --end 8999 --label my-preview
```

Reservations default to one hour. Use `--ttl 10m` for a different duration or
`--no-expiry` for a persistent reservation. `--scope all` checks configured peers
and refuses allocation if any peer cannot supply a complete, unlocked map.
Reservations are stored only on the selected server.

The CLI saves release tokens locally. Use `--json` when structured output is
needed, and treat the output as secret because it contains release tokens.
Labels appear in the dashboard and must not contain credentials.

## Recover or release

After a timeout, retry the same reservation arguments with the same URL and
state directory. To inspect pending requests or recover without allocating:

```bash
port-light requests
port-light recover <request-id>
port-light release 8000
```

Explicit recovery can retrieve still-active claims after the automatic retry
window. It never creates new claims or extends leases. Release uses the saved
per-port token. Bind promptly after reserving: another process can still claim
the operating-system port between the scan and the bind.

For stateless automation, `--no-save --json` requires a caller-retained
`PORT_LIGHT_REQUEST_KEY`; reuse that secret key after uncertain failures.
See `docs/cli.md` and `docs/integrations.md` for the full command and HTTP APIs.

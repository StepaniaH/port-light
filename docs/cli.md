# Command-line client

`port-light` is a pure HTTP client for a running Port-Light instance. It does
not inspect local sockets, Docker, or Compose files itself, and a reservation
does not bind an operating-system socket. The server remains the source of
truth for occupancy.

## Install

Python 3.11 or newer is required. From a checkout or a tagged Git URL:

```bash
pipx install .
pipx install "git+https://github.com/StepaniaH/port-light.git@vX.Y.Z"
```

The equivalent `uv` commands are:

```bash
uv tool install .
uv tool install "git+https://github.com/StepaniaH/port-light.git@vX.Y.Z"
```

Each tagged GitHub Release also attaches a matching `py3-none-any` wheel for
offline or pinned installation.

The published container also includes the command, so no host installation is
needed when Port-Light is already running in Docker:

```bash
docker exec port-light port-light doctor
```

When the standard `/data` volume is mounted, container-side reservation tokens
are kept under `/data/cli-state` and survive container recreation.

Use the server URL that is reachable from where the command runs. Inside the
Port-Light container the default `http://127.0.0.1:2100` is normally correct;
from the host, use its published port or reverse-proxy URL.

## Configure

Command options take precedence over environment variables.

| Setting | Environment variable | Default |
|---------|----------------------|---------|
| Server URL | `PORT_LIGHT_URL` | `http://127.0.0.1:2100` |
| HTTP Basic Auth | `PORT_LIGHT_AUTH=user:password` | unset |
| Suggestion API token | `PORT_LIGHT_AGENT_TOKEN` | `AGENT_TOKEN`, then unset |
| Reservation scope | `PORT_LIGHT_SCOPE=self|all` | `self` |
| Request timeout | `PORT_LIGHT_TIMEOUT` | `5` seconds |
| HTTPS CA bundle | `PORT_LIGHT_CA_FILE` | system trust store |
| Local token directory | `PORT_LIGHT_STATE_DIR` | platform state directory; `/data/cli-state` in the image |
| One release token | `PORT_LIGHT_RESERVATION_TOKEN` | saved token |

`PORT_LIGHT_AUTH` authenticates the whole UI/API when the server has Basic
Auth enabled. `PORT_LIGHT_AGENT_TOKEN` is a separate server-side gate for port
suggestions and reservations. A reservation token is a third, one-time secret
returned for a particular port and required to release it. Credentials are not
accepted as command-line arguments because process lists and shell history can
expose them.

Examples:

```bash
export PORT_LIGHT_URL=https://ports.example.lan
export PORT_LIGHT_AUTH='operator:password'
export PORT_LIGHT_AGENT_TOKEN='agent-token'

port-light doctor
port-light check 5432
port-light reserve --count 2 --start 8000 --end 8999 --label preview
port-light release 8000
```

## Commands

### `doctor`

Checks whether the server currently has a complete, trustworthy occupancy
snapshot. Exit status `0` means healthy and `1` means the diagnostic needs
attention.

```bash
port-light doctor
port-light doctor --json
```

### `check PORT`

Checks one port. It exits `0` only when the port is reported as free, which
makes it useful as a CI or shell assertion. Used or configured ports exit `1`.

```bash
port-light check 8080
```

### `reserve`

Atomically selects and reserves the requested number of free ports. The
default is one port, a one-hour lease, and `scope=self`.

```bash
port-light reserve
port-light reserve --count 3 --start 30000 --end 39999 --ttl 30m
port-light reserve --scope all --label 'integration test'
port-light reserve --no-expiry
```

Durations accept seconds or `s`, `m`, `h`, and `d` suffixes, from 60 seconds
through 7 days. Persistent reservations require the explicit `--no-expiry`
flag. Labels are visible to users of the Port-Light dashboard and API; do not
put secrets in them.

`scope=self` checks the selected server. `scope=all` also asks that server to
check every configured peer, then saves the reservation on the selected server.
It fails closed if a peer cannot provide a complete occupancy map. If peers are
configured and the scope was left at its default, the CLI prints a warning.
For shared automation, configure every caller with the same hub URL; otherwise
each Port-Light instance owns an independent reservation set.

By default, release tokens are saved locally and are not printed in human
output. On Unix-like systems they live under
`$XDG_STATE_HOME/port-light/reservations` or
`~/.local/state/port-light/reservations`; set `PORT_LIGHT_STATE_DIR` to choose
another directory. Directories are owner-only and token files are written
atomically with owner-only permissions where the platform supports them.
Tokens are partitioned by server URL and port.

For stateless automation, use JSON output together with `--no-save` and capture
the returned token in a secret store:

```bash
port-light reserve --json --no-save --ttl 10m
```

`--no-save` requires `--json` so a release token cannot disappear silently.
JSON reservation output contains secrets and should not be written to public
logs. If the remote reservation succeeds but local token storage fails, the
CLI returns exit `3` and emits recovery JSON containing the tokens.

### `release PORT`

Releases a reservation using, in order, a token read from standard input, the
`PORT_LIGHT_RESERVATION_TOKEN` environment variable, or the locally saved
token for that server and port. A successful release removes the local record.
If the remote release succeeds but that cleanup fails, JSON output sets
`remote_released: true` and the command exits `3`, so automation can distinguish
the completed remote mutation from the stale local token.

```bash
port-light release 8000
printf '%s\n' "$TOKEN" | port-light release 8000 --token-stdin
```

## Output and exit status

Human-readable output is the default. `--json` emits one JSON object for both
successful commands and failures, including invalid arguments; its top-level
`schema_version` is currently `1`. `--help` and `--version` remain ordinary
terminal text. The flag may appear before or after the command.

| Exit | Meaning |
|------|---------|
| `0` | The command succeeded; for `check`, the port is free |
| `1` | A valid negative result, such as occupied, unhealthy, or no capacity |
| `2` | Invalid input or missing/unreadable local token state |
| `3` | Authentication, compatibility, network, TLS, server, or storage failure |

The client verifies HTTPS with the system trust store unless `--ca-file` or
`PORT_LIGHT_CA_FILE` selects a private CA bundle. URLs containing credentials,
query strings, or fragments are rejected, as are HTTP redirects. The CLI
negotiates named capabilities through `/api/meta`. Read-only checks can validate
responses from older servers, but reservation commands require the advertised
all-or-none allocation capability before changing server state. Install the CLI
and server from the same release when reservation support is required.

Run `port-light --help` or `port-light COMMAND --help` for the complete option
reference.

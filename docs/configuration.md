# Configuration

[← Documentation index](README.md)

Most options are available both as environment variables and in **Settings** (`#/settings`), where changes save automatically to `/data/port_light.json`. Deployment-only options — timeouts, filesystem paths, auth/webhook/agent secrets, and backend logging/webhooks/metrics — are configured through environment variables only.

Precedence defaults to `built-in default < environment variable < /data/port_light.json` (`PORT_LIGHT_SETTINGS_SOURCE=auto`). Set `PORT_LIGHT_SETTINGS_SOURCE=env` (or `SETTINGS_READONLY=1`) to keep Settings read-only; see [Settings (Compose and Web UI)](deployment.md#settings-compose-and-web-ui) for GitOps examples.

## Environment variables

### Scanning and Compose discovery

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT_LIGHT_SCANNERS` | `listen,docker,compose` | Enabled sources, comma-separated; select at least one. |
| `PORT_LIGHT_SCAN_TIMEOUT_S` | `10` | Background refresh deadline in seconds (1–60). A timeout retains the snapshot and marks it stale. Env-only. |
| `COMPOSE_SCAN_DIR` | `/compose` | Directory to scan for `compose.y*ml` / `docker-compose.y*ml`. Env-only. |
| `COMPOSE_SCAN_DEPTH` | `4` | Max subdirectory depth under the scan dir. |
| `COMPOSE_SCAN_EXCLUDE_DIRS` | unset | Comma-separated folder names to skip during automatic Compose discovery. Explicit `include` / `extends` files are still read. |
| `COMPOSE_SCAN_MAX_FILES` | `400` | Cap on compose files parsed per refresh. |

### Storage, network, and port range

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT_RANGE_START` | `1` | Start of the range used for the **free** summary count. |
| `PORT_RANGE_END` | `9999` | End of the free-count range. |
| `PORT_LIGHT_DATA_DIR` | `/data` | Manual ports, hidden list, and saved settings (JSON). |
| `PORT_LIGHT_PORT` | `2100` | HTTP port inside the container. |
| `CUSTOM_PORTS_FILE` | `/data/custom_ports.json` | Extra / overriding port names. Env-only. |

### Appearance and dashboard cards

| Variable | Default | Description |
|----------|---------|-------------|
| `THEME_MODE` | `system` | `system` / `dark` / `light`. |
| `THEME_PALETTE` | built-in | Palette: `gruvbox`, `catppuccin`, `solarized`, `nord`, `dracula`, `tokyo-night`, `one-dark`, `everforest`, `rose-pine`, `kanagawa`. Empty uses the built-in colors. |
| `LOCALE` | `auto` | `auto` / `en` / `fr` / `de` / `es` / `zh-CN` / `zh-TW` / `ja`. Auto follows the browser. |
| `GRID_DENSITY` | `standard` | Card-density preset: `loose`, `standard`, or `compact`. A stored legacy `comfortable` behaves as `standard`. |
| `SHOW_STATUS_TEXT` | `false` | Show short in-use / configured labels on cards. |
| `SHOW_ACCESS_BADGE` | `true` | Mark common browser-access ports on cards. |
| `SHOW_PROTOCOL_BADGE` | `true` | Show `udp` / `tcp,udp` protocol badges on non-TCP-only cards. |
| `SHOW_BIND_ADDRESSES` | `false` | Show compact bind-address summaries on occupied cards. |
| `SHOW_BIND_IPV4` | `true` | Include IPv4 addresses when card bind summaries are enabled. |
| `SHOW_BIND_IPV6` | `true` | Include IPv6 addresses when card bind summaries are enabled. |
| `COPY_ON_CLICK` | `true` | Copy the port number to the clipboard when clicking a card. |
| `AUTO_REFRESH` | `true` | Poll the occupancy map automatically on an interval. |
| `REFRESH_MS` | `5000` | Dashboard polling interval (1,000–300,000 ms). Settings offers 5s–5m choices and shows the recommended peer capacity. The local background scanner remains capped at a 30s interval. |
| `PORT_LIGHT_HOST_LAYOUT` | `waterfall` | Responsive waterfall showing all machines, or `tabs` showing one machine at a time. Both desktop and mobile honor this choice. |

### Access URL guessing

| Variable | Default | Description |
|----------|---------|-------------|
| `GUESS_URLS` | `true` | Add guessed `http(s)://host:port` links for known web/access ports. |
| `URL_HOST` | empty | Hostname used in guessed `http(s)://` links. |
| `URL_SCHEME` | `auto` | `auto` / `http` / `https`. |

### Multi-host and settings persistence

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT_LIGHT_SETTINGS_SOURCE` | `auto` | `auto`: Web UI values override env defaults. `env`: Compose is the only source and the Settings page is read-only. `file`: Web UI settings still fall back to env when unset, while `PORT_LIGHT_PEERS` is read only from the data file. |
| `SETTINGS_READONLY` | `0` | Set to `1` to keep Settings read-only while still reading saved file values. Env-only. |
| `PORT_LIGHT_HOST_NAME` | hostname | Label for this machine when other occupancy maps are shown. Also configurable under Settings → Machines & scanning. |
| `PORT_LIGHT_HOST_DESCRIPTION` | empty | Optional plain-text note under this machine's name in the multi-host view, up to 120 characters. |
| `PORT_LIGHT_PEERS` | unset | JSON array of up to 32 `{name, url, description?, username?, password?}` entries, used when the data file has no `peers` key or when `PORT_LIGHT_SETTINGS_SOURCE=env`. Descriptions are optional plain text, up to 120 characters. |

### Access control and security

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_USER` / `AUTH_PASSWORD` | unset | Optional HTTP Basic Auth for the UI and API. `/api/health` stays open. Both values must be nonempty; partial/blank configuration returns 503. Unset both to disable. Env-only. |
| `HIDDEN_UNLOCK_PASSWORD` | unset | If set (or if Basic Auth is set), hidden-from-grid ports are withheld from the API until you unlock; without Basic Auth, CLI/MCP allocation requires `AGENT_TOKEN` (`check` on unhidden free ports requires Basic Auth; hidden ports stay `404` unless unlocked). Env-only. |
| `AGENT_TOKEN` | unset | When set, suggestions and reservation creation/recovery require a matching `X-Agent-Token` header. Env-only. |

### Automation, observability, AI, and supporter

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT_LIGHT_LOG_LEVEL` | `warning` | Backend log level (`debug` / `info` / `warning` / `error`). Degraded scans (Docker unreachable, unreadable Compose file, …) log one line and show up in `/api/health` under `degradations`. Env-only. |
| `WEBHOOK_URL` | unset | Opt-in webhook target (`http(s)` only). With `WEBHOOK_EVENTS=new_listener,conflict`, Port-Light POSTs `{event, port}` JSON. |
| `WEBHOOK_SECRET` | unset | Sent as `X-Port-Light-Secret`. |
| `WEBHOOK_EVENTS` | unset | Comma list: `new_listener`, `conflict`. |
| `WEBHOOK_COOLDOWN_S` | `0` | Optional per-event/port cooldown in seconds after a port leaves `used` or `conflict` and re-arms (`v0.9.0+`). Env-only. |
| `HISTORY_RETENTION_DAYS` | `7` | Days of local port-occupancy history retained in SQLite (`0` disables history). Env-only. |
| `PORT_LIGHT_AI_PROXY` | unset | Optional HTTP(S) proxy URL for outbound BYOK AI requests. Env-only. |
| `METRICS_ENABLED` | unset | Set to `1` to expose `GET /api/metrics` (Prometheus text format: used/configured/free counts, hidden, degradations, Compose files). Aggregates only — never ports or names. Env-only. |
| `PORT_LIGHT_SUPPORTER_KEY` | unset | Optional offline Ed25519 Supporter Key (`PL1.…`) verified locally with zero outbound requests. Also configurable in **Settings → Supporter Key** when writable. |
| `PORT_LIGHT_SUPPORTER_SHOW_HEADER` | `true` | Show the supporter nameplate in the top bar when a valid Supporter Key is active. |

Related environment variables documented in their respective guides:

- Docker socket proxy (`DOCKER_HOST`): [Deployment — Docker socket proxy](deployment.md#docker-socket-proxy)
- CLI and MCP client (`PORT_LIGHT_URL`, `PORT_LIGHT_AUTH`, `PORT_LIGHT_AGENT_TOKEN`, `PORT_LIGHT_STATE_DIR`, `PORT_LIGHT_TIMEOUT`, `PORT_LIGHT_CA_FILE`): [CLI — Configure](cli.md#configure)
- Optional BYOK AI settings: [Troubleshooting and BYOK — Optional AI](analysis.md#optional-ai)

## Access control

Set `AUTH_USER` and `AUTH_PASSWORD` to enable Basic Auth for the dashboard and API. Use an HTTPS reverse proxy for public deployments.

With Basic Auth or `HIDDEN_UNLOCK_PASSWORD` enabled, hidden ports require unlocking before API access. Otherwise, hiding a port affects its display only. See [SECURITY.md](../SECURITY.md).

## Custom port names

Use [custom_ports.example.json](../custom_ports.example.json) as a template for custom port names. Create the file before bind-mounting it.

## Data and privacy

Port-Light has no telemetry. Outbound HTTP requests serve configured peer queries, webhooks, and explicitly confirmed BYOK model requests. Webhooks send `{event, port}`; model requests send the selected sanitized evidence to your configured provider.

Dashboard and API users can read scan results, machine descriptions, and port rules. Configured hubs also receive this data. Compose `.env` files are used locally for variable substitution. Doctor reports contain sanitized summaries.

Settings, labels, and history are stored in the data volume. `port_light.json` may contain peer passwords and a UI-saved Supporter Key, and `analysis/byok/profile.json` stores UI-saved BYOK API keys (see [storage and access](analysis.md#storage-and-access)). CLI release credentials use a private local state directory (`/data/cli-state` in the container). Browser reservation credentials use tab session storage, and copied release commands contain tokens. Protect sensitive information in the data volume, commands, and screenshots.

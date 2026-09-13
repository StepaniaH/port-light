# Install or update Port-Light for an AI tool

Use this guide when the user asks to connect an AI coding tool to an existing
Port-Light instance. It installs the client integration, not the Port-Light
server. Keep existing server deployments and unrelated AI configuration intact.

## Identify the target

- Use the instance URL supplied by the user. If this document was fetched from
  an instance's `/ai-setup.md`, its base URL is that instance, including any
  reverse-proxy path prefix. A GitHub documentation URL is not an instance URL.
- If the instance guide requires authentication or cannot be fetched by the AI's
  web reader, use the public guide at
  <https://raw.githubusercontent.com/StepaniaH/port-light/main/docs/ai-setup.md>.
  Keep the user's instance URL as the target. Public documentation does not give
  a cloud agent access to a LAN server; check connectivity from local execution.
  If the public guide is unavailable too, use this file from the verified source
  checkout. Do not treat an HTML sign-in page as installation instructions.
- Detect the current AI tool and its existing MCP/skill configuration. Reuse the
  current installation scope and connection settings when updating. For a new
  integration, prefer the current project; use a personal/global scope when the
  user requests it. Ask only for a missing instance URL or an ambiguous target.
- Check the URL from the environment that will run the client. `localhost` means
  that environment, not necessarily the server or the user's browser. A cloud
  agent may be unable to reach a LAN instance; report that limitation without
  exposing the server publicly or changing its firewall.
- GET `<instance>/api/health` identifies the running version and whether Basic
  Auth is required. Then GET `/api/meta` using the configured credentials to
  check capabilities. `PORT_LIGHT_AUTH=user:password` supplies Basic Auth;
  `PORT_LIGHT_AGENT_TOKEN` supplies the separate `X-Agent-Token` gate. Preserve
  configured secret references; request missing secrets through the tool's
  secure configuration flow, not by putting them in the chat or a URL.

## Install the matching client

Python 3.11+ is required. The distribution is named `port-light-cli`; it includes
both the `port-light` CLI and, since v0.8.4, `port-light-mcp`.
There are no runtime Python dependencies. Use one of these routes:

### AI runs on a computer that can reach the instance over HTTP

Install the same **published release** as the server into an isolated tool
environment. Replace `vX.Y.Z` with the version verified through `/api/health`:

```bash
uv tool install 'git+https://github.com/StepaniaH/port-light.git@vX.Y.Z'
# Alternatively:
pipx install 'git+https://github.com/StepaniaH/port-light.git@vX.Y.Z'
```

For updates, use the existing tool manager and an explicit matching version
(`uv tool install --force ...` or `pipx install --force ...`). Reuse a verified
local checkout when the user is developing Port-Light. A matching release wheel
from GitHub Releases also works. Do not silently install `main`, an unverified
package of a similar name, or upgrade the server to match a newer client.

Older releases may contain only the CLI. Check the installed entry points; use
CLI + skill if `port-light-mcp` is absent, or retain an existing source/container
MCP entry point. Do not register an entry point that is absent from the installed version. For a source checkout, `python /absolute/path/to/mcp/server.py`
continues to work from any directory.

### AI can access the Docker daemon hosting Port-Light

The running image already includes the adapter and CLI. Use the actual container
name; a Docker installation on a different laptop does not expose the NAS daemon.

```json
{
  "mcpServers": {
    "port-light": {
      "command": "docker",
      "args": ["exec", "-i", "-e", "PORT_LIGHT_URL", "port-light", "python", "/app/mcp/server.py"],
      "env": {"PORT_LIGHT_URL": "http://127.0.0.1:2100"}
    }
  }
}
```

Here the URL uses the **internal** `PORT_LIGHT_PORT`, not the published host port.
If authentication is needed, add `PORT_LIGHT_AUTH` / `PORT_LIGHT_AGENT_TOKEN` to
`env` using the AI tool's supported secret mechanism, and add a separate
`"-e", "VARIABLE_NAME"` pair **before** the container name for each. Host process
`env` alone is not forwarded by `docker exec`. The server's `AGENT_TOKEN` is
already inherited inside its container. Do not use `-t`: MCP requires clean stdio.
The standard image keeps reservation state in `/data/cli-state`; preserve that
volume during updates. CA files must exist inside the client environment too.

## Register MCP and the collaboration skill

The `mcp-config` and `verify` commands require v0.8.4 or later. Check
`port-light --help` on older installations. Generate the configuration with
the installed `port-light`:

```bash
port-light --url http://your-host:2100 mcp-config --client codex
port-light --url http://your-host:2100 mcp-config --client claude-code
```

Run only the command for the current tool. It prints configuration; it does not
edit AI settings or claim registration. It records an absolute launch path and
state directory, preserving a virtual environment's Python path. A source
checkout uses its absolute `mcp/server.py` wrapper, so keep that checkout in place.
For an older adapter, use its verified executable path and consult the client's
local help or official documentation for registration.

### Codex

Merge the generated `[mcp_servers.port-light]` and its `env` table into the
existing project `.codex/config.toml`, or preserve `~/.codex/config.toml` scope
when updating an existing personal installation. Do not append duplicate tables
or redirect output over the whole file. Preserve custom timeouts, tool filters,
approval settings and other MCP entries. `env_vars` forwards the named secrets
from the environment that launches Codex; it does not store their values.

Check the entry with `codex mcp get port-light --json`. This confirms stored
configuration only. Reload the tool and use `/mcp` in the CLI to inspect the
loaded connection, then call `doctor` in an AI task. Install the skill at
`.agents/skills/port-light/SKILL.md` for the project, or
`~/.agents/skills/port-light/SKILL.md` for a requested personal scope.
See [Codex MCP](https://developers.openai.com/codex/mcp) and
[skills](https://developers.openai.com/codex/skills).

### Claude Code

Merge just `mcpServers.port-light` from the generated JSON into the project's
`.mcp.json`, preserving other entries and custom Port-Light options. The generated
`${VARIABLE:-}` references use Claude Code's environment expansion; missing
optional credentials become empty strings. Ensure required credentials are in
the environment that launches Claude Code, then reload it and inspect `/mcp`.
Follow its project-server trust prompt when shown and call `doctor` in a task.

For an existing local or user installation, retain its scope. The registration
command `claude mcp add-json port-light '<server-json>' --scope local` expects the
**inner server object**, not the complete `mcpServers` document. Check local help
before modifying an existing entry. Install the project skill at
`.claude/skills/port-light/SKILL.md`, or use `~/.claude/skills/port-light/SKILL.md`
for an explicitly requested personal scope. See [Claude Code MCP](https://code.claude.com/docs/en/mcp)
and [skills](https://code.claude.com/docs/en/skills).

For either client, GUI launches may not inherit terminal environment variables.
Use the tool's supported local secret configuration when needed; do not paste
credentials into the conversation or tracked project files. Keep the exact
instance URL and `PORT_LIGHT_STATE_DIR` on updates, including when moving from
a source wrapper to an installed wheel. Never delete state during reinstallation.

### Skill and other tools

Install the self-contained skill from `<instance>/skill.md` into the current
AI tool's supported skill directory as `port-light/SKILL.md`, creating the parent
directory first. If that endpoint is absent, read `skills/port-light/SKILL.md`
from the **same tagged source** used for the client. Preserve local additions
when updating; do not overwrite an unrelated skill with the same folder name.
Record the non-secret instance URL in the client's configuration. If MCP is not
supported, use CLI + skill with the same persistent environment and state folder.
A tool with no local execution or LAN access needs a reachable execution environment.

The skill format is documented at <https://agentskills.io/specification>.
MCP registration is client-specific; see the current tool's official documentation.

## Verify without reserving anything

1. Run `port-light --version`, then `port-light verify --json` with the configured
   URL, credentials and state directory. It starts the actual adapter, tests the
   handshake, tool list, instance diagnostics, read-only agent-token gate and
   local state writability. It never reserves a port. It may add a read-only
   suggestion to the instance's activity history. If `verify` is unavailable,
   run `port-light doctor` and continue with the manual checks below. In Docker,
   run the matching commands with `docker exec` and the same forwarded variables.
2. For MCP, perform `initialize`, `notifications/initialized` and `tools/list`
   through the AI tool or a stdio test. Verify the tools actually exposed by the
   installed version. Releases since v0.8.4 include `doctor`, `reserve_ports`
   and `release_port`; older adapters have `check_port` and `suggest_ports`.
3. Call MCP `doctor` if available. A healthy `/api/health` does not validate the
   agent token; if one is configured, a read-only `suggest_ports` call with no
   `reserve` or `ttl` verifies that gate. Never create a test reservation merely
   to check installation.
4. Report the instance, client and server versions, files changed, and checks that passed.
   Distinguish “configuration written; reload required” from “connected and
   verified”. If a scan needs attention, give its diagnostic cause; do not call
   it a free-port result or disable scanners to make the check pass.

`verify` always reports `ai_registration: "not_checked"`: it cannot inspect or
reload the host AI tool. Only an actual tool call from that AI environment verifies
the final step. Report missing network access, credentials or reload separately.

A successful update preserves the instance URL, secrets, registration scope,
custom skill guidance and reservation state. Repeating setup should update the
same integration rather than creating duplicate MCP entries or skill copies.

## Everyday collaboration

Try: “Choose two ports for this project's web and API services and update its
Compose mappings.” The agent should respect existing mappings and port rules,
reserve when it is ready to start the services, use a project/service label,
and report the selected host and ports. Temporary work uses expiring leases.
After the service stops or the work is abandoned, release only its own claims.
Port-Light does not stop processes, edit Compose files or deploy containers; the
AI tool performs those actions only within the user's development task.

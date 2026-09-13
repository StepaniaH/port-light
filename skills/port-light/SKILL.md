---
name: port-light
description: Check host port occupancy and reserve ports when choosing development server ports, editing Docker Compose port mappings, or investigating address-already-in-use errors with Port-Light.
---

# Port-Light

Use the configured Port-Light instance for the host where the service will run.
The AI client's machine and the monitored host may differ. Keep existing project
ports and named port rules when they still fit the task; a conflict alone does
not authorize stopping another service or changing an unrelated deployment.

## Choose and use ports

- Inspect existing mappings and check their ports before proposing replacements.
  For planning, use `check_port` / `suggest_ports` without `reserve` or `ttl`.
- When ready to start a new service, use `reserve_ports` with a short
  `project/service` label and the needed count, range or `rule`. It claims the
  whole count or fails. The default lease is one hour; `ttl` accepts 60–604800
  seconds. Let the task determine the duration.
- Use the returned ports in the intended configuration and start promptly.
  Reservations coordinate Port-Light clients but do not bind OS sockets.
  If binding still fails, check the conflict, release your unused claim, and
  choose a replacement within the task's allowed range.
- Release with `release_port(port=...)` after stopping or abandoning the service.
  The client uses its privately saved token. Keep the same URL and state folder.
  Release does not stop a process. Do not release a still-needed reservation just
  because the conversation is ending; report its expiry if the service stays up.

Prefer MCP when connected; use the CLI when only shell execution is available.
Older MCP adapters lack `reserve_ports`: use `suggest_ports(reserve=true,
 ttl=3600, ...)` and retain its returned token for release, or use the CLI.
Never use both interfaces to reserve the same service twice.

## CLI equivalent

```bash
port-light doctor
port-light check 5432
port-light reserve --count 2 --start 8000 --end 8999 --ttl 1h --label my-project/preview
```

Record the returned port numbers. After each service stops, run
`port-light release <port>` for that service's actual reserved port.

`check` exits 0 for free and 1 for occupied/configured; an error is not a free
port. CLI reservations also default to one hour. Persistent claims require
explicit `--no-expiry`. Release uses the saved token. `--json` supplies structured
CLI output but reservation JSON contains secret tokens: keep it out of shared logs.
The new MCP `reserve_ports` saves tokens and omits them from tool results.

## Failures and scope

- After an uncertain reservation failure, retry identical arguments against the
  same URL and private state folder. Pending requests recover the original claim.
  Do not change the label, range or interface while the outcome is uncertain.
  For recovery without allocating, run `port-light requests` and
  `port-light recover <request-id>`; neither creates new claims or extends TTL.
- On unreachable, stale or incomplete occupancy, use `doctor` (or CLI
  `port-light doctor`) and explain the cause. Do not assume missing ports are free
  or remove required scanners to force allocation.
- `scope=self` checks the selected host. Use `scope=all` when the task needs to
  avoid configured peers too; every peer must provide a complete, unlocked map.
  Claims are still saved only on the selected instance. Independent instances
  do not share a distributed lock.

## Connection

`PORT_LIGHT_URL` selects the instance. Optional `PORT_LIGHT_AUTH=user:password`
and `PORT_LIGHT_AGENT_TOKEN` supply Basic Auth and the separate allocation gate.
`PORT_LIGHT_STATE_DIR` holds private tokens and pending requests; keep it stable.
Do not include credentials in URLs, labels, project instructions or chat output.

If setup is missing, read `<instance>/ai-setup.md` using the supplied instance URL.
It explains client installation, MCP registration and verification. Ask for the
instance address only if the task and existing configuration do not identify it.
If a web reader cannot access the instance guide, use
https://raw.githubusercontent.com/StepaniaH/port-light/main/docs/ai-setup.md
with the same target URL. This does not make an unreachable LAN server accessible.
On clients that offer it, `port-light verify` tests the local adapter and access
without reserving ports. It does not confirm that the AI tool loaded its config;
reload the tool and call MCP `doctor` to check that final step.

Report the chosen host, ports, relevant configuration changes and lease expiry
briefly. For failures, state the cause and next action. Keep successful check
results concise.

# Port management

The management pages and grouping controls are available in the development branch after v0.8.2.

## Group projects and services

Use **Group by** beside Sort to switch between individual ports, Compose projects, and services. A consecutive run of four or more ports is collapsed when its status, protocol, and conflict state match. Open the range to inspect or act on individual ports. Searching a port number shows the matching port directly. Search and sort preferences apply to the underlying port records.

A port declared by multiple projects appears in each applicable group. Project folders distinguish projects with identical display names. Rows without Compose declarations appear under Other ports.

## Review conflicts

Open **Manage ports → Conflicts**. This page shows local declarations with overlapping binds across Compose projects, plus declarations outside an assigned rule. Each entry identifies its project, service, source file, bind address, protocol, and target port. Open the port link for its complete occupancy details.

Choose a rule or enter a range, then select **Suggest replacement**. The result preserves the target, protocol, and bind address; Swarm mappings use `deploy.ports`. Replace the selected mapping in the displayed file while keeping its other mappings. This is a reviewable example, not a complete Compose document or an override file. For a range, split the original mapping as needed; for inherited values, edit the declaration that supplies them. Port-Light does not edit Compose files.

Host-network and macvlan/ipvlan listener declarations need changes to the service listener and do not receive a published-port replacement. A suggestion does not reserve a port. Recheck occupancy before starting the service.

## Manage reservations

**Manage ports → Reservations** lists local manual entries and reservations. Filter by machine or expiry type; use Refresh to reload the list after external changes or expiry. New reservations can specify a label, count, range, rule, and duration. Leave the duration blank for a permanent reservation. When `AGENT_TOKEN` is configured, enter it for allocation requests.

The browser retains its request key before the POST. If the response is lost, reload the page and use **Retry pending request**. It reuses the same key and parameters. Successful responses retain release tokens in this tab’s session storage. The list API returns no release credentials, so another tab or client cannot release these reservations from this page.

Copy release commands before closing the tab, particularly for permanent reservations. With Basic Auth enabled, add your authentication credentials to the command. Treat copied commands as secrets: anyone with the required authentication and release token can release that reservation. Browser session storage survives reloads but is not a durable credential backup. Use the CLI for reservations that must remain recoverable after closing the browser.

Manual entries continue to use the port detail editor. Rule changes do not move or release existing reservations.

## Configure port rules

Under **Manage ports → Port rules**, create a named inclusive range, such as `development: 20000–29999` or `infrastructure: 10000–19999`. Names contain letters, digits, underscores, and hyphens. Optionally assign exact Compose project names, separated by commas.

Each project can be assigned to one rule. Ranges may overlap. Unassigned projects are not flagged. Saving an existing name updates its rule. A declaration outside its assigned range remains visible, with a warning on its card and in the conflict list.

Rules apply when explicitly selected for allocation. They do not reserve the whole range, bind sockets, change service configurations, or restrict allocations that omit a rule. Existing toolbar block reservations still use their selected numeric range. Settings read-only mode prevents rule edits.

```bash
port-light reserve --rule development --count 2 --ttl 3600 --label wiki-test
```

An explicit `--start` or `--end` must lie inside the rule. CLI and MCP check the server’s `port_rules` capability before using a rule. See [integrations](integrations.md#port-rules) for the API format.

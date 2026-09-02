---
name: port-light
description: Resolve genuinely free network ports on hosts running Port-Light before starting servers, dev previews, or writing docker-compose publish blocks. Use whenever you need to pick a port, check whether a specific port is taken, or reserve a range so later work does not collide.
---

# Port-Light

Port-Light is an occupancy map for host ports. It merges the OS listen
table, Docker publishes, Compose declarations, and manual reservations into
one answer: which ports are actually free.

## When to use

- Before binding a dev server or preview to a port
- Before writing `ports:` sections in a docker-compose file
- After a "port already in use" error, to find the nearest alternative
- Before asking "is X running on port N?"

## Setup

All calls go to a Port-Light instance over HTTP. Point the variable at
whatever URL you use to open that instance's dashboard in a browser
(localhost, LAN, or Tailscale):

```bash
export PORT_LIGHT_URL="http://<your-host>:<your-port>"
# optional, when the instance has Basic Auth:
export PORT_LIGHT_AUTH="user:password"
# optional, when the instance sets AGENT_TOKEN:
export PORT_LIGHT_AGENT_TOKEN="agent-token"
```

## Pick free ports

```bash
curl -s -H "X-Agent-Token: $PORT_LIGHT_AGENT_TOKEN" \
  "$PORT_LIGHT_URL/api/ports/suggest?count=1&reserve=true&label=my-preview"
```

```json
{"ports": [8081], "reserved": [8081], "reservations": [{"port": 8081, "token": "<save-this-token>", "expires_at": null}], "failed": [], "range": {"start": 1, "end": 9999}}
```

Use the returned ports. With `reserve=true` they are recorded as configured,
so repeated suggestions never hand out the same ports again. Save each
reservation token: it is returned once and is required to release that claim.

Parameters: `count` 1–64 (default 1), `start` / `end` narrow the search
window, `label` annotates the reservation.

## Check one port

```bash
curl -s "$PORT_LIGHT_URL/api/ports/5432?include_hidden=false" | jq '.status,.process'
```

Status is `used`, `configured` (declared but not listening) or `free`.

## Release a reservation

```bash
curl -s -X DELETE -H "X-Reservation-Token: <returned-token>" \
  "$PORT_LIGHT_URL/api/reservations/8081"
```

## Notes

- A port reported free can still be claimed by something else between the
  call and your bind. A reservation serializes Port-Light agents but does not
  bind an operating-system socket; bind promptly.
- `scope=all` fails without reserving if any configured peer cannot provide a
  complete, unlocked occupancy map.
- Hidden ports are excluded from suggestions and withheld by the server
  unless the request carries the unlock header.
- Full API reference: `docs/integrations.md` in the Port-Light repository.

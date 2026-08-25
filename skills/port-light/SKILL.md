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
```

## Pick free ports

```bash
curl -s "$PORT_LIGHT_URL/api/ports/suggest?count=1&reserve=true&label=my-preview"
```

```json
{"ports": [8081], "reserved": [8081], "failed": [], "range": {"start": 1, "end": 9999}}
```

Use the returned ports. With `reserve=true` they are recorded as configured,
so repeated suggestions never hand out the same ports again.

Parameters: `count` 1–64 (default 1), `start` / `end` narrow the search
window, `label` annotates the reservation.

## Check one port

```bash
curl -s "$PORT_LIGHT_URL/api/ports/5432?include_hidden=false" | jq '.status,.process'
```

Status is `used`, `configured` (declared but not listening) or `free`.

## Release a reservation

```bash
curl -s -X DELETE "$PORT_LIGHT_URL/api/manual-ports/8081"
```

## Notes

- A port reported free can still be claimed by something else between the
  call and your bind. Reserve first when the sequence matters.
- Hidden ports are excluded from suggestions and withheld by the server
  unless the request carries the unlock header.
- Full API reference: `docs/integrations.md` in the Port-Light repository.

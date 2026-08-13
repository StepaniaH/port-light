# Roadmap

Status as of **2026-08-13**. This is a plan, not a promise. Port-Light is a one-person homelab utility (~2.1k lines, MIT, image `stepaniah/port-light`).

## Where it stands

| Signal | Value |
|--------|--------|
| GitHub | 1 star, 0 forks, 0 issues, 0 Releases, empty topics |
| Docker Hub | ~295 pulls, amd64+arm64, tags `v0.1.0`–`v0.4.0` + `latest` |
| Last code | 2026-07-25 |
| Tests / CI (non-publish) | none |
| Product Hunt | listed |

The wedge is real: **a port occupancy map** that merges host listeners, Docker mappings, and Compose declarations. Adjacent tools (Portainer, Dozzle, Dockpeek, Homepage) are container panels, log viewers, or launchers. Competing with them is a losing move.

The trust gap is also real. README used to advertise multi-machine support and password-protected hidden ports. The machine dropdown is dead. Hide uses `localStorage` and an unauthenticated API. Homelab users who mount `docker.sock` will notice.

**Strategy in one line:** make the open-source tool honest and sharp for 12 months; only then consider a node-priced Pro around agents, SSO, and audit. Do not sell a license now. Do not build a SaaS scanner.

## What not to build

- Container start/stop, logs, image updates, CPU/RAM
- Another “homepage” of bookmarks
- Kubernetes control plane UI
- Cloud dashboard that ingests `docker.sock` or Compose trees

Those paths collide with larger projects and abandon the only unique question: *can I bind this port, and who already claimed it?*

---

## Product / engineering

### P0 — Trust (done in 0.5.0)

1. **Advertising = code.** Multi-host UI removed. Hide-from-grid documented as a display filter; API withholds rows when `AUTH_*` or `HIDDEN_UNLOCK_PASSWORD` is set.
2. **Optional auth.** `AUTH_USER` / `AUTH_PASSWORD` Basic Auth. `/api/health` stays open.
3. **Socket story.** [docker-socket-proxy](deployment.md#docker-socket-proxy) documented. Default `NET_ADMIN` removed from the dev Compose file.
4. **Versions.** App version is `0.5.0`. Publish workflow also pushes GHCR. GitHub Releases are still a maintainer step at tag time.

### P1 — Make the map correct

This is the product.

- Attribute **host-network** containers (today they show as nameless listeners).
- Mark bind address: `0.0.0.0` vs `127.0.0.1` vs LAN IP (a port bound to localhost is not “taken” for LAN services).
- UDP. IPv4/IPv6 dedup.
- Compose: `include:`, profiles, full published ranges, deeper trees (with a size cap).
- Load `custom_ports.json` once (it is read per port today).
- Re-fetch when the range inputs change.
- Either wire the machine selector or delete it.
- Detail panel: open `http(s)://host:port` when it is an access port; optional Traefik/Caddy label URL.

Polling every 5s is fine until scanners get expensive. SSE is polish, not the bottleneck.

### P2 — Engineering hygiene

- Pytest for `_parse_short_port`, `/proc` hex, classify, conflicts.
- Ruff + a CI workflow on pull requests.
- Image smoke: `curl -f localhost:2100/api/health`.
- `/api/health` should report whether Docker and `/host/proc` actually worked (without leaking data).
- Keep the JSON file store until there is more than one writer.

### P3 — Later, maybe

- Read-only **agent** JSON schema (snapshot of ports from another host). Design the payload before the UI.
- Podman / rootless notes from a real user, not speculation.
- Dark/light that is not “GitHub dark only.”
- Known-port catalog as a community JSON PR, still MIT.

Do not start fleet UI until P0–P1 are boringly solid.

---

## Open source

License stays **MIT**. No CLA, no BSL, no “source available” bait before there is a community.

### Hygiene (cheap, do now)

- Topics: `self-hosted`, `homelab`, `docker`, `docker-compose`, `ports`, `fastapi`, `dashboard`
- GitHub Release notes per tag
- GHCR (`ghcr.io/stepaniah/port-light`) — many compose users prefer it
- Docker Hub description + full README (absolute screenshot URL)
- Issue templates and a handful of **good first issues** so the tracker is not empty
- `FUNDING.yml` with only working platforms (Ko-fi is already set)

### Distribution (3–6 months)

Highest leverage for this audience, in order:

1. [awesome-selfhosted](https://github.com/awesome-selfhosted/awesome-selfhosted) — Monitoring or Miscellaneous
2. One honest Show & Tell on r/selfhosted and r/homelab **after** P0 docs
3. Unraid Community Applications template
4. CasaOS / TrueNAS SCALE app metadata if someone uses those
5. Homelab charts / “awesome compose” lists

Skip Docker Desktop extensions and CNCF lists. Wrong room.

### Community

Empty issue trackers look abandoned. File the known gaps as issues (host-network names, UDP, compose include, optional auth) and label `good first issue` where a parser test is enough.

Keep the frontend build-free. That is why a stranger can patch `app.js` in a weekend.

Chinese + English READMEs: the author and part of the user base are Chinese-speaking; Docker Hub, Reddit, and awesome-selfhosted are English-first.

### 12-month OSS milestones

| Window | Bar |
|--------|-----|
| 0–3 months | Docs match code; CI; Releases; topics. New users are not surprised. |
| 3–6 months | Listed in awesome-selfhosted; Unraid template; real issues from other machines. |
| 6–12 months | External PRs; known_ports contributions; agent schema drafted. |

Stars are a lagging vanity metric. Better: “I run this on Unraid,” a compose-parser bug with a fixture, a custom_ports PR.

---

## Commercialization

### Do not commercialize the core in 2026

295 pulls and one star is “a few people tried the image,” not a market. Charging now taxes the only growth loop (self-hosted word of mouth) and invites comparisons with Dockpeek, which already has auth and multi-host.

Homelab tools that made money (Portainer Business) did it **after** becoming the default panel. Dozzle stayed donation-ware at a much larger scale. Copy the second path until the first is forced on you.

### SaaS is structurally wrong

The scanners need `docker.sock`, host `/proc`, and the user’s Compose directory. None of that should leave the LAN. A cloud “port dashboard” has nothing to scan. Privacy is already a selling point; do not invert it.

### Models that can work, in order

| Model | When | Notes |
|-------|------|--------|
| Ko-fi / GitHub Sponsors | Now | Already linked. Zero product cost. |
| Listings (Unraid, etc.) | 0–12 months | Distribution, not invoices. |
| Open core **Pro** | After roughly 1k+ real deploys *or* repeated “I need SSO / 12 NASes” | Core map stays MIT. |
| MSP / small-IT fleet | Only with working agents + auth | This is where actual budgets live. |
| Relicense / dual license | Not before a community exists | Burns trust for no revenue. |

### If Pro happens, what is behind the paywall

Charge for **team and fleet** problems:

- Read-only agents and a single pane for N machines
- OIDC / SSO, read-only roles, audit log
- Alerts: new listener, compose conflict, bind on `0.0.0.0` that used to be localhost

Never paywall: the grid, smart search, Compose conflicts, known_ports, single-host Docker/Compose merge. That *is* the project.

Hypothetical pricing (not a commitment): personal tip $3–5/month or one-off; Pro per node $2–5/month or a small-team annual. Ignore the numbers until someone asks to pay.

### Competitive stance

- **Dockpeek** — stronger general Docker dashboard (auth, multi-host, Traefik, `:free` search). Stay port-grid, not container-table.
- **Portainer / Dozzle** — management and logs. Complementary. Docs should say “use beside Portainer,” not “lite Portainer.”

---

## Suggested sequencing

```
P0 honesty + security docs     weeks 1–4
P1 map correctness + tests     months 2–4
Distribution posts + Unraid    after a tagged P0/P1 release
Agent schema (no UI)           if two or more users run multiple NASes
Pro discussion                 only with that demand in writing
```

Effort mix for the next six months: ~35% trust, ~30% map correctness, ~25% distribution, ~10% commercial experiments (Sponsors copy, not a shop).

## Success criteria (personal)

Worth continuing if, within a year, strangers file bugs from real hardware and the README no longer overclaims.

Worth a Pro experiment if several operators run 5+ hosts and ask for SSO in public.

Worth pausing if P0 is done and pulls stay flat with no issues — then it stays a private homelab script, which is a valid outcome for a vibe-coded tool.

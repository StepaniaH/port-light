"""Port-Light backend — FastAPI app."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import port_store, settings as app_settings
from .auth import (
    auth_configured,
    basic_auth_middleware,
    hidden_ports_withheld,
    hidden_unlock_configured,
    request_may_see_hidden,
)
from .compose_scanner import scan_compose_files
from .docker_scanner import docker_available, safe_http_url, scan_containers
from .known_ports import get_known_port
from .port_scanner import host_proc_available, scan_listening_ports

VERSION = "0.5.3"

app = FastAPI(title="Port-Light", version=VERSION)


async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return response


app.middleware("http")(basic_auth_middleware)
app.middleware("http")(security_headers_middleware)

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class ManualPortCreate(BaseModel):
    port: int = Field(ge=1, le=65535)
    label: str = ""
    machine: str = "localhost"


class ManualPortUpdate(BaseModel):
    label: str = ""
    machine: str = "localhost"


def _compose_dir() -> str:
    return os.environ.get("COMPOSE_SCAN_DIR", "/compose")


def _values() -> dict:
    values, _ = app_settings.resolve()
    return values


@app.get("/api/meta")
def meta() -> dict:
    values, _ = app_settings.resolve()
    return {
        "version": VERSION,
        "auth_required": auth_configured(),
        "hidden_unlock_required": hidden_unlock_configured(),
        "hidden_ports_withheld": hidden_ports_withheld(),
        "settings_readonly": app_settings.settings_readonly(),
        "refresh_ms": values["refresh_ms"],
        "theme": values["theme"],
        "grid_density": values["grid_density"],
    }


@app.get("/api/health")
def health() -> dict:
    compose_dir = _compose_dir()
    return {
        "status": "ok",
        "version": VERSION,
        "auth_required": auth_configured(),
        "scanners": {
            "proc": host_proc_available(),
            "docker": docker_available(),
            "compose": os.path.isdir(compose_dir),
        },
    }


def _occupancy(
    request: Request,
    range_start: int | None,
    range_end: int | None,
    include_hidden: bool,
) -> dict:
    values = _values()
    start = range_start if range_start is not None else values["port_range_start"]
    end = range_end if range_end is not None else values["port_range_end"]
    if end < start:
        end = start
    may_see = request_may_see_hidden(request)
    show_hidden = bool(include_hidden and may_see)
    return _classify(
        scan_listening_ports(),
        scan_containers(),
        scan_compose_files(
            _compose_dir(),
            max_depth=values["compose_scan_depth"],
            max_files=values["compose_scan_max_files"],
        ),
        port_store.get_manual_ports(),
        port_store.get_hidden_ports(),
        start,
        end,
        show_hidden,
        hidden_locked=hidden_ports_withheld() and not may_see,
        options=values,
    )


def _json_etag(payload: dict) -> tuple[str, str]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return body, f'"{digest}"'


@app.get("/api/settings")
def get_settings() -> dict:
    return app_settings.snapshot()


@app.put("/api/settings")
def put_settings(body: dict = Body(...)) -> dict:
    try:
        return app_settings.apply_patch(body)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ports")
def get_ports(
    request: Request,
    range_start: int | None = Query(default=None, ge=1, le=65535),
    range_end: int | None = Query(default=None, ge=1, le=65535),
    include_hidden: bool = Query(default=False),
) -> Response:
    payload = _occupancy(request, range_start, range_end, include_hidden)
    body, etag = _json_etag(payload)
    headers = {"ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@app.get("/api/ports/{port}")
def get_port(
    port: int,
    request: Request,
    include_hidden: bool = Query(default=False),
) -> dict:
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    payload = _occupancy(request, 1, 65535, include_hidden)
    for row in payload["ports"]:
        if row["port"] == port:
            return row
    if port in port_store.get_hidden_ports() and payload["summary"]["hidden_locked"]:
        raise HTTPException(status_code=404, detail="not found")
    known = get_known_port(port)
    return {
        "port": port,
        "status": "free",
        "source_type": "unknown",
        "protocol": "tcp",
        "known_service": known,
        "is_hidden": False,
        "conflict": False,
        "urls": [],
        "containers": [],
        "compose_configs": [],
    }


@app.get("/api/known-ports/{port}")
def known_port(port: int) -> dict:
    known = get_known_port(port)
    if not known:
        raise HTTPException(status_code=404, detail="unknown port")
    return {"port": port, **known}


@app.get("/api/manual-ports")
def list_manual_ports() -> dict:
    return {"manual_ports": port_store.get_manual_ports()}


@app.post("/api/manual-ports")
def add_manual_port(body: ManualPortCreate) -> dict:
    entry = port_store.add_manual_port(body.port, body.label, body.machine)
    return {"status": "ok", "entry": entry}


@app.patch("/api/manual-ports/{port}")
def patch_manual_port(port: int, body: ManualPortUpdate) -> dict:
    entry = port_store.update_manual_port(port, body.label, body.machine)
    if not entry:
        raise HTTPException(status_code=404, detail="not found")
    return {"status": "ok", "entry": entry}


@app.delete("/api/manual-ports/{port}")
def del_manual_port(port: int, machine: str = Query(default="localhost")) -> dict:
    removed = port_store.remove_manual_port(port, machine)
    if not removed:
        raise HTTPException(status_code=404, detail="not found")
    return {"status": "ok"}


@app.get("/api/hidden")
def list_hidden(request: Request) -> dict:
    if hidden_ports_withheld() and not request_may_see_hidden(request):
        return {"hidden_ports": [], "locked": True}
    return {"hidden_ports": port_store.get_hidden_ports(), "locked": False}


@app.post("/api/hidden/{port}")
def hide_port(port: int) -> dict:
    added = port_store.add_hidden_port(port)
    return {"status": "ok" if added else "already_hidden"}


@app.delete("/api/hidden/{port}")
def unhide_port(port: int) -> dict:
    removed = port_store.remove_hidden_port(port)
    if not removed:
        raise HTTPException(status_code=404, detail="not found")
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        _FRONTEND_DIR / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "icon.png")


app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")


def _classify(
    listening: list,
    containers: list,
    compose_ports: list,
    manual_ports: list[dict],
    hidden_ports: list[int],
    range_start: int,
    range_end: int,
    include_hidden: bool,
    hidden_locked: bool,
    options: dict | None = None,
) -> dict:
    hidden: set[int] = set()
    for raw in hidden_ports or []:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 65535:
            hidden.add(n)
    hidden_ports = list(hidden)

    listening_map: dict[int, dict] = {}
    inode_to_port: dict[int, int] = {}
    for lp in listening:
        if lp.port < 1 or lp.port > 65535:
            continue
        if lp.inode:
            inode_to_port[lp.inode] = lp.port
        rec = listening_map.get(lp.port)
        if rec is None:
            listening_map[lp.port] = {
                "protocol": _proto_label([lp.protocol]),
                "protocols": [lp.protocol],
                "ip": lp.ip,
                "ips": [lp.ip] if lp.ip else [],
                "process": lp.process_name,
                "pid": lp.pid,
            }
            continue
        if lp.protocol and lp.protocol not in rec["protocols"]:
            rec["protocols"].append(lp.protocol)
            rec["protocol"] = _proto_label(rec["protocols"])
        if lp.ip and lp.ip not in rec["ips"]:
            rec["ips"].append(lp.ip)
        if lp.process_name and not rec["process"]:
            rec["process"] = lp.process_name
            rec["pid"] = lp.pid

    container_map: dict[int, list[dict]] = {}

    def _add_container(port: int, c, extra: dict | None = None) -> None:
        if port < 1 or port > 65535:
            return
        extra = extra or {}
        hip = (extra.get("host_ip") or "").strip() or None
        proto = extra.get("protocol") or None
        cport = extra.get("container_port")
        lst = container_map.setdefault(port, [])
        existing = next((x for x in lst if x["name"] == c.name), None)
        if existing:
            if hip and hip not in existing["bind_ips"]:
                existing["bind_ips"].append(hip)
            if proto and proto not in existing.get("protocols", []):
                existing.setdefault("protocols", []).append(proto)
                existing["protocol"] = _proto_label(existing["protocols"])
            if cport and not existing.get("container_port"):
                existing["container_port"] = cport
            if extra.get("source") != "expose":
                existing["expose_only"] = False
            return
        lst.append({
            "name": c.name,
            "status": c.status,
            "image": c.image,
            "compose_project": c.compose_project,
            "compose_service": c.compose_service,
            "network_mode": c.network_mode,
            "urls": list(c.urls or []),
            "vhost_urls": list(c.vhost_urls or []),
            "vhost_port": c.vhost_port,
            "vhost_fallback": _vhost_fallback(c),
            "label_port": c.label_port,
            "label_fallback": _label_url_fallback(c),
            "expose_only": extra.get("source") == "expose",
            "bind_ips": [hip] if hip else [],
            "protocols": [proto] if proto else [],
            "protocol": proto,
            "container_port": cport,
        })

    for c in containers:
        for p in c.ports:
            _add_container(p["host_port"], c, {
                "host_ip": p.get("host_ip"),
                "protocol": p.get("protocol"),
                "container_port": p.get("container_port"),
                "source": p.get("source") or "publish",
            })
        if c.network_mode == "host" and c.socket_inodes:
            for inode in c.socket_inodes:
                port = inode_to_port.get(inode)
                if port:
                    _add_container(port, c)
        if c.network_mode == "host" and c.pid:
            for port, rec in listening_map.items():
                if rec.get("pid") == c.pid:
                    _add_container(port, c)

    compose_map: dict[int, list[dict]] = {}
    compose_seen: set[tuple] = set()
    for cp in compose_ports:
        if cp.port < 1 or cp.port > 65535:
            continue
        key = (cp.project_dir, cp.port, cp.service_name, cp.host_ip or "", _proto_family(cp.protocol), cp.compose_file)
        if key in compose_seen:
            continue
        compose_seen.add(key)
        compose_map.setdefault(cp.port, []).append({
            "project_dir": cp.project_dir,
            "project_name": cp.project_name or cp.project_dir,
            "service_name": cp.service_name,
            "compose_file": cp.compose_file,
            "container_port": cp.container_port,
            "protocol": cp.protocol,
            "host_ip": cp.host_ip,
            "network_mode": cp.network_mode,
        })

    manual_map: dict[int, dict] = {}
    for mp in manual_ports:
        if not isinstance(mp, dict):
            continue
        try:
            port = int(mp["port"])
        except (KeyError, TypeError, ValueError):
            continue
        if port < 1 or port > 65535:
            continue
        manual_map[port] = {
            "label": mp.get("label", "") or "",
            "machine": mp.get("machine", "localhost") or "localhost",
        }

    all_ports = set(listening_map) | set(container_map) | set(compose_map) | set(manual_map)

    port_list: list[dict] = []
    for port in sorted(all_ports):
        if port in hidden_ports and not include_hidden:
            continue

        lp_info = listening_map.get(port)
        ctors = container_map.get(port, [])
        composes = compose_map.get(port, [])
        manual = manual_map.get(port)

        is_listening = port in listening_map
        has_live = any(
            c["status"] in ("running", "paused", "restarting") and not c.get("expose_only")
            for c in ctors
        )
        is_manual = manual is not None

        if is_listening or has_live:
            status = "used"
        elif composes or is_manual or ctors:
            status = "configured"
        else:
            status = "free"

        if ctors:
            source_type = "docker"
        elif is_listening and not ctors:
            known = get_known_port(port)
            if known and known.get("category") == "system":
                source_type = "system"
            else:
                source_type = "host"
        elif composes:
            source_type = "docker"
        elif is_manual:
            source_type = "manual"
        else:
            source_type = "unknown"

        known = get_known_port(port)
        ips = list((lp_info.get("ips") if lp_info else None) or [])
        if not ips:
            for c in ctors:
                for hip in c.get("bind_ips") or []:
                    hip = (hip or "").strip()
                    if hip and hip not in ips:
                        ips.append(hip)
        if not ips:
            for c in composes:
                hip = (c.get("host_ip") or "").strip()
                if hip and hip not in ips:
                    ips.append(hip)
        if not ips:
            ips = [lp_info["ip"] if lp_info else "0.0.0.0"]
        ip = (lp_info["ip"] if lp_info else None) or ips[0]
        urls = _collect_urls(port, ips, ctors, known, options)
        for c in ctors:
            c.pop("vhost_fallback", None)
            c.pop("label_fallback", None)
            c.pop("expose_only", None)
        proto_bits = []
        if lp_info:
            proto_bits.extend(lp_info.get("protocols") or [lp_info["protocol"]])
        else:
            for c in ctors:
                proto_bits.extend(c.get("protocols") or ([c.get("protocol")] if c.get("protocol") else []))
            proto_bits.extend(c.get("protocol") or "tcp" for c in composes)

        port_list.append({
            "port": port,
            "status": status,
            "source_type": source_type,
            "protocol": _proto_label(proto_bits) if proto_bits else "tcp",
            "ip": ip,
            "ips": ips,
            "bind_scope": _bind_scope_many(ips),
            "process": lp_info["process"] if lp_info else None,
            "pid": lp_info["pid"] if lp_info else None,
            "containers": ctors,
            "compose_configs": composes,
            "manual_label": manual["label"] if manual else None,
            "machine": manual["machine"] if manual else "localhost",
            "known_service": known,
            "is_hidden": port in hidden_ports,
            "conflict": _compose_conflict(composes),
            "urls": urls,
        })

    used = sum(
        1 for p in port_list
        if p["status"] == "used" and range_start <= p["port"] <= range_end
    )
    configured = sum(
        1 for p in port_list
        if p["status"] == "configured" and range_start <= p["port"] <= range_end
    )
    occupied = {p["port"] for p in port_list}
    occupied.update(hidden_ports)
    free = sum(1 for n in range(range_start, range_end + 1) if n not in occupied)

    return {
        "ports": port_list,
        "summary": {
            "used": used,
            "configured": configured,
            "free": free,
            "hidden": len(hidden_ports),
            "hidden_locked": hidden_locked,
            "range_start": range_start,
            "range_end": range_end,
        },
    }


def _proto_label(protocols: list[str]) -> str:
    bases: list[str] = []
    for proto in protocols:
        base = proto.replace("6", "")
        if base not in bases:
            bases.append(base)
    return ",".join(bases) if bases else "tcp"


def _strip_bind_ip(ip: str | None) -> str:
    text = (ip or "").strip()
    if text.startswith("[") and text.endswith("]") and ":" in text:
        text = text[1:-1]
    return text.split("%", 1)[0]


def _bind_key(ip: str | None) -> str:
    """Wildcard (`*`) or a canonical address. Empty Compose host_ip is 0.0.0.0."""
    text = _strip_bind_ip(ip)
    if not text or text in ("*", "0.0.0.0", "::", "::0"):
        return "*"
    if text == "localhost":
        return "127.0.0.1"
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return text
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.is_unspecified:
        return "*"
    return str(addr)


def _binds_overlap(a: str | None, b: str | None) -> bool:
    ka, kb = _bind_key(a), _bind_key(b)
    return ka == "*" or kb == "*" or ka == kb


def _proto_family(proto: str | None) -> str:
    base = (proto or "tcp").lower().replace("6", "")
    return "udp" if base.startswith("udp") else "tcp"


def _compose_conflict(composes: list[dict]) -> bool:
    """True when two Compose projects publish the same port+protocol on overlapping binds."""
    by_dir: dict[str, list[tuple[str | None, str]]] = {}
    for row in composes:
        by_dir.setdefault(row.get("project_dir") or "", []).append(
            (row.get("host_ip"), _proto_family(row.get("protocol"))),
        )
    dirs = list(by_dir.values())
    if len(dirs) < 2:
        return False
    for i, binds_a in enumerate(dirs):
        for binds_b in dirs[i + 1 :]:
            if any(
                pa == pb and _binds_overlap(a, b)
                for a, pa in binds_a
                for b, pb in binds_b
            ):
                return True
    return False


def _bind_scope(ip: str) -> str:
    text = _strip_bind_ip(ip)
    if not text or text in ("*",):
        return "public"
    if text == "localhost":
        return "localhost"
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return "lan"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.is_unspecified:
        return "public"
    if addr.is_loopback:
        return "localhost"
    if addr.is_link_local:
        return "link"
    try:
        if addr in ipaddress.ip_network("172.17.0.0/16"):
            return "link"
    except TypeError:
        pass
    if addr.is_global:
        return "public"
    return "lan"


def _bind_scope_many(ips: list[str]) -> str:
    scopes = {_bind_scope(ip) for ip in ips or ["0.0.0.0"]}
    if "public" in scopes:
        return "public"
    if "lan" in scopes:
        return "lan"
    if "link" in scopes:
        return "link"
    return "localhost"


_NO_HTTP_PORTS = frozenset({
    22, 23, 25, 53, 110, 143, 445, 554, 1194, 1935, 3260, 3389, 5060, 5061,
    5222, 5357, 5900, 8554, 9418, 10050, 10051, 4317, 51820,
})


def _host_for_url(host: str) -> str:
    """Bracket IPv6 literals so guessed links parse as URLs."""
    text = (host or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return host.strip() or "localhost"
    if addr.version == 6:
        return f"[{addr}]"
    return str(addr)


def _guess_url_host(ips: list[str], configured: str) -> str:
    """Localhost binds stay loopback; LAN-only binds use that address unless URL_HOST is set."""
    scope = _bind_scope_many(ips)
    if scope == "localhost":
        return "127.0.0.1"
    if configured:
        return configured
    if scope == "lan":
        for ip in ips or []:
            if _bind_scope(ip) == "lan":
                return ip
    return "localhost"


def _label_url_fallback(c) -> bool:
    lport = getattr(c, "label_port", None)
    cports = {p.get("container_port") for p in (c.ports or [])}
    cports.discard(None)
    if lport:
        return lport not in cports
    web = cports & {80, 443, 8080, 8443}
    if web:
        return False
    return True


def _vhost_fallback(c) -> bool:
    """True when VIRTUAL_PORT does not match any published container port."""
    vport = c.vhost_port
    if not vport or not c.vhost_urls:
        return True
    return vport not in {p.get("container_port") for p in c.ports or []}


def _collect_urls(
    port: int,
    ips: list[str],
    containers: list[dict],
    known: dict | None,
    options: dict | None = None,
) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for c in containers:
        label_urls = c.get("urls") or []
        if label_urls:
            cport = c.get("container_port")
            lport = c.get("label_port")
            attach = bool(c.get("label_fallback"))
            if lport is not None:
                attach = attach or cport == lport
            elif cport in (80, 443, 8080, 8443) or cport is None:
                attach = True
            if attach:
                for raw in label_urls:
                    u = safe_http_url(raw)
                    if u and u not in seen:
                        seen.add(u)
                        urls.append(u)
        vurls = c.get("vhost_urls") or []
        if not vurls:
            continue
        vport = c.get("vhost_port")
        cport = c.get("container_port")
        if c.get("vhost_fallback") or vport is None or cport == vport:
            for raw in vurls:
                u = safe_http_url(raw)
                if u and u not in seen:
                    seen.add(u)
                    urls.append(u)
    opts = options or {}
    if opts.get("guess_urls") is False:
        return urls
    if known and known.get("is_access_port"):
        configured = (opts.get("url_host") or "").strip()
        host = _guess_url_host(ips, configured)
        name = (known.get("name") or "").upper()
        scheme_pref = opts.get("url_scheme") or "auto"
        if scheme_pref in ("http", "https"):
            scheme = scheme_pref
        else:
            scheme = "https" if port in (443, 8443, 9443) or "HTTPS" in name else "http"
        if port in _NO_HTTP_PORTS:
            return urls
        host = _host_for_url(host)
        guess = f"{scheme}://{host}:{port}"
        if guess not in seen:
            urls.append(guess)
    return urls

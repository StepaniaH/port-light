"""Command-line adapter for the Port-Light client interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, TextIO

from . import __version__
from .client import PortLightClient, PortLightError
from .state import ReservationStore

SCHEMA_VERSION = 1
DEFAULT_URL = "http://127.0.0.1:2100"
DEFAULT_TTL = 3600


class _ArgumentParser(argparse.ArgumentParser):
    """Turn usage mistakes into data the CLI adapter can format consistently."""

    def __init__(self, *args: Any, **kwargs: Any):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        raise PortLightError("invalid_arguments", message)


def _bounded_int(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return number

    return parse


def parse_duration(value: str) -> int:
    text = value.strip().lower()
    factors = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    suffix = text[-1:] if text[-1:] in factors else ""
    number_text = text[:-1] if suffix else text
    try:
        number = float(number_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use seconds or a duration such as 10m, 1h, or 7d") from exc
    seconds = number * factors.get(suffix, 1)
    if not seconds.is_integer() or not 60 <= seconds <= 604800:
        raise argparse.ArgumentTypeError("must be between 60 seconds and 7 days")
    return int(seconds)


def build_parser() -> argparse.ArgumentParser:
    common = _ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--url", help="Port-Light base URL (default: PORT_LIGHT_URL or localhost)")
    common.add_argument("--timeout", type=float, help="request timeout in seconds (default: 5)")
    common.add_argument("--ca-file", help="custom CA bundle for HTTPS verification")
    common.add_argument("--json", action="store_true", help="emit one machine-readable JSON object")

    parser = _ArgumentParser(
        prog="port-light",
        description="Check and reserve ports through a running Port-Light instance.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "doctor",
        help="check whether Port-Light can provide trustworthy results",
        parents=[common],
    )

    check = commands.add_parser(
        "check",
        help="check whether one port is free on the selected instance",
        parents=[common],
    )
    check.add_argument("port", type=_bounded_int(1, 65535))

    reserve = commands.add_parser(
        "reserve",
        help="atomically select and reserve free ports",
        parents=[common],
    )
    reserve.add_argument("--count", type=_bounded_int(1, 64), default=1)
    reserve.add_argument("--start", type=_bounded_int(1, 65535))
    reserve.add_argument("--end", type=_bounded_int(1, 65535))
    reserve.add_argument("--label", default="", help="non-secret label shown in Port-Light")
    reserve.add_argument("--scope", choices=("self", "all"))
    expiry = reserve.add_mutually_exclusive_group()
    expiry.add_argument("--ttl", type=parse_duration, help="lease duration, such as 10m, 1h, or 7d")
    expiry.add_argument("--no-expiry", action="store_true", help="create a persistent reservation")
    reserve.add_argument(
        "--no-save",
        action="store_true",
        help="do not save tokens locally; requires --json",
    )

    release = commands.add_parser(
        "release",
        help="release a reservation created by this client",
        parents=[common],
    )
    release.add_argument("port", type=_bounded_int(1, 65535))
    release.add_argument(
        "--token-stdin",
        action="store_true",
        help="read the reservation token from standard input",
    )
    return parser


def _client_from_environment(args: argparse.Namespace, environ: Mapping[str, str]) -> PortLightClient:
    timeout_value: float | str = getattr(args, "timeout", environ.get("PORT_LIGHT_TIMEOUT", "5"))
    try:
        timeout = float(timeout_value)
    except ValueError as exc:
        raise PortLightError(
            "invalid_timeout",
            "PORT_LIGHT_TIMEOUT must be a number greater than zero",
        ) from exc
    return PortLightClient(
        getattr(args, "url", environ.get("PORT_LIGHT_URL", DEFAULT_URL)),
        basic_auth=environ.get("PORT_LIGHT_AUTH", ""),
        agent_token=environ.get("PORT_LIGHT_AGENT_TOKEN", "") or environ.get("AGENT_TOKEN", ""),
        timeout=timeout,
        ca_file=getattr(args, "ca_file", environ.get("PORT_LIGHT_CA_FILE") or None),
    )


def _json_write(stream: TextIO, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _expiry_text(value: Any) -> str:
    if value is None:
        return "no expiry"
    if isinstance(value, (int, float)):
        try:
            return "expires at " + datetime.fromtimestamp(value, timezone.utc).isoformat().replace(
                "+00:00", "Z",
            )
        except (OverflowError, OSError, ValueError):
            pass
    return f"expires at {value}"


def _error_exit(error: PortLightError) -> int:
    if error.code in ("not_found", "conflict", "no_capacity"):
        return 1
    if error.code.startswith("invalid_") or error.code in (
        "reservation_token_missing",
        "state_read_failed",
        "state_write_failed",
    ):
        return 2
    return 3


def _error_document(command: str | None, error: PortLightError) -> dict[str, Any]:
    detail: dict[str, Any] = {"code": error.code, "message": str(error)}
    if error.status is not None:
        detail["status"] = error.status
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "command": command,
        "error": detail,
    }


def _doctor(
    client: PortLightClient,
    *,
    json_output: bool,
    stdout: TextIO,
) -> int:
    document = client.doctor()
    healthy = document.get("overall") == "healthy"
    if json_output:
        _json_write(stdout, {
            "schema_version": SCHEMA_VERSION,
            "ok": healthy,
            "command": "doctor",
            "doctor": document,
        })
    else:
        context = document.get("context") or {}
        version = context.get("version") or "unknown version"
        stdout.write(f"Port-Light {version}: {document.get('overall', 'unknown')}\n")
        counts = document.get("counts") or {}
        stdout.write(
            f"Checks: {counts.get('pass', 0)} passed, "
            f"{counts.get('warning', 0)} warnings, {counts.get('fail', 0)} failed\n"
        )
        for check in document.get("checks") or []:
            if isinstance(check, dict) and check.get("status") in ("warning", "fail"):
                stdout.write(
                    f"{str(check['status']).upper():7} "
                    f"{check.get('id', 'unknown')}: {check.get('detail', 'unknown')}\n"
                )
    return 0 if healthy else 1


def _check(
    client: PortLightClient,
    port: int,
    *,
    json_output: bool,
    stdout: TextIO,
) -> int:
    row = client.check_port(port)
    free = row.get("status") == "free"
    if json_output:
        _json_write(stdout, {
            "schema_version": SCHEMA_VERSION,
            "ok": free,
            "command": "check",
            "port": row,
        })
    else:
        status = str(row.get("status") or "unknown")
        names = ", ".join(str(name) for name in row.get("names") or [])
        suffix = f" ({names})" if names else ""
        stdout.write(f"Port {port} is {status}{suffix}.\n")
    return 0 if free else 1


def _peer_warning(client: PortLightClient, scope_was_default: bool) -> str | None:
    if not scope_was_default:
        return None
    try:
        meta = client.meta()
    except PortLightError:
        return None
    automation = meta.get("automation") or {}
    if isinstance(automation, dict) and automation.get("suggest_peers"):
        return "Peers are configured; use --scope all to check them before reserving."
    return None


def _reserve(
    client: PortLightClient,
    args: argparse.Namespace,
    store: ReservationStore,
    environ: Mapping[str, str],
    *,
    json_output: bool,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if args.no_save and not json_output:
        raise PortLightError("invalid_request", "--no-save requires --json so tokens are not lost")
    if args.start is not None and args.end is not None and args.end < args.start:
        raise PortLightError("invalid_request", "--end must be greater than or equal to --start")
    scope_env = environ.get("PORT_LIGHT_SCOPE", "").strip()
    if scope_env and scope_env not in ("self", "all"):
        raise PortLightError("invalid_request", "PORT_LIGHT_SCOPE must be self or all")
    scope = args.scope or scope_env or "self"
    scope_was_default = args.scope is None and not scope_env
    ttl = None if args.no_expiry else (args.ttl if args.ttl is not None else DEFAULT_TTL)
    if not args.no_save:
        store.ensure_writable()
    result = client.reserve_ports(
        count=args.count,
        start=args.start,
        end=args.end,
        label=args.label,
        ttl=ttl,
        scope=scope,
    )
    reservations = result["reservations"]
    if not args.no_save:
        try:
            for reservation in reservations:
                store.save(client.base_url, reservation)
        except PortLightError as exc:
            recovery = _error_document("reserve", exc)
            recovery["reservations"] = reservations
            recovery["recovery_required"] = True
            if not json_output:
                stderr.write(
                    "Reservation succeeded, but token storage failed; save the recovery JSON.\n"
                )
            _json_write(stdout, recovery)
            return 3
    warnings = []
    warning = _peer_warning(client, scope_was_default)
    if warning:
        warnings.append(warning)
    complete = len(reservations) == args.count
    if not complete:
        warnings.append(
            f"Only {len(reservations)} of {args.count} requested ports were available and reserved."
        )
    if json_output:
        _json_write(stdout, {
            "schema_version": SCHEMA_VERSION,
            "ok": complete,
            "command": "reserve",
            "ports": result.get("ports", []),
            "reservations": reservations,
            "scope": result.get("scope", scope),
            "range": result.get("range"),
            "warnings": warnings,
            "tokens_saved": not args.no_save,
        })
    else:
        noun = "port" if len(reservations) == 1 else "ports"
        stdout.write(
            f"Reserved {len(reservations)} {noun} with scope {result.get('scope', scope)}.\n"
        )
        for reservation in reservations:
            stdout.write(
                f"{reservation['port']}  {_expiry_text(reservation.get('expires_at'))}\n"
            )
        if not args.no_save:
            stdout.write(f"Reservation tokens saved under {store.root}.\n")
        for message in warnings:
            stderr.write("Warning: " + message + "\n")
    return 0 if complete else 1


def _release(
    client: PortLightClient,
    port: int,
    token_stdin: bool,
    store: ReservationStore,
    environ: Mapping[str, str],
    stdin: TextIO,
    *,
    json_output: bool,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if token_stdin:
        token = stdin.read().strip()
    else:
        token = environ.get("PORT_LIGHT_RESERVATION_TOKEN", "").strip()
        if not token:
            token = store.load(client.base_url, port) or ""
    if not token:
        raise PortLightError(
            "reservation_token_missing",
            "no saved token; use PORT_LIGHT_RESERVATION_TOKEN or --token-stdin",
        )
    result = client.release_port(port, token)
    try:
        store.delete(client.base_url, port)
    except PortLightError as exc:
        if json_output:
            document = _error_document("release", exc)
            document.update(result)
            document["remote_released"] = True
            _json_write(stdout, document)
        else:
            stdout.write(f"Released reservation for port {port}.\n")
            stderr.write(
                "Warning: the saved local token could not be removed; "
                "the remote reservation is already released.\n"
            )
        return 3
    if json_output:
        _json_write(stdout, {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "command": "release",
            **result,
        })
    else:
        stdout.write(f"Released reservation for port {port}.\n")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    client: PortLightClient | None = None,
    store: ReservationStore | None = None,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    environ = environ if environ is not None else os.environ
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    try:
        args = parser.parse_args(raw_argv)
    except PortLightError as exc:
        if "--json" in raw_argv:
            command = next(
                (value for value in raw_argv if value in ("doctor", "check", "reserve", "release")),
                None,
            )
            _json_write(stdout, _error_document(command, exc))
        else:
            parser.print_usage(stderr)
            stderr.write(f"port-light: error: {exc}\n")
        return 2
    json_output = bool(getattr(args, "json", False))
    try:
        active_client = client or _client_from_environment(args, environ)
        active_store = store or ReservationStore()
        if args.command == "doctor":
            return _doctor(active_client, json_output=json_output, stdout=stdout)
        if args.command == "check":
            return _check(active_client, args.port, json_output=json_output, stdout=stdout)
        if args.command == "reserve":
            return _reserve(
                active_client,
                args,
                active_store,
                environ,
                json_output=json_output,
                stdout=stdout,
                stderr=stderr,
            )
        if args.command == "release":
            return _release(
                active_client,
                args.port,
                args.token_stdin,
                active_store,
                environ,
                stdin,
                json_output=json_output,
                stdout=stdout,
                stderr=stderr,
            )
        raise PortLightError("invalid_request", f"unknown command: {args.command}")
    except PortLightError as exc:
        if json_output:
            _json_write(stdout, _error_document(args.command, exc))
        else:
            stderr.write(f"port-light: {exc}\n")
        return _error_exit(exc)
    except Exception:  # noqa: BLE001 — keep terminal/JSON output stable without leaking details
        error = PortLightError(
            "internal_error",
            "the command failed unexpectedly; retry with a current Port-Light CLI",
        )
        if json_output:
            _json_write(stdout, _error_document(args.command, error))
        else:
            stderr.write(f"port-light: {error}\n")
        return 3

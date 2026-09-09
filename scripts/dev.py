#!/usr/bin/env python3
"""Run development, disposable demo previews, or the local test suite."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def demo_data(root: Path) -> Path:
    compose = root / 'compose'
    for name, ports in (
        ('media', ['8080:80', '20000-20255:20000-20255/udp']),
        ('wiki', ['127.0.0.1:8080:80', '9000:9000']),
    ):
        directory = compose / name
        directory.mkdir(parents=True)
        (directory / 'compose.yaml').write_text(
            'name: demo-' + name + '\nservices:\n  web:\n    image: example.invalid/demo\n    ports:\n' +
            ''.join('      - ' + json.dumps(port) + '\n' for port in ports))
    (root / 'port_light.json').write_text(json.dumps({
        'manual_ports': [{'port': 9090, 'label': 'Demo manual entry', 'machine': 'localhost'}],
        'hidden_ports': [], 'peers': [],
        'port_rules': [{'name': 'development', 'start': 20000, 'end': 29999, 'projects': ['demo-media']}],
        'settings': {'port_range_start': 1, 'port_range_end': 30000, 'locale': 'en', 'copy_on_click': False},
    }))
    return compose


def serve(args, data: Path, compose: Path, *, demo: bool = False) -> int:
    data.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if demo:
        for key in list(env):
            if key.startswith(('PORT_LIGHT_', 'AUTH_', 'AGENT_', 'HIDDEN_', 'COMPOSE_', 'WEBHOOK_', 'HISTORY_', 'PORT_RANGE_')):
                env.pop(key)
    env.update(PORT_LIGHT_DATA_DIR=str(data.resolve()), COMPOSE_SCAN_DIR=str(compose.resolve()),
               PORT_LIGHT_PORT=str(args.port))
    if demo:
        env.update(PORT_LIGHT_SCANNERS='compose', PORT_LIGHT_SETTINGS_SOURCE='file', HISTORY_RETENTION_DAYS='0')
    command = [sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', str(args.port)]
    if getattr(args, 'reload', False):
        command.append('--reload')
    print(f'http://127.0.0.1:{args.port}/' + (' (demo data; removed on exit)' if demo else ''), flush=True)
    child = subprocess.Popen(command, cwd=ROOT, env=env)
    def stop(_signal, _frame):
        if child.poll() is None:
            child.terminate()
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        return child.wait()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    dev = commands.add_parser('serve', help='run against local data and Compose files')
    dev.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    dev.add_argument('--compose-dir', type=Path, required=True)
    dev.add_argument('--reload', action='store_true')
    dev.add_argument('--port', type=int, default=2100)
    preview = commands.add_parser('preview', help='run with temporary example data; Ctrl-C cleans up')
    preview.add_argument('--port', type=int, default=2100)
    check = commands.add_parser('test', help='run Python and frontend checks')
    check.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    if args.command == 'test':
        steps = [[sys.executable, '-m', 'ruff', 'check', 'backend', 'tests', 'mcp', 'port_light_client', 'scripts/dev.py', 'scripts/check_release_ci.py', 'scripts/dockerhub_description.py'],
                 [sys.executable, '-m', 'pytest', '-q'], ['npm', 'run', 'lint'], ['npm', 'test']]
        if args.browser:
            steps += [['npm', 'run', 'smoke:browser'], ['npm', 'run', 'smoke:management']]
        for command in steps:
            result = subprocess.run(command, cwd=ROOT)
            if result.returncode:
                return result.returncode
        return 0
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    if args.command == 'serve':
        return serve(args, args.data_dir, args.compose_dir)
    with tempfile.TemporaryDirectory(prefix='port-light-preview-') as directory:
        data = Path(directory)
        return serve(args, data, demo_data(data), demo=True)


if __name__ == '__main__':
    raise SystemExit(main())

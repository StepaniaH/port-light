#!/usr/bin/env python3
"""Render the release README for Docker Hub; optionally update the repository."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'stepaniah/port-light'
GITHUB = 'https://github.com/StepaniaH/port-light'


def render(markdown: str, ref: str = 'main') -> str:
    """Resolve relative Markdown links and HTML images for the registry page."""
    def url(path: str, image: bool = False) -> str:
        if path.startswith(('https://', 'http://', '#', 'mailto:')):
            return path
        if image:
            return f'https://raw.githubusercontent.com/StepaniaH/port-light/{ref}/' + path
        return f'{GITHUB}/blob/{ref}/' + path
    result = re.sub(r'(!?\[[^\]\n]*\])\(([^\s)]+)\)',
                    lambda m: m[1] + '(' + url(m[2], m[1].startswith('!')) + ')', markdown)
    return re.sub(r'(<img\b[^>]*\bsrc=")([^"]+)(")',
                  lambda m: m[1] + url(m[2], True) + m[3], result)


def request(method: str, path: str, body: dict, token: str = '') -> dict:
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request('https://hub.docker.com' + path, method=method,
                                 data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='main', help='Git ref for documentation and screenshot URLs')
    parser.add_argument('--output', type=Path, help='write the rendered Markdown to a file')
    parser.add_argument('--publish', action='store_true', help='update Docker Hub with DOCKERHUB_USERNAME and DOCKERHUB_TOKEN')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9._-]+', args.ref):
        parser.error('ref must be a tag, commit SHA or branch name without slashes')
    description = render((ROOT / 'README.md').read_text(), args.ref)
    if args.output:
        args.output.write_text(description)
    if not args.publish:
        if not args.output:
            print(description)
        return 0
    username = os.environ.get('DOCKERHUB_USERNAME', '')
    secret = os.environ.get('DOCKERHUB_TOKEN', '')
    if not username or not secret:
        parser.error('DOCKERHUB_USERNAME and DOCKERHUB_TOKEN are required for publication')
    try:
        token = request('POST', '/v2/auth/token', {'identifier': username, 'secret': secret})['access_token']
        request('PATCH', '/v2/repositories/' + REPOSITORY, {
            'description': 'Host port occupancy from listeners, Docker mappings, and Compose declarations.',
            'full_description': description,
        }, token)
    except urllib.error.HTTPError as exc:
        print(f'Docker Hub description update failed (HTTP {exc.code}).', file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError):
        print('Docker Hub description update failed; check connectivity and credentials.', file=sys.stderr)
        return 1
    print('Docker Hub description updated.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

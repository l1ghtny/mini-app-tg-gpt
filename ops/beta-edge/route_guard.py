#!/usr/bin/env python3
"""Actively select healthy Moscow-edge beta AWG origins without replaying API requests."""
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

LIVE = Path('/etc/nginx/sites-available/lightnyai-beta')
STATE = Path('/var/lib/lightnyai-monitor/beta-route-guard.json')
LOCK = Path('/run/lock/lightnyai-nginx.lock')
BACKUPS = Path('/var/backups/lightnyai-edge/beta-route-guard')
PEERS = {'node2': '10.78.0.1', 'new': '10.78.0.5', 'main': '10.78.0.9'}
# name: (TLS host, port, path, accepted status, private CA, verify backend JSON)
ROUTES = {
    'frontend': ('lightnyai-beta.internal', 30445, '/health.json', 200, '/etc/nginx/trust/lightnyai-landing-ca.pem', False),
    'backend': ('lightnyai-beta.internal', 30445, '/health/ready', 200, '/etc/nginx/trust/lightnyai-landing-ca.pem', True),
}
UPSTREAM = re.compile(r'(?ms)^(upstream lightny_beta_(frontend|backend) \{\n)(.*?)(^\}\n)')
SERVER = re.compile(r'^\s*server\s+(10\.78\.0\.[159]):(30445|8443)(?:\s+[^;]*)?;\s*$')


def probe(route, peer):
    host, port, path, expected, ca, check_json = ROUTES[route]
    ip = PEERS[peer]
    args = ['curl', '--noproxy', '*', '-sS', '--connect-timeout', '2', '--max-time', '4',
            '--resolve', f'{host}:{port}:{ip}', '-w', '\n%{http_code}']
    if ca:
        args += ['--cacert', ca]
    args += [f'https://{host}:{port}{path}']
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return False
    body, separator, status = result.stdout.rpartition('\n')
    if result.returncode or not separator or status != str(expected):
        return False
    if route == 'frontend':
        try:
            return json.loads(body).get('status') == 'ok'
        except (ValueError, AttributeError):
            return False
    if check_json:
        try:
            value = json.loads(body)
            return (value.get('status') == 'ready'
                    and value.get('checks', {}).get('database') == 'ok'
                    and value.get('checks', {}).get('redis') == 'ok')
        except (ValueError, AttributeError):
            return False
    return True


def parse_upstreams(config):
    matches = list(UPSTREAM.finditer(config))
    if len(matches) != len(ROUTES) or {m.group(2) for m in matches} != set(ROUTES):
        raise ValueError('Expected two known beta upstreams')
    if 'server_name beta.app.lightnyai.ru;' not in config or 'proxy_next_upstream off;' not in config:
        raise ValueError('Not the expected beta config; leaving routing unchanged')
    current = {}
    for match in matches:
        route = match.group(2)
        port = ROUTES[route][1]
        servers = []
        for line in match.group(3).splitlines():
            found = SERVER.fullmatch(line)
            if not found or int(found.group(2)) != port:
                raise ValueError(f'Unexpected {route} upstream line: {line!r}')
            servers.append(found.group(1))
        if not servers or len(servers) > len(PEERS) or len(set(servers)) != len(servers):
            raise ValueError(f'Unexpected {route} upstream members: {servers!r}')
        current[route] = servers
    return current


def selected_members(current, healthy, streak):
    desired = {}
    for route, members in current.items():
        ready = {ip for ip, good in healthy[route].items() if good}
        # Keep the selected peer while healthy; promote an already selected backup
        # immediately after a failure. New peers require three successful probes.
        primary = next((ip for ip in members if ip in ready), None)
        if primary is None:
            primary = next((ip for ip in PEERS.values()
                            if ip in ready and streak[route][ip] >= 3), None)
        if primary is None:
            # No verified route is available. Retain the last known config.
            desired[route] = members
            continue
        standby_order = dict.fromkeys([*members, *PEERS.values()])
        desired[route] = [primary] + [ip for ip in standby_order
                                      if ip != primary and ip in ready
                                      and streak[route][ip] >= 3]
    return desired


def render(config, desired):
    def replace(match):
        route = match.group(2)
        port = ROUTES[route][1]
        members = desired[route]
        lines = [f'    server {members[0]}:{port};\n']
        for member in members[1:]:
            lines.append(f'    server {member}:{port} backup;\n')
        return match.group(1) + ''.join(lines) + match.group(4)
    updated = UPSTREAM.sub(replace, config)
    if parse_upstreams(updated) != desired:
        raise ValueError('Rendered routing does not match selected peers')
    return updated


def save_state(streak, healthy, selected, changed):
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    value = {'checked_at': datetime.now(timezone.utc).isoformat(),
             'healthy': healthy, 'success_streak': streak,
             'selected': selected, 'changed': changed}
    fd, tmp_name = tempfile.mkstemp(prefix='.route-guard-', dir=STATE.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, STATE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def apply_config(original, updated):
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = BACKUPS / ('lightnyai-beta-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    shutil.copy2(LIVE, backup)
    fd, tmp_name = tempfile.mkstemp(prefix='.lightnyai-route-', dir=LIVE.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(updated)
        shutil.copystat(LIVE, tmp_name)
        os.replace(tmp_name, LIVE)
        try:
            subprocess.run(['nginx', '-t'], check=True, capture_output=True, timeout=10)
            subprocess.run(['systemctl', 'reload', 'nginx'], check=True, capture_output=True, timeout=15)
        except Exception:
            shutil.copy2(backup, LIVE)
            subprocess.run(['nginx', '-t'], check=True, timeout=10)
            subprocess.run(['systemctl', 'reload', 'nginx'], check=True, timeout=15)
            raise
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return str(backup)


def main(dry_run=False):
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        tasks = {(route, peer): pool.submit(probe, route, peer)
                 for route in ROUTES for peer in PEERS}
        outcomes = {(route, peer): task.result() for (route, peer), task in tasks.items()}
    healthy = {route: {PEERS[peer]: outcomes[(route, peer)] for peer in PEERS} for route in ROUTES}
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('lightnyai route guard skipped: Nginx config lock held')
            return 0
        current_text = LIVE.read_text()
        if 'server_name beta.app.lightnyai.ru;' not in current_text:
            print('lightnyai route guard skipped: public beta config is in maintenance mode')
            return 0
        current = parse_upstreams(current_text)
        try:
            previous = json.loads(STATE.read_text()).get('success_streak', {})
        except (FileNotFoundError, ValueError, AttributeError):
            previous = {}
        streak = {route: {ip: min(3, previous.get(route, {}).get(ip, 0) + 1) if good else 0
                          for ip, good in results.items()} for route, results in healthy.items()}
        desired = selected_members(current, healthy, streak)
        updated = render(current_text, desired)
        changed = updated != current_text
        if dry_run:
            print(json.dumps({'healthy': healthy, 'current': current, 'selected': desired,
                              'would_change': changed}, indent=2))
            return 0
        backup = apply_config(current_text, updated) if changed else None
        save_state(streak, healthy, desired, changed)
        print(json.dumps({'healthy': healthy, 'selected': desired, 'changed': changed,
                          'backup': backup}, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main('--dry-run' in sys.argv[1:]))
    except Exception as error:
        print(f'lightnyai route guard failed: {error}', file=sys.stderr)
        raise SystemExit(1)

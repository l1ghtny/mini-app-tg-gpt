"""Install beta's isolated Nginx site/guard through the owner's pinned SSH host."""
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
phase = sys.argv[1]
if phase not in {'bootstrap', 'activate'}:
    raise SystemExit('Use bootstrap or activate')
payload = {'phase': phase, 'config': (HERE / 'nginx.conf').read_text(),
           'guard': (HERE / 'route_guard.py').read_text(),
           'service': (HERE / 'lightnyai-beta-route-guard.service').read_text(),
           'timer': (HERE / 'lightnyai-beta-route-guard.timer').read_text()}
REMOTE = r'''
import fcntl,json,os,shutil,subprocess,tempfile
from pathlib import Path
from datetime import datetime,timezone
payload = json.loads(PAYLOAD)
live = Path('/etc/nginx/sites-available/lightnyai-beta')
enabled = Path('/etc/nginx/sites-enabled/lightnyai-z-beta')
lock = Path('/run/lock/lightnyai-nginx.lock')
def atomic(path, data, mode=0o644):
    fd,tmp = tempfile.mkstemp(prefix='.beta-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream: stream.write(data)
        os.chmod(tmp,mode)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
config = payload['config']
if payload['phase'] == 'bootstrap':
    first = config.index('server {')
    second = config.index('server {',first + 8)
    config = config[first:second]
else:
    assert Path('/etc/letsencrypt/live/beta.app.lightnyai.ru/fullchain.pem').exists()
with lock.open('a+') as stream:
    fcntl.flock(stream,fcntl.LOCK_EX)
    backup_dir = Path('/var/backups/lightnyai-edge/beta-install') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup_dir.mkdir(parents=True,mode=0o700)
    original = live.read_text() if live.exists() else None
    if original is not None: shutil.copy2(live,backup_dir/'nginx.conf')
    had_link = enabled.exists()
    atomic(live,config)
    if not had_link: enabled.symlink_to(live)
    try:
        subprocess.run(['nginx','-t'],check=True,capture_output=True,timeout=10)
        subprocess.run(['systemctl','reload','nginx'],check=True,capture_output=True,timeout=15)
    except Exception as error:
        if getattr(error,'stderr',None): print(error.stderr.decode() if isinstance(error.stderr,bytes) else error.stderr)
        if original is None: live.unlink(missing_ok=True)
        else: atomic(live,original)
        if not had_link: enabled.unlink(missing_ok=True)
        subprocess.run(['nginx','-t'],check=True,timeout=10)
        subprocess.run(['systemctl','reload','nginx'],check=True,timeout=15)
        raise
    if payload['phase'] == 'activate':
        files = {
          '/opt/lightnyai/bin/beta-route-guard': (payload['guard'],0o755),
          '/etc/systemd/system/lightnyai-beta-route-guard.service': (payload['service'],0o644),
          '/etc/systemd/system/lightnyai-beta-route-guard.timer': (payload['timer'],0o644),
        }
        for name,(data,mode) in files.items():
            path = Path(name)
            if path.exists(): shutil.copy2(path,backup_dir/path.name)
            atomic(path,data,mode)
        subprocess.run(['systemctl','daemon-reload'],check=True)
        subprocess.run(['systemctl','enable','--now','lightnyai-beta-route-guard.timer'],check=True)
    print(json.dumps({'phase':payload['phase'],'backup':str(backup_dir),'nginx':'validated and reloaded'}))
'''
script = REMOTE.replace('PAYLOAD', repr(json.dumps(payload)))
subprocess.run(['ssh', '-F', '/Users/lightny/.ssh/lightnyai_edge.conf', 'lightnyai-edge', 'sudo', 'python3', '-'], input=script, text=True, check=True)

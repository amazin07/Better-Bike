#!/usr/bin/env python3
"""Forward test Stripe webhooks locally; keep API/signing secrets out of output."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from traffic import load_env_file

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', type=int, default=5001)
args = parser.parse_args()
load_env_file(ROOT / '.env')
key = os.getenv('STRIPE_SECRET_KEY', '')
if not key.startswith(('sk_test_', 'rk_test_')):
    sys.exit('Set a test STRIPE_SECRET_KEY in .env first.')
env = {**os.environ, 'STRIPE_API_KEY': key}
commands = [
    ('STRIPE_WEBHOOK_SECRET', ['--events', 'checkout.session.completed,checkout.session.expired,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed', '--events-from', '@self', '--forward-to', f'http://127.0.0.1:{args.port}/api/stripe/webhook']),
    ('STRIPE_CONNECT_WEBHOOK_SECRET', ['--events', 'v2.core.account[requirements].updated,v2.core.account[configuration.recipient].capability_status_updated', '--forward-to', f'http://127.0.0.1:{args.port}/api/stripe/connect-webhook']),
]
# Fetch secrets before starting Flask. Each event destination may have its own secret.
updates = {}
for name, flags in commands:
    result = subprocess.run(['stripe', 'listen', '--skip-update', '--print-secret', *flags], env=env, capture_output=True, text=True, timeout=40)
    match = re.search(r'whsec_[A-Za-z0-9]+', result.stdout + result.stderr)
    if result.returncode or not match:
        sys.exit('Stripe could not configure the local listener. Check your test key and CLI installation.')
    updates[name] = match[0]
p = ROOT / '.env'
lines = [line for line in p.read_text().splitlines() if line.split('=', 1)[0] not in updates]
p.write_text('\n'.join(lines + [f'{k}={v}' for k, v in updates.items()]) + '\n')
p.chmod(0o600)
print(f'Signing secrets saved to ignored .env. Start/restart Flask on port {args.port}.', flush=True)
processes = []
def watch(process):
    for line in process.stdout:
        line = re.sub(r'(?:whsec|sk_test|rk_test)_[A-Za-z0-9]+', '[secret hidden]', line)
        print(line.rstrip(), flush=True)
try:
    for name, flags in commands:
        process = subprocess.Popen(['stripe', 'listen', '--skip-update', '--latest', *flags], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        processes.append(process)
        threading.Thread(target=watch, args=(process,), daemon=True).start()
    while all(p.poll() is None for p in processes):
        threading.Event().wait(1)
finally:
    for p in processes:
        p.terminate()

#!/usr/bin/env python3
# Emite um token de usuário (30 dias) no ambiente grav usando as credenciais
# admin do PocketBase que já vivem na máquina. Não imprime segredo algum:
# grava /tmp/leia_tk.json (600) no remoto para ser copiado depois.
import json, os, re, sys, time, urllib.request, urllib.error, urllib.parse

ENV_PATH = '/home/ubuntu/leia_backend/.env'
BASE = 'http://localhost:8090'
EMAIL = sys.argv[1]
TTL = 60 * 60 * 24 * 30


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {'content-type': 'application/json'}
    if headers:
        h.update(headers)
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode() or '{}')


env = {}
for line in open(ENV_PATH, errors='replace'):
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    k, v = line.split('=', 1)
    env[k.strip()] = v.strip().strip('"').strip("'")

looks_email = lambda v: bool(re.match(r'^[^@\s]+@[^@\s]+\.[a-z]{2,}$', v, re.I))
emails = [k for k, v in env.items() if looks_email(v)]
secrets = [k for k, v in env.items() if not looks_email(v) and len(v) >= 8]

auth = None
for ek in emails:
    for pk in secrets:
        try:
            auth = call('POST', '/api/collections/_superusers/auth-with-password',
                        {'identity': env[ek], 'password': env[pk]}).get('token')
        except Exception:
            auth = None
        if auth:
            break
    if auth:
        break
if not auth:
    print('ERRO: superusuário do PocketBase não autenticou')
    sys.exit(2)

q = urllib.parse.urlencode({'filter': f"email='{EMAIL}'", 'perPage': 2})
res = call('GET', f'/api/collections/users/records?{q}', headers={'Authorization': auth})
items = res.get('items') or []
if not items:
    print('ERRO: usuário não encontrado nesse ambiente')
    sys.exit(3)
uid = items[0]['id']

imp = call('POST', f'/api/collections/users/impersonate/{uid}', {'duration': TTL},
           headers={'Authorization': auth})
token = imp.get('token')
if not token:
    print('ERRO: impersonate sem token')
    sys.exit(4)

out = {
    'token': token,
    'userId': uid,
    'baseUrl': 'https://grav-lightsail.grav.digital/leia_prod',
    'apiKey': 'rwf6HU',
    'expiresAtMs': int((time.time() + TTL) * 1000),
}
p = '/tmp/leia_tk.json'
with open(p, 'w') as f:
    json.dump(out, f)
os.chmod(p, 0o600)
print('OK: token emitido (%d bytes)' % len(json.dumps(out)))

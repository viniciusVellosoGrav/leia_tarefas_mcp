#!/usr/bin/env bash
# Reemite o token do quadro "Tarefas Leia" (30 dias) quando o antigo venceu.
#
# Emite direto na máquina do LEIA, via ssh, dentro do container do backend
# (leia-app), que tem o segredo de sessão e o acesso ao Mongo. Nada de segredo é impresso: o token vai do remoto direto para
# o arquivo de credencial (600) — o MESMO dos dois modos (plugin do Hermes e
# servidor MCP). Use quando o cliente reclamar de token vencido (máquina parada
# por mais de 30 dias).
#
# O watchdog (cron do Hermes) chama isto; a trava abaixo evita que duas execuções
# sobrepostas emitam dois tokens.
set -euo pipefail

HOST=ubuntu@54.91.22.152
KEY="$HOME/projects/grav/leia_backend/scripts/ssh.pem"
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="$HOME/.config/leia-tarefas-mcp"
DEST="${LEIA_TAREFAS_CRED:-$DIR/credentials.json}"
MINT="$HERE/mint_remoto.mjs"
CTR=leia-app
EMAIL=$(python3 -c "import json;print(json.load(open('$HOME/.claude.json'))['oauthAccount']['emailAddress'])")

LOCK="${TMPDIR:-/tmp}/leia_tarefas_remint.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "outra emissão de token já está em andamento ($LOCK); saindo sem mexer em nada."
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

[ -r "$KEY" ] || { echo "ERRO: chave ssh não encontrada em $KEY"; exit 2; }
[ -r "$MINT" ] || { echo "ERRO: mint_remoto.mjs não encontrado em $HERE"; exit 2; }
mkdir -p "$(dirname "$DEST")" && chmod 700 "$(dirname "$DEST")"

echo "emitindo token novo na máquina do LEIA..."
ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$HOST" "docker exec -i $CTR sh -c 'cat > /tmp/leia_mint.mjs'" < "$MINT"
ssh -i "$KEY" -o ConnectTimeout=15 "$HOST" "docker exec -w /usr/src/app $CTR node --env-file=.env /tmp/leia_mint.mjs '$EMAIL'"
ssh -i "$KEY" -o ConnectTimeout=15 "$HOST" "docker exec $CTR cat /tmp/leia_tk.json" > "$DEST.novo"
ssh -i "$KEY" -o ConnectTimeout=15 "$HOST" "docker exec $CTR rm -f /tmp/leia_mint.mjs /tmp/leia_tk.json" || true

python3 - "$DEST.novo" "$DEST" <<'PY'
import json, os, sys, datetime
novo, dest = sys.argv[1], sys.argv[2]
d = json.load(open(novo))
if not d.get('token') or not d.get('userId'):
    print('ERRO: o arquivo emitido não tem token/userId'); sys.exit(3)
os.replace(novo, dest)
os.chmod(dest, 0o600)
print('token novo gravado; vence em', datetime.datetime.fromtimestamp(d['expiresAtMs'] / 1000).strftime('%d/%m/%Y %H:%M'))
PY

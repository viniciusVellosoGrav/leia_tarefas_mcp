#!/usr/bin/env bash
# Watchdog semanal do token do quadro "Tarefas Leia" (roda pelo launchd).
#
# Silencioso quando está tudo bem. Só avisa (notificação do macOS + log) quando
# precisa de gente.
#
# 1. Chama o cliente, que renova sozinho quando faltam < 5 dias.
# 2. Se o token já venceu (máquina parada > 30 dias), reemite via ssh com remint.sh.
# 3. Se ainda falhar, explica o motivo em uma linha.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$HERE/.." && pwd)"
RENOVA="$HERE/remint.sh"

avisa() { echo "$(date '+%F %T') $1"; osascript -e "display notification \"$1\" with title \"Tarefas Leia\"" >/dev/null 2>&1 || true; }

saida=$(/usr/bin/env python3 - <<PY 2>&1
import sys, time
sys.path.insert(0, "$RAIZ")
from leia_tarefas import crm
try:
    c = crm.credentials()
except Exception as exc:
    print('FALHA %s' % str(exc)[:200]); sys.exit(0)
dias = (c['expiresAtMs'] / 1000 - time.time()) / 86400
if dias < 3:
    print('VENCENDO %.1f' % dias)
PY
)

case "$saida" in
  "") : ;;
  VENCENDO*)
    if bash "$RENOVA"; then echo "$(date '+%F %T') token renovado"; else avisa "Falhou ao renovar o token — o quadro está fora do Claude."; fi
    ;;
  *)
    if bash "$RENOVA"; then
      echo "$(date '+%F %T') token reemitido"
    else
      avisa "Token vencido e a reemissão falhou ($saida)."
    fi
    ;;
esac

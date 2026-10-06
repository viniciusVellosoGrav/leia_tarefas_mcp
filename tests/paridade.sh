#!/usr/bin/env bash
# Confere que os DOIS modos expõem o mesmo catálogo de ferramentas.
#
#   bash tests/paridade.sh
#
# Sai 0 e imprime "PARIDADE OK" quando os três conjuntos batem: catálogo,
# plugin nativo do Hermes e servidor MCP (subido de verdade por uv).
set -uo pipefail

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
PY_HERMES="${HERMES_AGENT_DIR:-$HOME/.hermes/hermes-agent}/venv/bin/python"

[ -x "$PY_HERMES" ] || { echo "ERRO: python do Hermes não encontrado em $PY_HERMES"; exit 2; }

echo "── modo plugin nativo do Hermes ──"
"$PY_HERMES" "$RAIZ/tests/test_paridade.py"
plugin=$?

echo
echo "── modo servidor MCP (uv run --script) ──"
python3 "$RAIZ/tests/test_mcp_stdio.py"
mcp=$?

echo
if [ $plugin -ne 0 ] || [ $mcp -ne 0 ]; then
  echo "PARIDADE FALHOU (plugin=$plugin mcp=$mcp)"
  exit 1
fi

mapfile -t via_mcp < "$RAIZ/tests/mcp_tools.txt" 2>/dev/null || readarray -t via_mcp < "$RAIZ/tests/mcp_tools.txt"
via_plugin=$("$PY_HERMES" - "$RAIZ" <<'PY'
import pathlib, sys
raiz = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(raiz))
from leia_tarefas import ferramentas
print('\n'.join('leia_tarefas_' + n for n in sorted(ferramentas.NOMES)))
PY
)

diff <(printf '%s\n' $via_plugin | sort) <(printf '%s\n' "${via_mcp[@]}" | sed 's/^/leia_tarefas_/' | sort) >/dev/null || {
  echo "PARIDADE FALHOU: nomes diferentes entre plugin e MCP"; exit 1; }

echo "modos: plugin $(printf '%s\n' $via_plugin | wc -l | tr -d ' ') ferramentas | MCP $(printf '%s\n' "${via_mcp[@]}" | wc -l | tr -d ' ') ferramentas"
echo "PARIDADE OK"

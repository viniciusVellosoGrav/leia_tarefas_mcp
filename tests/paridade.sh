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

# Os dois testes comparam cada modo com o MESMO catálogo — se ambos passaram, é
# impossível os modos divergirem entre si.
plugin_n=$("$PY_HERMES" -c "import sys; sys.path.insert(0, '$RAIZ'); from leia_tarefas import ferramentas; print(len(ferramentas.NOMES))")
mcp_n=$(wc -l < "$RAIZ/tests/mcp_tools.txt" | tr -d ' ')
if [ "$plugin_n" != "$mcp_n" ]; then
  echo "PARIDADE FALHOU: plugin=$plugin_n ferramentas, MCP=$mcp_n"
  exit 1
fi
echo "modos: plugin $plugin_n ferramentas | MCP $mcp_n ferramentas"
echo "PARIDADE OK"

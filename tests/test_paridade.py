"""Paridade entre o catálogo, o plugin nativo do Hermes e o modo servidor MCP.

Roda com o python do Hermes (precisa de `tools.registry`):

    /Users/vini/.hermes/hermes-agent/venv/bin/python tests/test_paridade.py

Não toca a rede: só lê o catálogo e monta os schemas.
"""

import os
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

# `from tools.registry import ...` só existe dentro do Hermes: garante o código do
# Hermes no caminho quando o teste roda de fora dele.
try:
    import tools.registry  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, os.environ.get('HERMES_AGENT_DIR', '/Users/vini/.hermes/hermes-agent'))

from leia_tarefas import ferramentas  # noqa: E402
from hermes_plugin import _schema_de  # noqa: E402


class CtxFalso:
    """Captura o que o plugin registraria no registry do Hermes."""

    def __init__(self):
        self.tools = []

    def register_tool(self, **kw):
        self.tools.append(kw)


def main() -> int:
    esperado = ['leia_tarefas_' + nome for nome in ferramentas.NOMES]
    ctx = CtxFalso()
    import hermes_plugin
    hermes_plugin.register(ctx)
    registrado = [t['name'] for t in ctx.tools]

    falhas = []
    if registrado != esperado:
        falhas.append('nomes registrados != catálogo\n  esperado: %s\n  veio:     %s' % (esperado, registrado))
    if len(set(esperado)) != len(esperado):
        falhas.append('nome de ferramenta repetido no catálogo')

    for tool in ctx.tools:
        fn = next(f for f in ferramentas.FERRAMENTAS if 'leia_tarefas_' + f.__name__ == tool['name'])
        derivado = _schema_de(fn)
        if tool['schema'] != derivado:
            falhas.append('schema divergente em %s' % tool['name'])
        if tool['schema']['description'] != (fn.__doc__ or '').strip():
            falhas.append('descrição divergente em %s' % tool['name'])
        if tool['toolset'] != 'leia_tarefas':
            falhas.append('toolset errado em %s' % tool['name'])
        if tool['handler'].__module__ != 'hermes_plugin':
            falhas.append('handler de %s fora do namespace do plugin (%s) — posse da tool quebra'
                          % (tool['name'], tool['handler'].__module__))

    print('catálogo: %d ferramentas' % len(esperado))
    print('plugin Hermes: %d registradas' % len(registrado))
    for f in falhas:
        print('FALHA:', f)
    print('OK' if not falhas else 'FALHOU')
    return 1 if falhas else 0


if __name__ == '__main__':
    raise SystemExit(main())

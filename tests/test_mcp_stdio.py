"""Confere o modo servidor MCP pelo caminho real: sobe `uv run --script mcp_server.py`
e fala JSON-RPC por stdio (initialize -> tools/list), sem depender de cliente MCP.

    python3 tests/test_mcp_stdio.py

Escreve os nomes das ferramentas em tests/mcp_tools.txt (usado pelo paridade.sh).
"""

import json
import pathlib
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from leia_tarefas import ferramentas  # noqa: E402


def pedir(proc, mensagem: dict) -> dict:
    proc.stdin.write(json.dumps(mensagem) + '\n')
    proc.stdin.flush()
    while True:
        linha = proc.stdout.readline()
        if not linha:
            raise SystemExit('o servidor MCP fechou a saída antes de responder')
        try:
            resposta = json.loads(linha)
        except json.JSONDecodeError:
            continue
        if resposta.get('id') == mensagem['id']:
            return resposta


def main() -> int:
    proc = subprocess.Popen(
        ['uv', 'run', '--script', str(RAIZ / 'mcp_server.py')],
        cwd=str(RAIZ), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        pedir(proc, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                     'params': {'protocolVersion': '2024-11-05',
                                'capabilities': {},
                                'clientInfo': {'name': 'teste', 'version': '1'}}})
        proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
        proc.stdin.flush()
        resposta = pedir(proc, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    finally:
        proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if 'error' in resposta:
        print('FALHA: %s' % resposta['error'])
        return 1
    nomes = sorted(t['name'] for t in resposta['result']['tools'])
    esperado = sorted(ferramentas.NOMES)
    (RAIZ / 'tests' / 'mcp_tools.txt').write_text('\n'.join(nomes) + '\n')

    print('servidor MCP: %d ferramentas' % len(nomes))
    print('catálogo:     %d ferramentas' % len(esperado))
    if nomes != esperado:
        print('FALHA: diferente do catálogo')
        print('  só no MCP:      %s' % sorted(set(nomes) - set(esperado)))
        print('  só no catálogo: %s' % sorted(set(esperado) - set(nomes)))
        return 1
    print('OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

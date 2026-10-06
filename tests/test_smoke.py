"""Leitura real do quadro pelo caminho do núcleo (rede + credencial de verdade).

    /Users/vini/.hermes/hermes-agent/venv/bin/python tests/test_smoke.py

Só LEITURA: não cria, não move e não comenta em nada. Se falhar, mostra o motivo.
"""

import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from leia_tarefas import crm, ferramentas  # noqa: E402


def main() -> int:
    cred = crm.credentials(renew_if_expiring=False)
    import time
    print('credencial ok — vence em %.1f dias (%s)' % (
        (cred['expiresAtMs'] / 1000 - time.time()) / 86400,
        time.strftime('%d/%m/%Y %H:%M', time.localtime(cred['expiresAtMs'] / 1000))))

    eu = crm.me()
    print('usuário do token: %s' % eu.get('nome'))

    pendentes = ferramentas.minhas_pendentes(limite=5)
    print('minhas pendentes: total=%s mostrados=%s' % (pendentes['total'], pendentes['mostrados']))
    for fase in pendentes['fases']:
        print('  %s: %d' % (fase['fase'], len(fase['cards'])))

    sem_dono = ferramentas.sem_responsavel(limite=5)
    print('sem responsável (A Fazer): total=%s' % sem_dono['total'])

    busca = ferramentas.buscar('a', limite=1)
    print('busca "a": %d candidato(s)' % len(busca['cards']))
    print('OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

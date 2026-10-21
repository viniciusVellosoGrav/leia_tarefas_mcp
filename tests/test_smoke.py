"""Leitura real do quadro pelo caminho do núcleo (rede + credencial de verdade).

Confere também que nenhuma fase do quadro ficou fora do mapa fixo de `crm.PHASE_IDS`.

    /Users/vini/.hermes/hermes-agent/venv/bin/python tests/test_smoke.py

Só LEITURA: não cria, não move e não comenta em nada. Se falhar, mostra o motivo.
"""

import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from leia_tarefas import crm, ferramentas  # noqa: E402


def _fases_do_quadro() -> list[dict]:
    """Fases do processo do quadro, direto do CRM (nome + id)."""
    for proc in crm.api('/crm/processes') or []:
        if proc.get('objectKey') == crm.OBJECT_KEY:
            return proc.get('phases') or []
    return []


def conferir_fases() -> int:
    """Falha quando o quadro tem fase que este mapa fixo não conhece (ou o contrário).

    Fase nova criada no CRM sem entrada em `PHASE_IDS` some de TODA listagem: o
    card volta com o id cru no campo `fase` e nunca casa com o recorte por fase.
    Já aconteceu com "Refinamento" e "Aguardando publicação" (15/10/2026).
    """
    fases = _fases_do_quadro()
    if not fases:
        print('fases: não consegui ler o processo do quadro')
        return 1
    desconhecidas = [f for f in fases if f.get('id') not in crm.PHASE_BY_ID]
    fora_do_crm = [n for n, i in crm.PHASE_IDS.items() if i not in {f.get('id') for f in fases}]
    print('fases no CRM: %d | mapeadas: %d' % (len(fases), len(crm.PHASE_IDS)))
    for f in sorted(fases, key=lambda x: x.get('order') or 0):
        marca = 'ok' if f.get('id') in crm.PHASE_BY_ID else 'FORA DO MAPA'
        print('  %-3s %-24s %s' % (f.get('order'), f.get('name'), marca))
    if fora_do_crm:
        print('AVISO: fase mapeada que não existe mais no CRM: %s' % ', '.join(fora_do_crm))
    if desconhecidas:
        print('FALHOU: fase do quadro fora de crm.PHASE_IDS: %s' % ', '.join(
            (f.get('name') or '') + ' (' + str(f.get('id')) + ')' for f in desconhecidas))
        return 1
    return 0


def main() -> int:
    cred = crm.credentials(renew_if_expiring=False)
    import time
    print('credencial ok — vence em %.1f dias (%s)' % (
        (cred['expiresAtMs'] / 1000 - time.time()) / 86400,
        time.strftime('%d/%m/%Y %H:%M', time.localtime(cred['expiresAtMs'] / 1000))))

    if conferir_fases():
        return 1

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

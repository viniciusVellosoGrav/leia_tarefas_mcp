"""Cliente do quadro "Tarefas Leia" — o CRM do ambiente grav.

Latência é o ponto: cada consulta é **uma** ida à rede quando dá, e o que se
repete fica em cache de processo por um minuto (quem usa é processo longo — o Hermes
ou o servidor MCP — e o quadro muda devagar). Escrita limpa o cache na hora, para a leitura seguinte já
refletir a mudança.

Regras do quadro que este módulo assume (apuradas no ambiente em 24/09/2026):

- Objeto `leia_tasks`, tipo de registro "Tarefas Leia" (o outro tipo do mesmo
  objeto — "Tarefas NEP" — é outro quadro e fica de fora).
- Oito fases, nesta ordem: Backlog, Refinamento, A Fazer, Com Impedimento,
  Fazendo, Aguardando Aprovação, Aguardando publicação, Feito.
- Backlog e Refinamento não são fila de trabalho (card ali ainda não foi
  refinado): ficam fora das consultas de pendente. "Pendente" = A Fazer, Com
  Impedimento, Fazendo, Aguardando Aprovação e Aguardando publicação.
  (Revisado em 21/10/2026: o quadro ganhou "Refinamento" e "Aguardando
  publicação" em 15/10/2026 e o mapa fixo de fases deixou 19 cards invisíveis
  para toda listagem — card em fase fora de `PHASE_IDS` volta com o id cru. O
  processo inteiro vem de `GET /crm/processes`; `tests/test_smoke.py` confere
  que nenhuma fase do quadro ficou fora deste mapa.)
- Quem cuida do card é o campo nativo **Responsável** (`ownerId`) — é o campo que
  o app desenha no card e o que os relatórios chamam de "Sem responsável".
  Participante não conta para isso.
  (Revisado em 08/10/2026: até então o quadro era lido pelo campo customizado
  "Membros" + participantes. O campo "Membros" saiu da definição do objeto e o
  app mostra só o Responsável — card sem ele aparece sem dono no app. Os valores
  antigos de "Membros" continuam nos registros: ver `FIELD_MEMBROS_LEGADO`.)

A credencial vive em `~/.config/leia-tarefas-mcp/credentials.json` e o token é
renovado sozinho quando está perto de vencer (mesma rota que o emitiu).
"""

from __future__ import annotations

import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

CRED_PATH = os.path.expanduser(os.environ.get('LEIA_TAREFAS_CRED', '~/.config/leia-tarefas-mcp/credentials.json'))
OBJECT_KEY = 'leia_tasks'
RECORD_TYPE = 'Tarefas Leia'
RECORD_TYPE_ID = '6a4bc681a25728ec19100475'  # "Tarefas Leia"
FIELD_MEMBROS_LEGADO = '6a4bc701a25728ec19100489'  # campo que saiu da definição do objeto: só valor velho nos registros
FIELD_RESPONSAVEL = 'std:owner'                     # rótulo "Responsável"; o valor é o `ownerId` do registro
FIELD_DESCRICAO = '6a4b0267a25728ec19100274'
FIELD_CLIENTE = '6a748c8fcb09e9df2123b34d'
FIELD_TIPO = '6a763b3b1750de2274368ab4'
FIELD_PRIORIDADE = '6a763b711750de2274368ab7'
FIELD_CHECKLIST = '6a506b201780517ef130c44d'
PHASE_ORDER = ['Backlog', 'Refinamento', 'A Fazer', 'Com Impedimento', 'Fazendo',
               'Aguardando Aprovação', 'Aguardando publicação', 'Feito']
PHASE_IDS = {
    'Backlog': '6a4b02e4a25728ec1910027b',
    'Refinamento': '6ad0dee3a6e4d53001e9882d',
    'A Fazer': '6a4b02e4a25728ec1910027c',
    'Com Impedimento': 'c3a7f6b6fa5c0c3d62ae5f03',
    'Fazendo': '6a4b02e4a25728ec1910027d',
    'Aguardando Aprovação': 'e4833d400748720e58eddd69',
    'Aguardando publicação': '6ad0dee3a6e4d53001e9882e',
    'Feito': '6a4b02e4a25728ec1910027e',
}
PHASE_BY_ID = {v: k for k, v in PHASE_IDS.items()}
DONE, DOING = 'Feito', 'Fazendo'
BACKLOG, A_FAZER = 'Backlog', 'A Fazer'
# Fila de trabalho de verdade: Backlog e Refinamento (card ainda não refinado) e Feito
# ficam de fora. "Aguardando publicação" entra: o trabalho acabou, a entrega não.
ATIVAS = ('A Fazer', 'Com Impedimento', 'Fazendo', 'Aguardando Aprovação',
          'Aguardando publicação')
TIMEOUT = 25
PAGE_SIZE = 100
BOARD_TTL = 60           # s — leitura repetida dentro desse prazo não volta à rede
NAMES_TTL = 1800         # s — nome de gente muda raramente
CAMPOS_TTL = 6 * 3600    # s — metadados dos campos do objeto quase nunca mudam
CLIENTE_TTL = 3600       # s — nome de cliente muda raramente
RENEW_MARGIN_MS = 5 * 24 * 60 * 60 * 1000
CARD_ID = re.compile(r'^[a-z0-9]{15,26}$')

_lock = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}   # chave -> (expira_em, valor)
_cred_cache: dict | None = None
_eu_cache: dict | None = None


class LeiaError(RuntimeError):
    """Falha que deve chegar ao usuário como texto, não como stack trace."""


def _cache_get(chave: str):
    with _lock:
        item = _cache.get(chave)
        if item and item[0] > time.time():
            return item[1]
    return None


def _cache_set(chave: str, valor, ttl: float = BOARD_TTL) -> None:
    with _lock:
        _cache[chave] = (time.time() + ttl, valor)


def _cache_limpa() -> None:
    with _lock:
        _cache.clear()


# ── credencial ────────────────────────────────────────────────────────────────


def _read_cred() -> dict:
    try:
        with open(CRED_PATH) as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise LeiaError(
            f'Credencial do quadro não encontrada em {CRED_PATH}. '
            'Rode `bash scripts/remint.sh` na raiz deste repositório.'
        ) from exc
    except json.JSONDecodeError as exc:
        raise LeiaError(f'Credencial ilegível em {CRED_PATH}: {exc}') from exc


def _write_cred(cred: dict) -> None:
    tmp = CRED_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(cred, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CRED_PATH)


def credentials(renew_if_expiring: bool = True) -> dict:
    """Credencial viva; renova o token antes de ele vencer."""
    global _cred_cache
    with _lock:
        if _cred_cache is None:
            _cred_cache = _read_cred()
        cred = _cred_cache
        faltando = int(cred.get('expiresAtMs', 0)) - int(time.time() * 1000)
        if renew_if_expiring and faltando < RENEW_MARGIN_MS:
            try:
                cred = _renew(cred)
            except Exception:      # renovação é otimista: o token atual ainda vale
                pass
        return cred


def _renew(cred: dict) -> dict:
    status, _, texto = _http(
        'POST',
        cred['baseUrl'].rstrip('/') + '/mcp/token',
        {'api-key': cred['apiKey'], 'auth': cred['token'], 'content-type': 'application/json'},
        corpo={},
    )
    if status >= 400:
        raise LeiaError(f'Renovação do token respondeu {status}')
    novo = json.loads(texto or '{}')
    if not novo.get('token'):
        raise LeiaError('Renovação não devolveu token')
    cred = {**cred, 'token': novo['token']}
    if novo.get('expiresInSeconds'):
        cred['expiresAtMs'] = int((time.time() + novo['expiresInSeconds']) * 1000)
    _write_cred(cred)
    return cred


# ── HTTP ──────────────────────────────────────────────────────────────────────
#
# A conexão é REUSADA (`keep-alive`). Cada requisição nova pagava TCP + TLS até a
# máquina do LEIA — ~0,48 s medidos de us-east-1 para o Brasil, mais que qualquer
# consulta do servidor (7–20 ms) ou do banco (2–5 ms). Com a conexão viva, o que
# sobra por requisição é uma ida e volta (~0,15 s).
#
# O pool existe porque o quadro é lido em paralelo (5 páginas por vez): guardar
# uma conexão só transformaria o paralelismo em fila. Conexão ociosa é
# reaproveitada; se falhar (servidor fechou do outro lado), é descartada e a
# requisição repetida numa conexão nova — só em GET, que é o que não repete
# efeito. Escrita com conexão morta falha e o erro chega ao usuário.

_pool: dict[str, list] = {}          # origem ("https://host") -> conexões ociosas
_pool_lock = threading.Lock()
POOL_MAX = 6


def _headers(cred: dict, com_corpo: bool = False) -> dict:
    h = {'api-key': cred['apiKey'], 'auth': cred['token'], 'accept': 'application/json'}
    if com_corpo:
        h['content-type'] = 'application/json'
    return h


def _pegar_conexao(origem: str):
    """Conexão ociosa daquela origem, ou uma nova (TCP + TLS na primeira vez)."""
    with _pool_lock:
        livres = _pool.get(origem)
        if livres:
            return livres.pop()
    partes = urllib.parse.urlsplit(origem)
    classe = http.client.HTTPSConnection if partes.scheme == 'https' else http.client.HTTPConnection
    return classe(partes.netloc, timeout=TIMEOUT)


def _devolver_conexao(origem: str, con, viva: bool = True) -> None:
    if con is None:
        return
    if viva:
        with _pool_lock:
            livres = _pool.setdefault(origem, [])
            if len(livres) < POOL_MAX:
                livres.append(con)
                return
    try:
        con.close()
    except Exception:
        pass


def _http(method: str, url: str, headers: dict, corpo=None, tentativas: int = 2):
    """Uma requisição HTTP reusando conexão. Devolve (status, headers, texto).

    GET é repetido uma vez quando a conexão ociosa estava morta; os demais
    métodos vão numa tentativa só (repetir escrita pode aplicar duas vezes).
    """
    partes = urllib.parse.urlsplit(url)
    origem = '%s://%s' % (partes.scheme, partes.netloc)
    alvo = partes.path + (('?' + partes.query) if partes.query else '')
    dados = json.dumps(corpo).encode() if corpo is not None else None
    total = tentativas if method.upper() in ('GET', 'HEAD') else 1
    for tentativa in range(1, total + 1):
        con = _pegar_conexao(origem)
        try:
            con.request(method, alvo, body=dados, headers=headers)
            resposta = con.getresponse()
            texto = resposta.read().decode(errors='replace')
            status, cabecalhos = resposta.status, dict(resposta.getheaders())
            _devolver_conexao(origem, con)
            return status, cabecalhos, texto
        except (http.client.HTTPException, OSError) as exc:
            _devolver_conexao(origem, con, viva=False)
            if tentativa == total:
                raise LeiaError(f'CRM inalcançável: {exc}') from exc
    raise LeiaError('CRM inalcançável: requisição não completada')


def api(path: str, params: dict | None = None, method: str = 'GET', body: dict | None = None):
    cred = credentials()
    url = cred['baseUrl'].rstrip('/') + path
    if params:
        url += '?' + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})
    status, _, texto = _http(method, url, _headers(cred, com_corpo=body is not None), corpo=body)
    if status >= 400:
        try:
            msg = json.loads(texto).get('message') or texto
        except Exception:
            msg = texto or f'HTTP {status}'
        if status == 401:
            raise LeiaError(f'Sem autorização no CRM ({status}): {msg}')
        if status == 403:
            raise LeiaError(f'Sem permissão no CRM ({status}): {msg}')
        raise LeiaError(f'CRM respondeu {status}: {msg[:300]}')
    return json.loads(texto or '{}')


def me() -> dict:
    """Usuário dono do token (id + nome), uma vez por processo."""
    global _eu_cache
    if _eu_cache:
        return _eu_cache
    u = (api('/users/self/simple') or {}).get('user') or {}
    if not u.get('id'):
        u = {**u, 'id': credentials().get('userId')}
    nome = u.get('name') or u.get('email') or u.get('id')
    _eu_cache = {'id': u.get('id'), 'nome': nome}
    _cache_set('nome:%s' % u.get('id'), nome, NAMES_TTL)
    return _eu_cache


def nomes(ids: list[str]) -> dict[str, str]:
    """id → nome de exibição. Uma chamada por lote; o resto vem do cache."""
    ids = [i for i in dict.fromkeys(ids) if i]
    faltando = [i for i in ids if _cache_get('nome:%s' % i) is None]
    if faltando:
        try:
            dados = api('/users/find', {'ids': ','.join(faltando)})
        except LeiaError:
            dados = {}
        for u in dados.get('data') or []:
            if u.get('id'):
                _cache_set('nome:%s' % u['id'], u.get('name') or u.get('email') or u['id'], NAMES_TTL)
    return {i: (_cache_get('nome:%s' % i) or i) for i in ids}


# ── cards ─────────────────────────────────────────────────────────────────────


def _membros_legado(rec: dict) -> list[str]:
    """Valores que sobraram no campo "Membros" (saiu da definição do objeto).

    Não é mais fonte de nada: existe só para o card poder avisar que tem dado
    antigo ali. Quem conta como dono do card é o Responsável nativo.
    """
    for cf in rec.get('customFields') or []:
        if cf.get('fieldId') == FIELD_MEMBROS_LEGADO:
            v = cf.get('value')
            return [x for x in (v if isinstance(v, list) else [v]) if x]
    return []


def _responsaveis(rec: dict) -> list[str]:
    """Quem cuida do card: o **Responsável** nativo (`ownerId`), só ele.

    É o campo que o app mostra no card e o que os relatórios chamam de "sem
    responsável". Participante não conta — contar participante (e o campo
    "Membros") foi o que fez a ferramenta dizer, até 08/10/2026, que um card sem
    dono tinha dono.
    """
    dono = rec.get('ownerId')
    return [dono] if dono else []


def _quando(iso: str | None) -> datetime | None:
    """Instante do CRM → datetime com fuso (o CRM manda UTC, sufixo `Z`).

    Ler a string como se já fosse hora local não é detalhe: em UTC-3 isso faz o
    card parecer 3 h mais novo do que é, e um card de ontem passa por "hoje".
    """
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone()
    except Exception:
        try:
            return datetime.strptime(iso[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc).astimezone()
        except Exception:
            return None


def _idade(iso: str | None) -> str | None:
    """Tempo decorrido em texto curto: `AGORA`, `45MIN`, `3H`, `5D`, `2M`.

    A unidade vai junto de propósito. Número puro arredondado para dias faz
    \"menos de 24 h\" virar `0` e todo mundo ler como \"criado hoje\" — já custou
    uma leitura errada do quadro. Nada de só o número: número + sufixo.
    """
    quando = _quando(iso)
    if quando is None:
        return None
    segundos = max(0.0, (datetime.now(timezone.utc) - quando).total_seconds())
    if segundos < 60:
        return 'AGORA'
    if segundos < 3600:
        return '%dMIN' % (segundos // 60)
    if segundos < 86400:
        return '%dH' % (segundos // 3600)
    if segundos < 30 * 86400:
        return '%dD' % (segundos // 86400)
    return '%dM' % (segundos // (30 * 86400))


def _local(iso: str | None) -> str | None:
    """Data do CRM em hora local, `AAAA-MM-DD HH:MM:SS` (ordena como texto)."""
    quando = _quando(iso)
    return quando.strftime('%Y-%m-%d %H:%M:%S') if quando else None


def _resumo(rec: dict, mapa: dict[str, str]) -> dict:
    return {
        'id': rec.get('id'),
        'titulo': rec.get('title'),
        'fase': PHASE_BY_ID.get(rec.get('currentPhaseId'), rec.get('currentPhaseId')),
        'responsaveis': [mapa.get(i, i) for i in _responsaveis(rec)],
        'criado_em': _local(rec.get('createdAt')),
        'atualizado_em': _local(rec.get('updatedAt')),
        'tempo_desde_criacao': _idade(rec.get('createdAt')),
        'tempo_sem_mudanca': _idade(rec.get('updatedAt')),
    }


def _pagina(cred: dict, n: int, extras: dict | None = None) -> list[dict]:
    """Uma página do quadro. `extras` são os filtros que o servidor aplica."""
    params = {'recordTypeId': RECORD_TYPE_ID, 'perPage': PAGE_SIZE, 'page': n, **(extras or {})}
    dados = api('/crm/records/%s' % OBJECT_KEY, params)
    return dados.get('items') or []


def ids_das_fases(fases: list[str] | None) -> str:
    """Nomes de fase → ids, na forma que a API aceita em `currentPhaseIds`.

    Fase desconhecida é erro do usuário, não do servidor: melhor recusar aqui do
    que mandar um id inventado e receber o quadro inteiro de volta.
    """
    nomes = [f for f in (fases or []) if f]
    invalidas = [f for f in nomes if f not in PHASE_IDS]
    if invalidas:
        raise LeiaError('Fase inválida: %s. Válidas: %s'
                        % (', '.join(invalidas), ', '.join(PHASE_ORDER)))
    return ','.join(PHASE_IDS[f] for f in nomes)


def _so_nessas_fases(rec: dict, fases: list[str] | None) -> bool:
    """Conferência local do recorte por fase — o servidor já filtrou; isto é a rede de segurança."""
    if not fases:
        return True
    atual = rec.get('currentPhaseId')
    return PHASE_BY_ID.get(str(atual) if atual else '') in fases


def _minha_lista(fases: list[str] | None = None) -> list[dict]:
    """Cards do quadro em que eu sou o **Responsável**: uma consulta, com cache.

    O filtro vai no servidor (`owner` e, com `fases`, `currentPhaseIds`), e o
    resultado é conferido aqui: servidor que ainda não conheça o parâmetro o
    ignora e devolveria o quadro inteiro — e filtrar por participante, que era o
    que se fazia, trazia card de outra pessoa. Pedir a fase no SERVIDOR é o que
    evita baixar o quadro todo (103 cards meus) para mostrar 8.
    """
    chave = 'meus' if not fases else 'meus:%s' % ','.join(fases)
    cacheado = _cache_get(chave)
    if cacheado is not None:
        return cacheado  # type: ignore[return-value]
    cred = credentials()
    eu = cred.get('userId')
    extras = {'owner': eu}
    if fases:
        extras['currentPhaseIds'] = ids_das_fases(fases)
    items, pagina = [], 1
    while True:
        lote = _pagina(cred, pagina, extras)
        items += [r for r in lote if r.get('ownerId') == eu and _so_nessas_fases(r, fases)]
        if len(lote) < PAGE_SIZE:
            break
        pagina += 1
    _cache_set(chave, items)
    return items


def _todo_o_quadro(fases: list[str] | None = None) -> list[dict]:
    """Todos os cards do quadro (paginado em paralelo, com cache).

    Com `fases`, o recorte é feito no servidor — a consulta entra pelo índice de
    fase (`ownerId, objectKey, currentPhaseId, phaseOrder`) em vez de varrer o
    quadro e descartar aqui.
    """
    chave = 'quadro' if not fases else 'quadro:%s' % ','.join(fases)
    cacheado = _cache_get(chave)
    if cacheado is not None:
        return cacheado  # type: ignore[return-value]
    cred = credentials()
    extras = {'currentPhaseIds': ids_das_fases(fases)} if fases else {}
    primeira = _pagina(cred, 1, extras)
    if len(primeira) < PAGE_SIZE:
        _cache_set(chave, primeira)
        return primeira
    paginas = [primeira]
    n = 2
    with ThreadPoolExecutor(max_workers=5) as pool:
        while True:
            lote = list(pool.map(lambda p: _pagina(cred, p, extras), range(n, n + 5)))
            paginas.extend(lote)
            if any(len(p) < PAGE_SIZE for p in lote):
                break
            n += 5
    todos = [c for p in paginas for c in p if _so_nessas_fases(c, fases)]
    _cache_set(chave, todos)
    return todos


def cards_de(quem: str, fases: list[str] | None = None) -> list[dict]:
    """Cards de um recorte: `eu` (responsável sou eu) ou `ninguem` (sem responsável).

    `fases` limita a consulta no servidor; sem ele, vem o recorte inteiro.
    """
    if quem == 'eu':
        base = _minha_lista(fases)
    elif quem == 'ninguem':
        base = [c for c in _todo_o_quadro(fases) if not _responsaveis(c)]
    else:
        raise LeiaError(f'recorte desconhecido: {quem}')
    mapa = nomes([i for c in base for i in _responsaveis(c)])
    return [_resumo(c, mapa) for c in base]


def cards_pendentes(quem: str) -> list[dict]:
    """Cards do recorte que dá para pegar agora: fora de Backlog, Refinamento e Feito.

    Backlog e Refinamento são onde a coisa ainda não foi refinada — não é fila de trabalho.
    """
    return [c for c in cards_de(quem, list(ATIVAS)) if c['fase'] in ATIVAS]


def cards_na_fase(quem: str, fase: str) -> list[dict]:
    """Cards do recorte numa fase — a fase já vai filtrada na consulta."""
    return [c for c in cards_de(quem, [fase]) if c['fase'] == fase]


def recentes(quem: str | None, fase: str, limite: int, predicado=None) -> tuple[int, list[dict]]:
    """Os `limite` cards de uma fase mais recentemente atualizados, e o total da fase.

    Ordem e corte são do SERVIDOR (`sort=-updatedAt` + `perPage`): nada de baixar o
    recorte e ordenar aqui. `quem`: `eu` (Responsável sou eu), `ninguem` (sem
    Responsável) ou None (qualquer um). Com `predicado` (filtro que o servidor não
    tem, ex. nome do responsável) as páginas — já ordenadas pelo servidor — são
    percorridas até o fim para contar o total, guardando só as `limite` primeiras.
    """
    if fase not in PHASE_IDS:
        raise LeiaError(f'Fase inválida: {fase}. Válidas: {", ".join(PHASE_ORDER)}')
    params = {'recordTypeId': RECORD_TYPE_ID, 'currentPhaseId': PHASE_IDS[fase], 'sort': '-updatedAt'}
    if quem == 'eu':
        params['owner'] = credentials().get('userId')
    elif quem == 'ninguem':
        params['owner'] = 'none'
    elif quem is not None:
        raise LeiaError(f'recorte desconhecido: {quem}')
    limite = max(0, int(limite))
    brutos, total, pagina = [], 0, 1
    while True:
        por_pagina = PAGE_SIZE if predicado else min(max(limite - len(brutos), 1), PAGE_SIZE)
        dados = api('/crm/records/%s' % OBJECT_KEY, {**params, 'page': pagina, 'perPage': por_pagina})
        lote = [r for r in (dados.get('items') or []) if r.get('currentPhaseId') == PHASE_IDS[fase]]
        if quem == 'eu':
            lote = [r for r in lote if r.get('ownerId') == params['owner']]
        elif quem == 'ninguem':
            lote = [r for r in lote if not _responsaveis(r)]
        if predicado:
            lote = [r for r in lote if predicado(r)]
            total += len(lote)
            brutos += lote[:max(0, limite - len(brutos))]
        else:
            total = int(dados.get('total') or 0)
            brutos += lote
        recebidos = len(dados.get('items') or [])
        if recebidos < por_pagina or (not predicado and len(brutos) >= limite):
            break
        pagina += 1
    brutos = brutos[:limite]
    mapa = nomes([i for c in brutos for i in _responsaveis(c)])
    return total, [_resumo(c, mapa) for c in brutos]


def _trecho(rec: dict, limite: int = 160) -> str | None:
    """Começo da descrição, numa linha — identifica o card sem ler o texto todo."""
    for cf in rec.get('customFields') or []:
        if cf.get('fieldId') == FIELD_DESCRICAO and isinstance(cf.get('value'), str):
            texto = ' '.join(cf['value'].split())
            return (texto[:limite] + '…') if len(texto) > limite else texto
    return None


def buscar(termo: str, limite: int = 15) -> list[dict]:
    """Cards do quadro cujo título contém o termo (qualquer fase).

    O filtro por tipo de registro não é enfeite: sem ele a busca do objeto traz
    junto os cards de "Tarefas NEP", que são outro quadro.
    """
    dados = api('/crm/records/%s' % OBJECT_KEY, {
        'search': termo, 'perPage': min(max(limite, 1), 100), 'recordTypeId': RECORD_TYPE_ID,
    })
    itens = [c for c in (dados.get('items') or []) if c.get('recordTypeId') in (None, RECORD_TYPE_ID)]
    mapa = nomes([i for c in itens for i in _responsaveis(c)])
    cards = []
    for c in itens:
        card = _resumo(c, mapa)
        if _trecho(c):
            card['trecho'] = _trecho(c)
        cards.append(card)
    return cards


def card_por_id(card_id: str) -> dict:
    rec = _do_quadro(api('/crm/records/%s/%s' % (OBJECT_KEY, card_id)))
    return _resumo(rec, nomes(_responsaveis(rec)))


# ── card inteiro (descrição e demais campos) ──────────────────────────────────
#
# A leitura do registro já traz os campos customizados; o que faltava era
# renderizá-los. Os metadados dos campos do objeto dizem o rótulo, o tipo e as
# opções de cada um — daí a renderização ser genérica, não uma lista fixa de
# campos: campo novo que o time crie no quadro aparece sozinho, em `outros_campos`.


def campos_meta() -> dict[str, dict]:
    """fieldId → {label, tipo, settings} no objeto do quadro. Cacheado por horas."""
    cacheado = _cache_get('campos')
    if cacheado is not None:
        return cacheado  # type: ignore[return-value]
    dados = api('/crm/objects/%s/fields' % OBJECT_KEY, {'recordTypeId': RECORD_TYPE_ID})
    meta: dict[str, dict] = {}
    for f in (dados if isinstance(dados, list) else []):
        if f.get('id'):
            meta[f['id']] = {
                'label': f.get('label') or f.get('name') or f['id'],
                'tipo': f.get('type'),
                'settings': f.get('settings') or {},
            }
    _cache_set('campos', meta, CAMPOS_TTL)
    return meta


def clientes(ids: list[str]) -> dict[str, str]:
    """id → nome do cliente. `Cliente` aponta para `account`, que tem rota
    própria (`/crm/accounts/<id>` — a rota de objeto customizado dá 403)."""
    saida: dict[str, str] = {}
    for i in [x for x in dict.fromkeys(ids) if x]:
        nome = _cache_get('cliente:%s' % i)
        if nome is None:
            try:
                nome = (api('/crm/accounts/%s' % i) or {}).get('name') or i
            except LeiaError:
                nome = i          # cliente ilegível não derruba a leitura do card
            _cache_set('cliente:%s' % i, nome, CLIENTE_TTL)
        saida[i] = nome
    return saida


def _do_quadro(rec: dict) -> dict:
    """Recusa card que não é do quadro \"Tarefas Leia\" (o objeto tem outro tipo)."""
    tipo = rec.get('recordTypeId')
    if tipo and tipo != RECORD_TYPE_ID:
        raise LeiaError(
            f'Esse card é de outro quadro ("Tarefas NEP"), não do "Tarefas Leia": {rec.get("title")}'
        )
    return rec


def _rotulo(fid: str, valor, meta: dict) -> str:
    """Valor de select → rótulo (o valor cru é mais curto: Media em vez de Média)."""
    opcoes = {o.get('value'): o.get('label') for o in (meta.get(fid, {}).get('settings') or {}).get('options') or []}
    return opcoes.get(valor) or str(valor)


def _render(fid: str, valor, meta: dict, mapa: dict[str, str], mapa_cli: dict[str, str]):
    """Renderiza um campo pelo tipo que os metadados declaram."""
    tipo = (meta.get(fid) or {}).get('tipo')
    if tipo == 'user':
        ids = valor if isinstance(valor, list) else [valor]
        return [mapa.get(i, i) for i in ids if i]
    if tipo == 'relation':
        ids = valor if isinstance(valor, list) else [valor]
        if ((meta.get(fid) or {}).get('settings') or {}).get('relatedObjectKey') == 'account':
            return [mapa_cli.get(i, i) for i in ids if i]
        return [i for i in ids if i]
    if tipo == 'checklist':
        return checklist_legivel(valor)
    if tipo == 'select_single':
        return _rotulo(fid, valor, meta)
    return valor


def _detalhe(rec: dict) -> dict:
    """O card inteiro: resumo + descrição, tipo, prioridade, cliente, checklist."""
    meta = campos_meta()
    valores = {cf['fieldId']: cf.get('value') for cf in (rec.get('customFields') or []) if cf.get('fieldId')}
    gente = list(_responsaveis(rec)) + [p.get('userId') for p in rec.get('participants') or []] + _membros_legado(rec)
    mapa = nomes(gente)
    ids_cli = valores.get(FIELD_CLIENTE) or []
    mapa_cli = clientes(ids_cli if isinstance(ids_cli, list) else [ids_cli])

    usados = {FIELD_DESCRICAO, FIELD_MEMBROS_LEGADO, FIELD_CLIENTE, FIELD_CHECKLIST, FIELD_TIPO, FIELD_PRIORIDADE}
    detalhe = _resumo(rec, mapa)
    detalhe.update({
        'tipo': _render(FIELD_TIPO, valores.get(FIELD_TIPO), meta, mapa, mapa_cli) if valores.get(FIELD_TIPO) else None,
        'prioridade': _render(FIELD_PRIORIDADE, valores.get(FIELD_PRIORIDADE), meta, mapa, mapa_cli) if valores.get(FIELD_PRIORIDADE) else None,
        'cliente': _render(FIELD_CLIENTE, ids_cli, meta, mapa, mapa_cli) or None,
        'descricao': valores.get(FIELD_DESCRICAO) or None,
        'checklist': _render(FIELD_CHECKLIST, valores.get(FIELD_CHECKLIST), meta, mapa, mapa_cli) or None,
        'dono': mapa.get(rec.get('ownerId'), rec.get('ownerId')) or None,
        'participantes': [mapa.get(p.get('userId'), p.get('userId')) for p in rec.get('participants') or []],
        'outros_campos': {
            meta.get(f, {}).get('label', f): _render(f, v, meta, mapa, mapa_cli)
            for f, v in valores.items()
            if f not in usados and not f.startswith('std:') and v not in (None, '', [], {}, False)
        } or None,
    })
    # Card sem Responsável que ainda carrega dado velho de dono (campo "Membros"
    # e/ou participantes) some como "atribuído" para quem lê participante como
    # dono, mas aparece SEM dono no app. O aviso evita essa leitura errada.
    orfaos = _membros_legado(rec) + [p.get('userId') for p in rec.get('participants') or [] if p.get('userId')]
    if not rec.get('ownerId') and orfaos:
        detalhe['aviso'] = ('Sem Responsável — no app o card aparece sem dono. O que existe aqui é '
                            'o campo antigo "Membros" e/ou Participantes (%s), que não contam como '
                            'responsável.' % ', '.join(mapa.get(i, i) for i in dict.fromkeys(orfaos)))
    return detalhe


def card_completo(card_id: str) -> dict:
    """Descrição e demais campos de um card, por id."""
    return _detalhe(_do_quadro(api('/crm/records/%s/%s' % (OBJECT_KEY, card_id))))


def mover(card_id: str, fase: str) -> dict:
    """Move o card de fase e devolve como ele ficou. Limpa o cache.

    O PUT já devolve o registro atualizado — é ele que volta, sem uma leitura
    extra. Se a resposta vier sem os campos que usamos, aí sim relemos.
    """
    if fase not in PHASE_IDS:
        raise LeiaError(f'Fase desconhecida: {fase}. Válidas: {", ".join(PHASE_ORDER)}')
    rec = api('/crm/records/%s/%s/phase' % (OBJECT_KEY, card_id), method='PUT', body={'phaseId': PHASE_IDS[fase]})
    _cache_limpa()
    registro = rec.get('record') if isinstance(rec, dict) and 'record' in rec else rec
    if isinstance(registro, dict) and registro.get('title'):
        return _resumo(registro, nomes(_responsaveis(registro)))
    return card_por_id(card_id)


def arquivar(card_id: str) -> None:
    """Manda o card para a lixeira do CRM (soft delete, com cascata). Limpa o cache."""
    api('/crm/records/%s/%s' % (OBJECT_KEY, card_id), method='DELETE')
    _cache_limpa()


# ── edição de campos (só PATCH parcial) ───────────────────────────────────────
#
# Só a rota `PATCH /crm/records/<objeto>/<id>` com `{customFields: [{fieldId, value}]}`:
# muda apenas os campos enviados e preserva o resto. O PUT do mesmo caminho NÃO serve —
# ele derruba os campos editáveis omitidos e apagaria o card. Sem fallback para PUT.

ACOES_CHECKLIST = ('marcar', 'desmarcar', 'adicionar', 'remover', 'renomear')


def registro(card_id: str) -> dict:
    """Registro cru do card, lido agora (sem cache), recusando card de outro quadro."""
    return _do_quadro(api('/crm/records/%s/%s' % (OBJECT_KEY, card_id)))


def valor_campo(rec: dict, field_id: str):
    for cf in rec.get('customFields') or []:
        if cf.get('fieldId') == field_id:
            return cf.get('value')
    return None


def patch_campos(card_id: str, campos: list[dict]) -> None:
    """Grava só os campos enviados. 404/405 = a rota parcial não existe neste ambiente."""
    _patch(card_id, {'customFields': campos})


def _patch(card_id: str, body: dict) -> None:
    """PATCH parcial do registro (`title` e/ou `customFields`); o omitido fica como está."""
    try:
        api('/crm/records/%s/%s' % (OBJECT_KEY, card_id), method='PATCH', body=body)
    except LeiaError as exc:
        if str(exc).startswith(('CRM respondeu 404', 'CRM respondeu 405')):
            raise LeiaError(
                'A rota de atualização parcial (PATCH /crm/records/%s/<id>) ainda não está publicada '
                'neste ambiente do backend. Não usei o PUT (ele apagaria campos do card) e não mexi em nada.'
                % OBJECT_KEY
            ) from exc
        raise
    _cache_limpa()


def _achar_item(itens: list[dict], ref) -> int:
    """Índice do item por posição (1-based) ou por texto exato/trecho único. Ambíguo → erro."""
    if isinstance(ref, bool) or ref is None or (isinstance(ref, str) and not ref.strip()):
        raise LeiaError('Informe qual item: a posição (1, 2, …) ou o texto dele.')
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        pos = int(ref)
        if not 1 <= pos <= len(itens):
            raise LeiaError(f'Não existe o item {pos}: o checklist tem {len(itens)} item(ns).')
        return pos - 1
    alvo = str(ref).strip().lower()
    textos = [str(it.get('text') or '') for it in itens]
    for casa in (lambda t: t.strip().lower() == alvo, lambda t: alvo in t.lower()):
        achados = [i for i, t in enumerate(textos) if casa(t)]
        if len(achados) == 1:
            return achados[0]
        if len(achados) > 1:
            lista = '; '.join('%d) %s' % (i + 1, textos[i]) for i in achados)
            raise LeiaError(f'"{ref}" casa com mais de um item — não escolhi por você: {lista}')
    raise LeiaError(f'Nenhum item do checklist casa com "{ref}".')


def aplicar_ops_checklist(itens: list[dict] | None, ops: list[dict]) -> list[dict]:
    """Aplica as operações ao checklist armazenado (`{text, checked, ...}`) e devolve a nova lista.

    Função pura. Todas as referências (posição ou texto) valem para a lista ORIGINAL, então a
    ordem das operações não desloca posições; remoções acontecem no fim e itens novos entram no
    final, na ordem pedida. Chaves extras dos itens são preservadas. Qualquer erro aborta tudo.
    Cada op: {acao: marcar|desmarcar|remover, item}, {acao: renomear, item, texto},
    {acao: adicionar, texto | textos}.
    """
    if not ops:
        raise LeiaError('Nenhuma operação informada.')
    atual = [dict(it) for it in (itens or [])]
    remover: set[int] = set()
    novos: list[dict] = []
    tocados: set[int] = set()
    for n, op in enumerate(ops, 1):
        acao = str((op or {}).get('acao') or '').strip().lower()
        if acao not in ACOES_CHECKLIST:
            raise LeiaError(f'Operação {n}: ação "{acao}" inválida. Válidas: {", ".join(ACOES_CHECKLIST)}.')
        if acao == 'adicionar':
            textos = list(op.get('textos') or [])
            if op.get('texto'):
                textos.insert(0, op['texto'])
            textos = [str(t).strip() for t in textos if str(t or '').strip()]
            if not textos:
                raise LeiaError(f'Operação {n}: "adicionar" precisa de "texto" (ou "textos").')
            novos += [{'text': t, 'checked': False} for t in textos]
            continue
        i = _achar_item(atual, op.get('item'))
        if i in remover or (acao == 'remover' and i in tocados):
            raise LeiaError(f'Operação {n}: o item {i + 1} já foi alvo de outra operação nesta chamada.')
        if acao == 'remover':
            remover.add(i)
        elif acao == 'renomear':
            texto = str(op.get('texto') or '').strip()
            if not texto:
                raise LeiaError(f'Operação {n}: "renomear" precisa do novo "texto".')
            atual[i]['text'] = texto
        else:
            atual[i]['checked'] = acao == 'marcar'
        tocados.add(i)
    return [it for i, it in enumerate(atual) if i not in remover] + novos


def checklist_legivel(itens: list[dict] | None) -> list[dict]:
    """Mesmo formato do `card`: {item, feito}."""
    return [{'item': it.get('text'), 'feito': bool(it.get('checked'))} for it in (itens or [])]


def editar_checklist(card_id: str, ops: list[dict]) -> list[dict]:
    """Lê o card agora, aplica as ops, faz PATCH só do checklist e devolve o que ficou (relido)."""
    itens = valor_campo(registro(card_id), FIELD_CHECKLIST)
    novo = aplicar_ops_checklist(itens if isinstance(itens, list) else [], ops)
    patch_campos(card_id, [{'fieldId': FIELD_CHECKLIST, 'value': novo}])
    return checklist_legivel(valor_campo(registro(card_id), FIELD_CHECKLIST))


def editar_descricao(card_id: str, descricao: str) -> str | None:
    """PATCH só da Descrição (markdown) e devolve a descrição relida."""
    patch_campos(card_id, [{'fieldId': FIELD_DESCRICAO, 'value': descricao}])
    return valor_campo(registro(card_id), FIELD_DESCRICAO)


def renomear(card_id: str, titulo: str) -> str | None:
    """PATCH só do título e devolve o título relido."""
    _patch(card_id, {'title': titulo})
    return registro(card_id).get('title')


def definir_responsavel(card_id: str, user_id: str | None) -> list[str]:
    """Define o Responsável do card (`user_id=None` remove) e devolve quem ficou, relido.

    Grava pela rota própria do responsável — `PUT /crm/records/<objeto>/<id>/owner`
    com `{"newOwnerId": <id|null>}` —, a mesma que o app usa. PATCH de campo
    customizado não serve: o app desenha o Responsável nativo, não um campo
    qualquer (foi assim que o campo "Membros" virou escrita invisível).
    Já estando como pedido, não grava nada.
    """
    atual = registro(card_id).get('ownerId')
    if (atual or None) == (user_id or None):
        return _responsaveis(registro(card_id))
    api('/crm/records/%s/%s/owner' % (OBJECT_KEY, card_id),
        method='PUT', body={'newOwnerId': user_id})
    _cache_limpa()
    return _responsaveis(registro(card_id))


# ── pessoas e participantes ───────────────────────────────────────────────────
#
# Participante NÃO é campo customizado: é `participants: [{userId, role}]` do registro, com
# rotas próprias (as que o app usa) — `POST /crm/records/<objeto>/<id>/participants`
# `{userId, role}` (adiciona ou muda o papel) e `DELETE .../participants/<userId>`. PATCH não
# mexe nisso. Não tem relação com o Responsável (`ownerId`), que continua intocado.

PAPEIS_PARTICIPANTE = ('viewer', 'editor', 'admin')


def buscar_pessoas(termo: str, limite: int = 10) -> list[dict]:
    """Pessoas ativas cujo nome casa com o termo — busca no servidor (Meilisearch), só o topo."""
    termo = (termo or '').strip()
    if not termo:
        raise LeiaError('Informe um nome (ou parte dele) para buscar a pessoa.')
    dados = api('/users/find', {'name': termo, 'limit': min(max(int(limite or 10), 1), 25),
                                'excludeServiceAccounts': 'true', 'fields': 'name,email,disabled'})
    return [{'id': u.get('id'), 'nome': u.get('name'), 'email': u.get('email')}
            for u in dados.get('data') or [] if u.get('id') and not u.get('disabled')]


def _participantes_de(rec: dict) -> list[dict]:
    mapa = nomes([p.get('userId') for p in rec.get('participants') or []])
    return [{'id': p.get('userId'), 'nome': mapa.get(p.get('userId'), p.get('userId')), 'papel': p.get('role')}
            for p in rec.get('participants') or []]


def editar_participantes(card_id: str, adicionar: list[str], remover: list[str], papel: str) -> dict:
    """Adiciona/remove participantes (por id de usuário) e devolve a lista relida e o que mudou.

    Valida tudo antes de gravar (ids existentes, sem repetir, sem o Responsável como participante),
    para um erro não deixar a edição pela metade.
    """
    if papel not in PAPEIS_PARTICIPANTE:
        raise LeiaError(f'Papel inválido: {papel}. Válidos: {", ".join(PAPEIS_PARTICIPANTE)}')
    adicionar = list(dict.fromkeys(i.strip() for i in adicionar or [] if (i or '').strip()))
    remover = list(dict.fromkeys(i.strip() for i in remover or [] if (i or '').strip()))
    if not adicionar and not remover:
        raise LeiaError('Nada a fazer: informe "adicionar" e/ou "remover" (ids de `buscar_pessoas`).')
    if set(adicionar) & set(remover):
        raise LeiaError('O mesmo id está em "adicionar" e em "remover".')
    rec = registro(card_id)
    atuais = {p.get('userId'): p.get('role') for p in rec.get('participants') or []}
    if rec.get('ownerId') in adicionar:
        raise LeiaError('Essa pessoa já é o Responsável do card; o Responsável não entra como participante. Não mexi em nada.')
    existentes = {u['id'] for u in (api('/users/find', {'ids': ','.join(adicionar), 'limit': 100}).get('data') or [])} \
        if adicionar else set()
    sem_cadastro = [i for i in adicionar if i not in existentes]
    if sem_cadastro:
        raise LeiaError('Id de usuário inexistente: %s. Use `buscar_pessoas` para achar o id. Não mexi em nada.'
                        % ', '.join(sem_cadastro))
    mudou = {'adicionados': [], 'papel_alterado': [], 'removidos': [], 'ja_estavam': [], 'nao_eram_participantes': []}
    for uid in adicionar:
        if atuais.get(uid) == papel:
            mudou['ja_estavam'].append(uid)
            continue
        api('/crm/records/%s/%s/participants' % (OBJECT_KEY, card_id), method='POST',
            body={'userId': uid, 'role': papel})
        mudou['papel_alterado' if uid in atuais else 'adicionados'].append(uid)
    for uid in remover:
        if uid not in atuais:
            mudou['nao_eram_participantes'].append(uid)
            continue
        api('/crm/records/%s/%s/participants/%s' % (OBJECT_KEY, card_id, urllib.parse.quote(uid, safe='')),
            method='DELETE')
        mudou['removidos'].append(uid)
    _cache_limpa()
    nm = nomes([i for v in mudou.values() for i in v])
    return {'participantes': _participantes_de(registro(card_id)),
            'mudou': {k: [nm.get(i, i) for i in v] for k, v in mudou.items() if v}}


# ── comentários ───────────────────────────────────────────────────────────────
#
# `/crm/comments` é genérico (objectType + objectId) e devolve do mais antigo
# para o mais novo, paginado. Para os últimos N: uma leitura mínima dá o total,
# e as duas últimas páginas de tamanho N cobrem o recorte. Só o autor edita ou
# exclui (o backend dá 403 para os outros); exclusão é soft delete (lixeira).


def _comentario(c: dict, mapa: dict[str, str]) -> dict:
    saida = {
        'id': c.get('id'),
        'autor': mapa.get(c.get('createdBy'), c.get('createdBy')),
        'criado_em': _local(c.get('createdAt')),
        'ha': _idade(c.get('createdAt')),
        'texto': c.get('content') or '',
    }
    if c.get('updatedAt') and c.get('updatedAt') != c.get('createdAt'):
        saida['editado_em'] = _local(c.get('updatedAt'))
    if c.get('attachments'):
        saida['anexos'] = [a.get('name') or a.get('key') for a in c['attachments']]
    if c.get('mentions'):
        saida['mencoes'] = [mapa.get(i, i) for i in c['mentions']]
    return saida


def _pagina_comentarios(card_id: str, pagina: int, por_pagina: int) -> dict:
    return api('/crm/comments', {'objectType': OBJECT_KEY, 'objectId': card_id,
                                 'page': pagina, 'perPage': por_pagina}) or {}


def comentarios_crus(card_id: str, limite: int = 10) -> tuple[list[dict], int]:
    """Os `limite` comentários mais recentes (crus, do mais antigo ao mais novo) e o total."""
    n = min(max(int(limite or 10), 1), 100)
    primeira = _pagina_comentarios(card_id, 1, n)
    total = int(primeira.get('total') or 0)
    if total <= n:
        return primeira.get('items') or [], total
    ultima = -(-total // n)
    itens = (_pagina_comentarios(card_id, ultima - 1, n).get('items') or []) + \
            (_pagina_comentarios(card_id, ultima, n).get('items') or [])
    return itens[-n:], total


def comentarios(card_id: str, limite: int = 10) -> dict:
    itens, total = comentarios_crus(card_id, limite)
    mapa = nomes([c.get('createdBy') for c in itens] + [m for c in itens for m in c.get('mentions') or []])
    return {'total': total, 'mostrados': len(itens), 'comentarios': [_comentario(c, mapa) for c in itens]}


def comentar(card_id: str, texto: str) -> dict:
    criado = api('/crm/comments', method='POST', body={
        'objectType': OBJECT_KEY, 'objectId': card_id, 'content': texto, 'mentions': [], 'attachments': [],
    })
    if not (criado or {}).get('id'):
        raise LeiaError('a API respondeu sem id: %s' % str(criado)[:300])
    return _comentario(criado, nomes([criado.get('createdBy')]))


def excluir_comentario(comentario_id: str) -> None:
    api('/crm/comments/%s' % comentario_id, method='DELETE')

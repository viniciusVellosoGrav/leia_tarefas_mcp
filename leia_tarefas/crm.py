"""Cliente do quadro "Tarefas Leia" — o CRM do ambiente grav.

Latência é o ponto: cada consulta é **uma** ida à rede quando dá, e o que se
repete fica em cache de processo por um minuto (quem usa é processo longo — o Hermes
ou o servidor MCP — e o quadro muda devagar). Escrita limpa o cache na hora, para a leitura seguinte já
refletir a mudança.

Regras do quadro que este módulo assume (apuradas no ambiente em 24/09/2026):

- Objeto `leia_tasks`, tipo de registro "Tarefas Leia" (o outro tipo do mesmo
  objeto — "Tarefas NEP" — é outro quadro e fica de fora).
- Seis fases, nesta ordem: Backlog, A Fazer, Com Impedimento, Fazendo,
  Aguardando Aprovação, Feito. "Pendente" = qualquer fase menos Feito.
- Quem cuida do card é o campo customizado "Membros"; "sem ninguém atribuído"
  = sem "Membros" e sem participantes.

A credencial vive em `~/.config/leia-tarefas-mcp/credentials.json` e o token é
renovado sozinho quando está perto de vencer (mesma rota que o emitiu).
"""

from __future__ import annotations

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
FIELD_MEMBROS = '6a4bc701a25728ec19100489'
FIELD_DESCRICAO = '6a4b0267a25728ec19100274'
FIELD_CLIENTE = '6a748c8fcb09e9df2123b34d'
FIELD_TIPO = '6a763b3b1750de2274368ab4'
FIELD_PRIORIDADE = '6a763b711750de2274368ab7'
FIELD_CHECKLIST = '6a506b201780517ef130c44d'
PHASE_ORDER = ['Backlog', 'A Fazer', 'Com Impedimento', 'Fazendo', 'Aguardando Aprovação', 'Feito']
PHASE_IDS = {
    'Backlog': '6a4b02e4a25728ec1910027b',
    'A Fazer': '6a4b02e4a25728ec1910027c',
    'Com Impedimento': 'c3a7f6b6fa5c0c3d62ae5f03',
    'Fazendo': '6a4b02e4a25728ec1910027d',
    'Aguardando Aprovação': 'e4833d400748720e58eddd69',
    'Feito': '6a4b02e4a25728ec1910027e',
}
PHASE_BY_ID = {v: k for k, v in PHASE_IDS.items()}
DONE, DOING = 'Feito', 'Fazendo'
BACKLOG, A_FAZER = 'Backlog', 'A Fazer'
# Fila de trabalho de verdade: Backlog (não refinado) e Feito ficam de fora.
ATIVAS = ('A Fazer', 'Com Impedimento', 'Fazendo', 'Aguardando Aprovação')
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
    req = urllib.request.Request(
        cred['baseUrl'].rstrip('/') + '/mcp/token', data=b'{}',
        headers={'api-key': cred['apiKey'], 'auth': cred['token'], 'content-type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        novo = json.loads(r.read().decode() or '{}')
    if not novo.get('token'):
        raise LeiaError('Renovação não devolveu token')
    cred = {**cred, 'token': novo['token']}
    if novo.get('expiresInSeconds'):
        cred['expiresAtMs'] = int((time.time() + novo['expiresInSeconds']) * 1000)
    _write_cred(cred)
    return cred


# ── HTTP ──────────────────────────────────────────────────────────────────────


def _headers(cred: dict, com_corpo: bool = False) -> dict:
    h = {'api-key': cred['apiKey'], 'auth': cred['token'], 'accept': 'application/json'}
    if com_corpo:
        h['content-type'] = 'application/json'
    return h


def api(path: str, params: dict | None = None, method: str = 'GET', body: dict | None = None):
    cred = credentials()
    url = cred['baseUrl'].rstrip('/') + path
    if params:
        url += '?' + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})
    req = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        headers=_headers(cred, com_corpo=body is not None), method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as exc:
        corpo = exc.read().decode(errors='replace')
        try:
            msg = json.loads(corpo).get('message') or corpo
        except Exception:
            msg = corpo or f'HTTP {exc.code}'
        if exc.code == 401:
            raise LeiaError(f'Sem autorização no CRM ({exc.code}): {msg}') from exc
        if exc.code == 403:
            raise LeiaError(f'Sem permissão no CRM ({exc.code}): {msg}') from exc
        raise LeiaError(f'CRM respondeu {exc.code}: {msg[:300]}') from exc
    except urllib.error.URLError as exc:
        raise LeiaError(f'CRM inalcançável: {exc.reason}') from exc


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


def _membros(rec: dict) -> list[str]:
    for cf in rec.get('customFields') or []:
        if cf.get('fieldId') == FIELD_MEMBROS:
            v = cf.get('value')
            return [x for x in (v if isinstance(v, list) else [v]) if x]
    return []


def _responsaveis(rec: dict) -> list[str]:
    ids = list(_membros(rec))
    for p in rec.get('participants') or []:
        if p.get('userId') and p['userId'] not in ids:
            ids.append(p['userId'])
    return ids


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
    params = {'recordTypeId': RECORD_TYPE_ID, 'perPage': PAGE_SIZE, 'page': n, **(extras or {})}
    url = cred['baseUrl'].rstrip('/') + '/crm/records/%s?' % OBJECT_KEY + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=_headers(cred)), timeout=TIMEOUT) as r:
        return json.loads(r.read().decode() or '{}').get('items') or []


def _minha_lista() -> list[dict]:
    """Cards do quadro em que eu sou responsável: uma consulta, com cache."""
    cacheado = _cache_get('meus')
    if cacheado is not None:
        return cacheado  # type: ignore[return-value]
    cred = credentials()
    filtro = json.dumps({FIELD_MEMBROS: cred.get('userId')})
    items, pagina = [], 1
    while True:
        lote = _pagina(cred, pagina, {'customFields': filtro})
        items += lote
        if len(lote) < PAGE_SIZE:
            break
        pagina += 1
    _cache_set('meus', items)
    return items


def _todo_o_quadro() -> list[dict]:
    """Todos os cards do quadro (paginado em paralelo, com cache)."""
    cacheado = _cache_get('quadro')
    if cacheado is not None:
        return cacheado  # type: ignore[return-value]
    cred = credentials()
    primeira = _pagina(cred, 1)
    if len(primeira) < PAGE_SIZE:
        _cache_set('quadro', primeira)
        return primeira
    paginas = [primeira]
    n = 2
    with ThreadPoolExecutor(max_workers=5) as pool:
        while True:
            lote = list(pool.map(lambda p: _pagina(cred, p), range(n, n + 5)))
            paginas.extend(lote)
            if any(len(p) < PAGE_SIZE for p in lote):
                break
            n += 5
    todos = [c for p in paginas for c in p]
    _cache_set('quadro', todos)
    return todos


def cards_de(quem: str) -> list[dict]:
    """Cards de um recorte: `eu` (responsável sou eu) ou `ninguem` (sem responsável)."""
    if quem == 'eu':
        base = _minha_lista()
    elif quem == 'ninguem':
        base = [c for c in _todo_o_quadro() if not _responsaveis(c)]
    else:
        raise LeiaError(f'recorte desconhecido: {quem}')
    mapa = nomes([i for c in base for i in _responsaveis(c)])
    return [_resumo(c, mapa) for c in base]


def cards_pendentes(quem: str) -> list[dict]:
    """Cards do recorte que dá para pegar agora: fora de Backlog e de Feito.

    Backlog é onde a coisa ainda não foi refinada — não é fila de trabalho.
    """
    return [c for c in cards_de(quem) if c['fase'] in ATIVAS]


def cards_na_fase(quem: str, fase: str) -> list[dict]:
    return [c for c in cards_de(quem) if c['fase'] == fase]


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
    gente = list(_responsaveis(rec)) + [rec.get('ownerId')] + [p.get('userId') for p in rec.get('participants') or []]
    mapa = nomes(gente)
    ids_cli = valores.get(FIELD_CLIENTE) or []
    mapa_cli = clientes(ids_cli if isinstance(ids_cli, list) else [ids_cli])

    usados = {FIELD_DESCRICAO, FIELD_MEMBROS, FIELD_CLIENTE, FIELD_CHECKLIST, FIELD_TIPO, FIELD_PRIORIDADE}
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


def definir_membro(card_id: str, user_id: str, dentro: bool) -> list[str]:
    """Põe ou tira uma pessoa do campo "Membros" (PATCH só desse campo, lido agora) e devolve
    os ids dos Membros como ficaram, relidos. Já estando como pedido, não grava nada."""
    atuais = _membros(registro(card_id))
    novo = [m for m in atuais if m != user_id] + ([user_id] if dentro else [])
    if dentro and user_id in atuais:
        novo = atuais
    if novo != atuais:
        patch_campos(card_id, [{'fieldId': FIELD_MEMBROS, 'value': novo}])
    return _membros(registro(card_id))


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

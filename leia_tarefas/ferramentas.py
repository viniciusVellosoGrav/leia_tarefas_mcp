"""Camada neutra de ferramentas do quadro "Tarefas Leia".

Aqui vive **toda** a regra de negócio das ferramentas — resolução de card por id
ou trecho do título, bloqueio de escrita em card de outra pessoa, agrupamento,
limites — em funções normais que devolvem `dict` e levantam `crm.LeiaError` com o
motivo. Nada aqui conhece MCP nem Hermes.

Os dois adaptadores finos registram estas mesmas funções:

- `leia_tarefas/servidor_mcp.py` — modo servidor MCP (FastMCP, usado pelo Claude Code)
- `hermes_plugin/__init__.py`    — modo plugin nativo do Hermes

Regra do repositório: **ferramenta nova nasce aqui**, com docstring (ela vira a
descrição nos dois modos) e tipos na assinatura (eles viram o JSON Schema nos
dois modos). Os adaptadores só embrulham — nunca reimplemente regra neles.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from leia_tarefas import crm  # noqa: E402

OBJETO = crm.OBJECT_KEY
TIPOS = ['Feature', 'Bug', 'Investigacao']


# ── infra comum ───────────────────────────────────────────────────────────────


def _agrupar(consulta: str, cards: list[dict], limite: int) -> dict:
    """Agrupa por fase na ordem do quadro, mais parado primeiro, e corta no limite."""
    por_fase: dict[str, list] = {}
    for c in cards:
        por_fase.setdefault(c['fase'], []).append(c)
    fases, restante = [], max(0, int(limite or 40))
    for fase in crm.PHASE_ORDER:
        if fase not in por_fase or restante == 0:
            continue
        lista = sorted(por_fase[fase], key=lambda c: (c.get('atualizado_em') or '9999-99-99', c.get('titulo') or ''))
        recorte = lista[:restante]
        restante -= len(recorte)
        fases.append({'fase': fase, 'total_na_fase': len(lista), 'cards': recorte})
    return {
        'consulta': consulta,
        'total': len(cards),
        'mostrados': sum(len(f['cards']) for f in fases),
        'fases': fases,
        'nota': ('lista completa deste recorte, agrupada por fase e ordenada do card mais parado '
                 'para o mais recente; cada card traz id, criado_em e atualizado_em (hora local) '
                 'e dois tempos em texto com unidade — tempo_desde_criacao e tempo_sem_mudanca: '
                 'AGORA, 45MIN, 3H, 5D, 2M. Leia como está: "5D" são cinco dias, "3H" são três '
                 'horas; nunca arredonde "0" para "hoje". Precisando de mais itens, aumente '
                 '"limite" numa única chamada. A descrição não vem na lista: para o card '
                 'inteiro, chame card.'),
    }


def _lista(quem: str, fase: str | None, limite: int) -> dict:
    cards = crm.cards_de(quem)
    base = 'minhas tarefas' if quem == 'eu' else 'tarefas sem nenhum responsável'
    if fase:
        return _agrupar('%s em %s' % (base, fase), [c for c in cards if c['fase'] == fase], limite)
    return _agrupar('%s já na fila de trabalho (sem Backlog e Feito)' % base,
                    [c for c in cards if c['fase'] in crm.ATIVAS], limite)


def _resolver(tarefa: str) -> tuple[dict | None, object]:
    """Aceita o id do card ou um trecho do título. Devolve (card, erro)."""
    alvo = (tarefa or '').strip()
    if not alvo:
        return None, 'Informe o card: o id dele ou um trecho do título.'
    if crm.CARD_ID.match(alvo):
        try:
            return crm.card_por_id(alvo), None
        except crm.LeiaError as exc:
            if '404' not in str(exc):
                return None, str(exc)
    achados = crm.buscar(alvo, 20)
    if not achados:
        return None, f'Nenhum card do quadro casa com "{alvo}".'
    if len(achados) == 1:
        return achados[0], None
    exato = [c for c in achados if (c['titulo'] or '').strip().lower() == alvo.lower()]
    if len(exato) == 1:
        return exato[0], None
    return None, {'erro': 'mais de um card casa com o termo; não escolhi por você', 'candidatos': achados}


def _falha(erro: object):
    if isinstance(erro, dict):
        return erro
    raise crm.LeiaError(str(erro))


def _exigir_meu(card: dict) -> None:
    """Só mexe em card cujo **Responsável** é o dono do token."""
    if crm.me()['nome'] not in card['responsaveis']:
        raise crm.LeiaError(
            f'"{card["titulo"]}" não está com você como Responsável — responsável atual: '
            f'{", ".join(card["responsaveis"]) or "ninguém"}. Não mexi no card.'
        )


def _mover(tarefa: str, fase: str) -> dict:
    card, erro = _resolver(tarefa)
    if card is None:
        return _falha(erro)
    if card['fase'] == fase:
        return {'card': card, 'nota': f'já está em {fase}: nada mudou'}
    _exigir_meu(card)
    return {'card': crm.mover(card['id'], fase), 'de': card['fase'], 'para': fase}


# ── leitura ───────────────────────────────────────────────────────────────────


def minhas_pendentes(limite: int = 40) -> dict:
    """Tarefas em que EU sou o Responsável, já na fila de trabalho — A Fazer, Com Impedimento,
    Fazendo e Aguardando Aprovação. Backlog fica de fora (não refinado) e Feito também (acabou).
    Ser só participante não conta."""
    return _lista('eu', None, limite)


def minhas_fazendo(limite: int = 40) -> dict:
    """Tarefas em que EU sou o Responsável e que estão na fase Fazendo. Ser só participante não
    conta."""
    return _lista('eu', crm.DOING, limite)


def sem_responsavel(limite: int = 40) -> dict:
    """Tarefas da coluna A Fazer SEM Responsável — refinadas e disponíveis para pegar. É o mesmo
    "sem responsável" que o app mostra no card: participante e o campo antigo "Membros" não contam.
    Backlog fica de fora de propósito."""
    return _lista('ninguem', 'A Fazer', limite)


def listar(fase: str | None = None, responsavel: str | None = None,
           incluir_backlog_e_feito: bool = False, limite: int = 60) -> dict:
    """Quadro inteiro filtrado por fase e/ou responsável (trecho do nome, ex. "Enzo"; é o
    Responsável do card — participante não conta). Sem fase, traz só as fases ativas, a não ser que
    incluir_backlog_e_feito=true."""
    if fase and fase not in crm.PHASE_IDS:
        raise crm.LeiaError(f'Fase inválida: {fase}. Válidas: {", ".join(crm.PHASE_ORDER)}')
    todos = crm._todo_o_quadro()
    mapa = crm.nomes([i for c in todos for i in crm._responsaveis(c)])
    cards = [crm._resumo(c, mapa) for c in todos]
    if fase:
        cards = [c for c in cards if c['fase'] == fase]
    elif not incluir_backlog_e_feito:
        cards = [c for c in cards if c['fase'] in crm.ATIVAS]
    if responsavel:
        alvo = responsavel.strip().lower()
        cards = [c for c in cards if any(alvo in (r or '').lower() for r in c['responsaveis'])]
    consulta = 'quadro' + (f' em {fase}' if fase else '') + (f' com responsável ~"{responsavel}"' if responsavel else '')
    return _agrupar(consulta, cards, limite)


def buscar(termo: str, limite: int = 15) -> dict:
    """Busca cards pelo título (qualquer fase). Cada resultado traz um trecho da descrição."""
    return {'termo': termo, 'cards': crm.buscar(termo, int(limite or 15))}


def card(tarefa: str) -> dict:
    """Lê o card INTEIRO: Descrição (markdown completo — relato, sintoma, causa, o que corrigir),
    Tipo, Prioridade, Cliente, Responsáveis, Dono, Participantes, Checklist e datas.
    `tarefa` = id do card ou trecho do título."""
    resumo, erro = _resolver(tarefa)
    if resumo is None:
        return _falha(erro)
    return crm.card_completo(resumo['id'])


# ── escrita ───────────────────────────────────────────────────────────────────


def iniciar(tarefa: str) -> dict:
    """Move para a fase Fazendo um card atribuído a mim. `tarefa` = id ou trecho do título."""
    return _mover(tarefa, crm.DOING)


def concluir(tarefa: str) -> dict:
    """Conclui (fase Feito) um card atribuído a mim. `tarefa` = id ou trecho do título."""
    return _mover(tarefa, crm.DONE)


def mover_fase(tarefa: str, fase: str) -> dict:
    """Move um card atribuído a mim para a fase escolhida: Backlog, A Fazer, Com Impedimento,
    Fazendo, Aguardando Aprovação ou Feito."""
    if fase not in crm.PHASE_IDS:
        raise crm.LeiaError(f'Fase inválida: {fase}. Válidas: {", ".join(crm.PHASE_ORDER)}')
    return _mover(tarefa, fase)


def arquivar(tarefa: str) -> dict:
    """Arquiva (manda para a lixeira do CRM) um card atribuído a mim ou sem ninguém atribuído.
    Some do quadro junto com comentários e histórico; só admin do CRM restaura. Confirme com o
    usuário antes. `tarefa` = id ou trecho do título."""
    card, erro = _resolver(tarefa)
    if card is None:
        return _falha(erro)
    responsaveis = card['responsaveis']
    if responsaveis and crm.me()['nome'] not in responsaveis:
        raise crm.LeiaError(
            f'"{card["titulo"]}" está atribuído a outra pessoa — responsáveis: '
            f'{", ".join(responsaveis)}. Não arquivei.'
        )
    crm.arquivar(card['id'])
    return {'arquivado': card, 'nota': 'foi para a lixeira do CRM; só admin do CRM restaura.'}


def checklist(tarefa: str, operacoes: list[dict]) -> dict:
    """Edita o checklist de um card atribuído a mim, tudo numa gravação só. `tarefa` = id ou trecho
    do título. `operacoes` = lista de objetos, aplicados juntos (posições e textos valem para o
    checklist como estava antes):
      {"acao": "marcar" | "desmarcar" | "remover", "item": <posição 1-based ou texto/trecho>}
      {"acao": "renomear", "item": <posição ou texto>, "texto": "novo texto"}
      {"acao": "adicionar", "texto": "novo item"}  (ou "textos": ["a", "b"]; entram no fim)
    Item ambíguo → erro com candidatos e nada é gravado. Devolve o checklist como ficou, lido de
    volta, no formato do `card` ({item, feito})."""
    resumo, erro = _resolver(tarefa)
    if resumo is None:
        return _falha(erro)
    _exigir_meu(resumo)
    ficou = crm.editar_checklist(resumo['id'], operacoes)
    return {'card': {'id': resumo['id'], 'titulo': resumo['titulo']}, 'checklist': ficou or None}


def editar_descricao(tarefa: str, descricao: str) -> dict:
    """Substitui a Descrição (markdown) de um card atribuído a mim. Troca o texto INTEIRO: parta
    da descrição atual (`card`) e confirme com o usuário antes. `tarefa` = id ou trecho do título.
    Devolve a descrição salva, lida de volta."""
    if not (descricao or '').strip():
        raise crm.LeiaError('Descrição vazia: informe o texto (não apago descrição por aqui).')
    resumo, erro = _resolver(tarefa)
    if resumo is None:
        return _falha(erro)
    _exigir_meu(resumo)
    salva = crm.editar_descricao(resumo['id'], descricao)
    return {'card': {'id': resumo['id'], 'titulo': resumo['titulo']}, 'descricao': salva}


def renomear(tarefa: str, titulo: str) -> dict:
    """Troca o título de um card atribuído a mim (só o título; descrição e demais campos ficam).
    `tarefa` = id ou trecho do título atual. Devolve o título salvo, lido de volta."""
    titulo = (titulo or '').strip()
    if not titulo:
        raise crm.LeiaError('Título vazio: informe o novo título.')
    resumo, erro = _resolver(tarefa)
    if resumo is None:
        return _falha(erro)
    _exigir_meu(resumo)
    salvo = crm.renomear(resumo['id'], titulo)
    return {'card': {'id': resumo['id'], 'titulo_anterior': resumo['titulo']}, 'titulo': salvo}


def _responsavel(tarefa: str, assumir: bool) -> dict:
    card, erro = _resolver(tarefa)
    if card is None:
        return _falha(erro)
    eu = crm.me()
    anterior = list(card['responsaveis'])
    if not assumir and not anterior:
        return {'card': {'id': card['id'], 'titulo': card['titulo'], 'fase': card['fase']},
                'responsavel': None, 'nota': 'o card já estava sem Responsável: nada mudou.'}
    if not assumir:
        _exigir_meu(card)          # tirar o responsável de outra pessoa não é por aqui
    ids = crm.definir_responsavel(card['id'], eu['id'] if assumir else None)
    saida = {'card': {'id': card['id'], 'titulo': card['titulo'], 'fase': card['fase']},
             'responsavel': list(crm.nomes(ids).values()) or None}
    if assumir and anterior and eu['nome'] not in anterior:
        saida['nota'] = 'o Responsável era %s e passou a ser você.' % ', '.join(anterior)
    elif assumir and anterior:
        saida['nota'] = 'o Responsável já era você: nada mudou.'
    elif assumir:
        saida['nota'] = 'o card não tinha Responsável; agora é você.'
    if not assumir:
        rec = crm.registro(card['id'])
        if eu['id'] in [p.get('userId') for p in rec.get('participants') or []]:
            saida['nota'] = ('você continua como participante do registro — mas participante não '
                             'conta como responsável: no app o card aparece sem dono.')
        else:
            saida['nota'] = 'no app o card passa a aparecer sem responsável.'
    return saida


def me_adicionar(tarefa: str) -> dict:
    """Me define como **Responsável** do card (o campo que o app mostra). Se já houver outra pessoa
    como responsável, ela é transferida para mim — a nota diz de quem era. Não muda a fase.
    `tarefa` = id ou trecho do título."""
    return _responsavel(tarefa, True)


def me_remover(tarefa: str) -> dict:
    """Tiro eu do **Responsável** (o card fica sem dono no app). Só funciona se o responsável
    atual sou eu; não muda a fase. `tarefa` = id ou trecho do título."""
    return _responsavel(tarefa, False)


def _itens(resposta) -> list[dict]:
    if isinstance(resposta, list):
        return resposta
    if isinstance(resposta, dict):
        return resposta.get('items') or resposta.get('data') or []
    return []


def _record_type_id() -> str:
    for rt in _itens(crm.api('/crm/objects/%s/record-types' % OBJETO)):
        if (rt.get('name') or '').strip().lower() == crm.RECORD_TYPE.lower():
            return rt['id']
    return crm.RECORD_TYPE_ID


def _campo_id(nome: str) -> str:
    for campo in _itens(crm.api('/crm/objects/%s/fields' % OBJETO)):
        if (campo.get('name') or '').strip().lower() == nome and campo.get('id'):
            return campo['id']
    raise crm.LeiaError(f'o campo "{nome}" não existe mais no objeto {OBJETO}')


def criar_card(titulo: str, descricao: str | None = None, tipo: str | None = None,
               fase: str = crm.A_FAZER) -> dict:
    """Cria um card no quadro. Nasce sem Responsável (disponível) e em "A Fazer" — a fila de trabalho
    de verdade; Backlog só se pedido, e nunca por padrão. Título descritivo e autocontido; a
    descrição (markdown) guarda as palavras do usuário. `tipo` (Feature, Bug, Investigacao) só
    quando for óbvio. Confere lendo o card de volta do servidor."""
    if fase not in crm.PHASE_IDS:
        raise crm.LeiaError(f'Fase inválida: {fase}. Válidas: {", ".join(crm.PHASE_ORDER)}')
    if tipo and tipo not in TIPOS:
        raise crm.LeiaError(f'Tipo inválido: {tipo}. Válidos: {", ".join(TIPOS)}')
    custom = []
    if descricao:
        custom.append({'fieldId': _campo_id('descricao'), 'value': descricao})
    if tipo:
        custom.append({'fieldId': _campo_id('tipo'), 'value': tipo})
    criado = crm.api('/crm/records/%s' % OBJETO, method='POST', body={
        'title': titulo,
        'recordTypeId': _record_type_id(),
        'currentPhaseId': crm.PHASE_IDS[fase],
        'customFields': custom,
    })
    cid = (criado or {}).get('id')
    if not cid:
        raise crm.LeiaError('a API respondeu sem id: %s' % str(criado)[:300])
    crm._cache_limpa()
    saida = crm.card_completo(cid)
    if fase == crm.BACKLOG:
        saida['nota'] = 'em Backlog o card NÃO aparece nas listas de pendente/sem responsável — é o esperado.'
    return saida


# ── comentários ───────────────────────────────────────────────────────────────


def comentarios(tarefa: str, limite: int = 10) -> dict:
    """Comentários mais recentes de um card (padrão 10, máx. 100), do mais antigo ao mais novo.
    Cada um traz id, autor, data e texto. `tarefa` = id do card ou trecho do título."""
    card, erro = _resolver(tarefa)
    if card is None:
        return _falha(erro)
    return {'card': {'id': card['id'], 'titulo': card['titulo']}, **crm.comentarios(card['id'], limite)}


def comentar(tarefa: str, texto: str) -> dict:
    """Adiciona um comentário (texto puro) a um card do quadro, em nome de quem usa o token.
    `tarefa` = id do card ou trecho do título."""
    if not (texto or '').strip():
        raise crm.LeiaError('Comentário vazio: informe o texto.')
    card, erro = _resolver(tarefa)
    if card is None:
        return _falha(erro)
    return {'card': {'id': card['id'], 'titulo': card['titulo']}, 'comentario': crm.comentar(card['id'], texto)}


def excluir_comentario(tarefa: str, comentario_id: str) -> dict:
    """Exclui (manda para a lixeira do CRM) um comentário MEU de um card. Pegue o id em
    `comentarios` e confirme com o usuário antes. `tarefa` = id do card ou trecho do título."""
    card, erro = _resolver(tarefa)
    if card is None:
        return _falha(erro)
    itens, _ = crm.comentarios_crus(card['id'], 100)
    alvo = next((c for c in itens if c.get('id') == comentario_id), None)
    if alvo is None:
        raise crm.LeiaError(f'Comentário {comentario_id} não está entre os 100 mais recentes de "{card["titulo"]}".')
    if alvo.get('createdBy') != crm.me()['id']:
        autor = crm.nomes([alvo.get('createdBy')]).get(alvo.get('createdBy'))
        raise crm.LeiaError(f'Esse comentário é de {autor}; só o autor exclui. Não mexi nele.')
    crm.excluir_comentario(comentario_id)
    return {'card': {'id': card['id'], 'titulo': card['titulo']},
            'excluido': crm._comentario(alvo, crm.nomes([alvo.get('createdBy')])),
            'nota': 'foi para a lixeira do CRM.'}


# ── catálogo único ────────────────────────────────────────────────────────────
#
# Cada ferramenta aparece UMA vez, com o emoji e a linha curta que o Hermes usa na
# interface; nome e descrição longa vêm da própria função (nome e docstring), e o
# JSON Schema vem dos tipos da assinatura. Os dois adaptadores leem daqui.

CATALOGO: list[dict] = [
    {'funcao': minhas_pendentes, 'emoji': '📋', 'curta': 'Tarefas pendentes comigo como Responsável (quadro Tarefas Leia)'},
    {'funcao': minhas_fazendo, 'emoji': '🚧', 'curta': 'Tarefas em andamento (fase Fazendo) comigo como Responsável'},
    {'funcao': sem_responsavel, 'emoji': '🫥', 'curta': 'Tarefas pendentes sem Responsável (ninguém atribuído)'},
    {'funcao': listar, 'emoji': '🗂️', 'curta': 'Quadro Tarefas Leia por fase e/ou responsável'},
    {'funcao': buscar, 'emoji': '🔎', 'curta': 'Buscar card no quadro Tarefas Leia pelo título'},
    {'funcao': card, 'emoji': '📄', 'curta': 'Ler um card inteiro: descrição, tipo, prioridade, cliente, checklist'},
    {'funcao': iniciar, 'emoji': '▶️', 'curta': 'Mover um card meu para a fase Fazendo'},
    {'funcao': concluir, 'emoji': '✅', 'curta': 'Mover um card meu para a fase Feito (concluir)'},
    {'funcao': mover_fase, 'emoji': '↔️', 'curta': 'Mover um card meu para uma fase específica do quadro'},
    {'funcao': arquivar, 'emoji': '🗑️', 'curta': 'Arquivar card (lixeira do CRM) — meu ou sem responsável'},
    {'funcao': checklist, 'emoji': '☑️', 'curta': 'Editar o checklist de um card meu (marcar, adicionar, remover, renomear)'},
    {'funcao': editar_descricao, 'emoji': '✏️', 'curta': 'Substituir a descrição de um card meu'},
    {'funcao': renomear, 'emoji': '🏷️', 'curta': 'Trocar o título de um card meu'},
    {'funcao': me_adicionar, 'emoji': '🙋', 'curta': 'Me definir como Responsável de um card do quadro'},
    {'funcao': me_remover, 'emoji': '🚪', 'curta': 'Me tirar do Responsável (o card fica sem dono)'},
    {'funcao': criar_card, 'emoji': '🆕', 'curta': 'Criar card no quadro (nasce em A Fazer, sem responsável)'},
    {'funcao': comentarios, 'emoji': '💬', 'curta': 'Comentários recentes de um card'},
    {'funcao': comentar, 'emoji': '📝', 'curta': 'Comentar em um card do quadro'},
    {'funcao': excluir_comentario, 'emoji': '❌', 'curta': 'Excluir um comentário meu (lixeira do CRM)'},
]

FERRAMENTAS = [item['funcao'] for item in CATALOGO]
NOMES = [fn.__name__ for fn in FERRAMENTAS]

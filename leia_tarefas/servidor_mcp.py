"""Adaptador MCP: expõe o catálogo de `ferramentas.py` como ferramentas MCP (FastMCP).

Fino de propósito — nenhuma regra mora aqui. Se você está mexendo em comportamento
de ferramenta, o lugar é `leia_tarefas/ferramentas.py`.

Não rode este módulo direto (`python -m leia_tarefas.servidor_mcp`): ele precisa do
pacote `mcp` 1.x, que não é o que vive no venv do Hermes. O entry point suportado é
o `mcp_server.py` da raiz do repositório, via `uv run --script`.
"""

from mcp.server.fastmcp import FastMCP

from leia_tarefas import ferramentas as f

INSTRUCOES = """\
Quadro "Tarefas Leia" — kanban interno do time, no CRM do ambiente grav. Use estas
ferramentas para qualquer pergunta sobre ele ("minhas tarefas", "sem responsável",
"mover card", "concluir tarefa", "quadro do CRM"); nunca monte chamada HTTP na mão.

Regras do quadro:
- Fases, nesta ordem: Backlog, Refinamento, A Fazer, Com Impedimento, Fazendo,
  Aguardando Aprovação, Aguardando publicação, Feito.
- Backlog e Refinamento não são fila de trabalho (não refinado, ninguém inicia ali).
  "Pendente" = A Fazer, Com Impedimento, Fazendo, Aguardando Aprovação, Aguardando
  publicação.
- "Sem ninguém atribuído" é a coluna A Fazer, não o quadro inteiro.
- Quem cuida do card é o campo nativo "Responsável" (o que o app mostra no card);
  ser participante não conta. Campo antigo "Membros" não é mais fonte de nada.
- Mudança de fase só em card com quem pergunta como Responsável; a ferramenta recusa
  com o motivo. Não contorne.
- O objeto também tem o quadro "Tarefas NEP"; estas ferramentas só falam do
  "Tarefas Leia" e recusam card do NEP.
- Arquivar = mandar para a lixeira do CRM (some do quadro; só admin do CRM restaura).
  Só em card atribuído a quem pergunta ou sem ninguém atribuído. Confirme com o
  usuário antes de arquivar. Excluir de vez não existe, de propósito.
- Comentários: `comentarios` traz os mais recentes (padrão 10). Qualquer card do quadro
  aceita comentário. Excluir comentário só o próprio autor (vai para a lixeira do CRM);
  confirme com o usuário antes. Mostre o texto dos comentários como está.
- Onde se pede um card, aceita o id (24 caracteres) ou um trecho do título. Se mais de
  um casa, voltam candidatos: quem escolhe é o usuário, nunca adivinhe.
- As listas trazem só o resumo; descrição e demais campos vêm de `card`. Mostre a
  descrição como está, sem reescrever nem resumir. Mostre títulos na íntegra.
- Checklist e descrição: `checklist` (marcar/desmarcar, adicionar, remover, renomear — várias
  operações numa chamada só) e `editar_descricao` (troca a descrição inteira). Só em card
  atribuído a quem pergunta. Mostre o checklist devolvido como está. Item = posição ou texto;
  se ambíguo a ferramenta lista candidatos: pergunte, nunca adivinhe. Editar descrição
  substitui tudo: parta da descrição atual de `card` e confirme com o usuário antes.
  Gravam só por atualização parcial (PATCH); se o backend não tiver a rota, avisam e não gravam.
- Título: `renomear` troca só o título de um card atribuído a quem pergunta.
- Card novo nasce em "A Fazer" (a fila de trabalho), sem Responsável; título descritivo e
  autocontido. Nunca criar em Backlog por padrão — só se o usuário pedir Backlog.
- Responsável: `me_adicionar` define QUEM PERGUNTA como Responsável do card (se havia outra
  pessoa, ela é transferida — a nota diz de quem era) e `me_remover` tira (o card fica sem
  dono). Valem em qualquer card do quadro e não mexem na fase. Não há ferramenta para
  atribuir outra pessoa, de propósito. Para pegar um card: `me_adicionar` e depois `iniciar`.
- Participantes: `buscar_pessoas` acha gente pelo nome (id + nome + e-mail; se mais de uma casar,
  o usuário escolhe) e `participantes` adiciona/remove participantes de qualquer card do quadro
  (por id, papel padrão editor). Não mexe no Responsável nem na fase; o Responsável não entra
  como participante. O servidor só deixa o Responsável, participante admin ou admin do CRM
  alterar — se recusar, repasse o motivo. Mostre a lista devolvida como está.
"""

mcp = FastMCP('leia-tarefas', instructions=INSTRUCOES)


def _registrar(fn) -> None:
    """Registra a função como ferramenta MCP, com o nome e a docstring dela."""
    descricao = (fn.__doc__ or '').strip()
    try:
        decorada = mcp.tool(name=fn.__name__, description=descricao)
    except TypeError:  # versões de FastMCP sem esses kwargs: cai na docstring
        decorada = mcp.tool()
    decorada(fn)


for ferramenta in f.FERRAMENTAS:
    _registrar(ferramenta)

# `f` fica exposto para quem quiser as funções cruas (testes, scripts).
__all__ = ['mcp', 'f']

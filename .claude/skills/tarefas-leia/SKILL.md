---
name: tarefas-leia
description: "Use ao ler, criar ou mover cards do quadro Tarefas Leia (CRM do GRAV)."
---

# Quadro "Tarefas Leia" (CRM do ambiente grav)

Objeto de CRM `leia_tasks`, processo **"Processo LEIA Desenvolvimento"**
(`6a4b02e4a25728ec1910027f`). Tudo aqui é **produção**, mas mexer no quadro quando o pedido manda é
o próprio trabalho: card, coluna e cronômetro são o que o time lê como "em andamento".

- **No Hermes** as ferramentas vêm do plugin nativo (`leia_tarefas_*`).
- **No Claude Code** vêm do servidor MCP `leia-tarefas` (mesmas ferramentas, sem prefixo).
- Nunca monte chamada HTTP na mão para o que a ferramenta faz: ela grava por PATCH parcial e confere
  lendo de volta.

## Fases (a lista é do processo no CRM e cresce sem aviso)

`Backlog` → `Refinamento` → `A Fazer` → `Com Impedimento` → `Fazendo` → `Aguardando Aprovação` →
`Aguardando publicação` → `Feito`

O que cada uma significa na prática:

| Fase | Significado |
| --- | --- |
| **Backlog** | ideia/demanda ainda não refinada. **Não é fila de trabalho** — card ali não está pronto e ninguém pode iniciar. |
| **Refinamento** | está sendo detalhada; também não é fila. |
| **A Fazer** | fila de trabalho de verdade: refinada e disponível para pegar. **É onde card novo do time nasce.** |
| **Com Impedimento** | bloqueada. Só sai com o motivo escrito em comentário: o que trava, desde quando, o que destrava. |
| **Fazendo** | em execução (com cronômetro ligado). |
| **Aguardando Aprovação** | entrega esperando aprovação de quem pediu; **quem aprova move para Feito**. |
| **Aguardando publicação** | implementação pronta e publicada no dev, esperando os **testes manuais**. |
| **Feito** | encerrada, por pedido explícito, após deploy verificado + comentário de fechamento. |

- **Card novo nasce em A Fazer**, não em Backlog. A criação pela API/script põe **Backlog** quando a
  fase não é informada — então sempre informe a fase. Backlog só a pedido explícito (demanda de
  terceiro que ainda não está refinada). "Bloqueado" dito pelo usuário = **Com Impedimento**, nunca
  invente coluna com esse nome.
- **Fase fora desse mapa desaparece das listagens.** Se aparecer um id de fase desconhecido, leia o
  nome em `GET /crm/processes` e corrija o mapa no catálogo — não trate o card como inexistente.

## Responsável, e quem pode escrever

- **Responsável = `ownerId`** (campo nativo, o único nome de pessoa que o card mostra). Ser
  **criador** ou **participante não conta**. "Sem ninguém atribuído" = Responsável vazio.
- **"Eu" = dono do token.** Nunca escreva o nome de outra pessoa no Responsável.
- **Escrita só em card atribuído a mim.** As exceções, de propósito: `criar_card`, `comentar`,
  `arquivar` e `me_adicionar`/`me_remover`.
- **Mover/concluir card sem dono = dois passos:** `me_adicionar` primeiro, o movimento depois — e diga
  no relatório que o card passou a ser seu.

## Começar uma atividade

1. **O card vem antes de qualquer trabalho** — código, investigação, ajuste. Se o pedido não nomeia o
   card, ache (`buscar`, 2–3 termos) ou crie.
2. `me_adicionar` → `iniciar` (coluna **Fazendo**) → **cronômetro** com o título completo e idêntico
   do card (`iniciar_cronometro_paralelo` no servidor `clockify_mcp`, ver skill `clockify-horas`).
   Coluna e relógio na mesma rodada: um sem o outro conta histórias diferentes.
3. Se o card não existir, proponha criá-lo com título em linguagem de leigo e siga.

## Terminar uma atividade

- **Implementação pronta e publicada no dev → "Aguardando publicação"** (regra do time,
  06/11/2026), sem esperar pedido: é lá que ficam os testes manuais. Não fica em "Fazendo" e não pula
  para "Feito".
- **Comente antes de mover**: o que foi feito e verificado, e em bloco próprio o que ficou fora e por
  quê. É o único lugar onde a próxima pessoa descobre que a entrega foi parcial.
- **Feito exige pedido explícito** do usuário + deploy em produção verificado + comentário de
  fechamento. Aprovação é do dono do card; **Feito é de quem aprova** — não mova por conta própria só
  porque o código está no branch.
- **Demanda concluída que depende de aprovação → "Aguardando Aprovação"**, com o mesmo comentário de
  fechamento.
- **Cronômetro para junto com o entregável** (ver `clockify-horas`), e o relatório diz início → fim e
  duração.

## Escrever o card

- **Título = efeito que a pessoa vê**, em linguagem de leigo, sem termo técnico e **sem aspas**
  (o título viaja como `descricao` do cronômetro). O nome técnico do que foi feito vive na descrição.
- **Descrição carrega a evidência com números** (payload, medição antes × depois, commit) — quem abre
  o card meses depois não deve precisar refazer a investigação.
- **O relato de quem abriu fica.** Ao concluir, **acrescente** (causa, o que foi feito, commit) em vez
  de reescrever; se reescrever, preserve o relato original num bloco `## Contexto`.
- **Não existe "como reverter" no card**: caminho de volta se decide na hora, com o histórico na mão.
- **Buscar antes de criar** (2–3 termos, inclusive o nome do subsistema): o card novo desdobra o
  existente e cita o id dele em `## Contexto`.
- **Não apague card.** `arquivar` manda para a lixeira do CRM (só admin restaura) — ação destrutiva.

## Editar fora das ferramentas (API)

- **PATCH, nunca PUT**, para editar campos: o `PUT /crm/records/leia_tasks/:id` substitui
  `customFields` inteiro e **apaga Cliente/Tipo omitidos**. O PATCH exige `title` no corpo.
- Rotas, todas com `api-key` + `Authorization` do usuário:

  | O quê | Rota |
  | --- | --- |
  | listar/buscar | `GET /crm/records/leia_tasks?search=&currentPhaseId=&perPage=` (máx. 100) |
  | ler | `GET /crm/records/leia_tasks/:id` |
  | criar | `POST /crm/records/leia_tasks` `{title, recordTypeId, currentPhaseId?, customFields:[{fieldId, value}]}` |
  | editar campos | `PATCH /crm/records/leia_tasks/:id` `{title, customFields}` |
  | mudar de fase | `PUT /crm/records/leia_tasks/:id/phase` `{phaseId}` |
  | trocar responsável | `PUT /crm/records/leia_tasks/:id/owner` `{newOwnerId}` |
  | fases | `GET /crm/processes` |
  | campos / tipos | `GET /crm/objects/leia_tasks/fields` · `/record-types` |
  | pessoa por nome | `GET /users/find?name=` (nome/apelido, não e-mail) |
  | conta (Cliente) | `GET /crm/accounts?search=` |

- Campos: **Tipo** = `Bug`, `Feature`, `Investigacao`; **Prioridade** = `Alta`, `Media`, `Baixa`
  (só parte dos cards usa); **Cliente** = conta do CRM.

## Leitura e busca

- **A lista traz resumo**: título, fase, tempo sem mudança. O card inteiro (descrição, tipo,
  prioridade, cliente, checklist) é a ferramenta `card` — a chamada mais cara do catálogo.
- **Não imprima o id na lista** (é ruído); use o id internamente nas operações.
- **Trecho curto de título casa com o card errado, em silêncio.** Use o id (24 caracteres) de um card
  já lido na conversa, ou 3+ palavras distintivas. Ambíguo devolve candidatos — quem escolhe é o
  usuário, nunca adivinhe.
- **"Criada pela Leina" é a Leina Dias**, pessoa da GRAV que abre a maior parte dos cards — não a IA.
- **`atualizado_em` não diz quem mexeu**: para atribuir ação a alguém, cruze o Responsável com
  `autor` + `criado_em` dos comentários.
- **Checklist desmarcada não é trabalho não feito**: marcar é ato manual; o comentário do card é o
  registro. Só marque item com prova (commit, deploy).
- **Erro transitório de rede (502/504 do nginx, "CRM inalcançável")**: repita a mesma chamada — nada
  ficou pela metade. Só reporte falha depois de duas seguidas.

## "Já fizemos isso?"

A resposta sai do **código**, não do card: `git fetch`, `git log --grep`/`-S`, e diga em qual branch o
commit está (`origin/dev` ≠ `origin/main` ≠ produção). A lista "o que falta" da descrição envelhece —
confira item por item antes de virar plano.

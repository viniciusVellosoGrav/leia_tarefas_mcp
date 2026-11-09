# leia-tarefas

Quadro **"Tarefas Leia"** (kanban interno do CRM do ambiente `grav`) em **um** código, servido de
**duas** formas:

- **servidor MCP** (stdio) — consumido pelo Claude Code;
- **plugin nativo do Hermes** — ferramentas no chat (desktop e Discord).

Antes existiam duas cópias do mesmo cliente, em pastas diferentes, que já tinham divergido (o plugin
do Hermes parou em 8 ferramentas; o servidor MCP foi para 19; hoje são 23). Aqui o núcleo é um só e
os dois modos leem o **mesmo catálogo**.

## Independência (importante)

Este repositório **não conhece** o cronômetro e não depende dele: gerencia card, fase, Responsável,
checklist e comentários. O único elo com o [clockify_mcp](https://github.com/viniciusVellosoGrav/clockify_mcp)
é um **contrato de texto**: o título exato do card é a `descricao` do lançamento no Clockify — quem
cria a ponte é o agente. A rotina completa do LEIA usa os dois (ver "A rotina de uma atividade do
LEIA"), mas cada um instala e funciona sozinho.

## Instalação numa máquina nova

### Pré-requisitos

- `uv` no PATH — instalador oficial por sistema:
  - macOS: `brew install uv`
  - Linux / WSL: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `winget install --id=astral-sh.uv -e` (ou `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`)
- Um cliente: **Claude Code** (`claude`) ou **Hermes**.
- Python **não** precisa estar instalado: o núcleo (`leia_tarefas/`) usa só a stdlib e o
  `mcp_server.py` é um script PEP 723 (`requires-python >=3.10`, dependência `mcp`) — o
  `uv run --script` baixa um Python gerenciado quando o da máquina é antigo (macOS ainda vem 3.9,
  várias distros Linux vêm 3.8/3.9).

### 1. Credencial (uma só, para os dois modos)

`~/.config/leia-tarefas-mcp/credentials.json` — o `~` vale nos três sistemas (no Windows é
`C:\Users\<você>`, então o arquivo fica em `C:\Users\<você>\.config\leia-tarefas-mcp\credentials.json`;
restrinja a leitura ao seu usuário com `chmod 600` no macOS/Linux). Ou o caminho em
`$LEIA_TAREFAS_CRED` / `%LEIA_TAREFAS_CRED%`:

```json
{
  "baseUrl": "https://grav-lightsail.grav.digital/leia_prod",
  "apiKey": "CHAVE_DO_AMBIENTE",
  "token": "TOKEN_PESSOAL",
  "userId": "ID_DO_DONO_DO_TOKEN",
  "expiresAtMs": 1765000000000
}
```

- `baseUrl`: o do servidor **GRAV**, exatamente o acima (o CRM vive na raiz; o `/clockify/api/v1` é
  outro servidor, o `clockify_mcp`).
- `apiKey`: chave **do ambiente** (`API_KEY` do backend, a mesma que o app usa) — não é pessoal e é
  a mesma para todo mundo no GRAV.
- `token`: **pessoal**, JWT de 30 dias emitido para o e-mail da pessoa. É ele que decide o "eu" das
  ferramentas — nunca copie o de outra pessoa.
- `userId` / `expiresAtMs`: dono do token e prazo; o cliente usa os dois para renovar.

**Emissão do token** (a primeira vez, ou depois de 30 dias parado): `scripts/mint_remoto.mjs` roda
**dentro do container do backend** (`leia-app`) na máquina do LEIA, procura o usuário pelo e-mail no
Mongo e grava o arquivo de credencial — nada de segredo passa pelo terminal. O `scripts/remint.sh`
faz o caminho completo por ssh (host e chave no próprio script) e pega o e-mail do login do Claude
Code (`~/.claude.json`); para emitir para outra pessoa, rode o `mint_remoto.mjs` com o e-mail dela.
**Renovação normal é automática**: o cliente renova sozinho quando faltam menos de 5 dias
(`POST /mcp/token`), então só se mexe nisso quando o token já venceu.

### 2. Registrar no Claude Code

```
claude mcp add --scope user leia-tarefas -- uv run --script /caminho/absoluto/leia-tarefas/mcp_server.py
```

- `--scope user` = todos os projetos; `--scope local` (padrão) = só no diretório onde rodou.
- Caminho **absoluto** (o Claude sobe o servidor fora do diretório do comando): macOS/Linux
  `/Users/voce/leia-tarefas/mcp_server.py`; Windows `C:\Users\voce\leia-tarefas\mcp_server.py` (entre
  aspas se houver espaço).
- Conferir: `claude mcp get leia-tarefas` → `Status: ✔ Connected`, `Type: stdio`, `Command: uv`,
  `Args: run --script .../mcp_server.py`. Remover: `claude mcp remove leia-tarefas -s user`.
- Equivalentemente, no `~/.claude.json` (chave global `mcpServers`):

  ```json
  "leia-tarefas": {
    "type": "stdio",
    "command": "uv",
    "args": ["run", "--script", "/caminho/para/leia-tarefas/mcp_server.py"],
    "env": {}
  }
  ```

### 3. Registrar no Hermes (plugin nativo)

Symlink (ou junção, no Windows), sem copiar nada — fonte única:

```bash
# macOS / Linux
ln -s ~/projects/vit/leia-tarefas/hermes_plugin ~/.hermes/plugins/leia-tarefas
```

```bat
:: Windows (cmd) — junção não exige modo de desenvolvedor nem admin
mklink /J "%USERPROFILE%\.hermes\plugins\leia-tarefas" "C:\caminho\leia-tarefas\hermes_plugin"
```

E `leia-tarefas` em `plugins.enabled` do `~/.hermes/config.yaml` (no Windows, o mesmo arquivo dentro
do seu perfil). O `__init__.py` acha o repositório
por `Path(__file__).resolve().parents[1]` (segue o symlink/junção), então não se instala nada no venv
do Hermes — isso também sobrevive a `hermes update`. O toolset é montado quando o processo sobe:
mudança no plugin só aparece depois de reiniciar (desktop e gateway).

### 4. Conferir antes de usar

Em sessão nova, pedir **só leitura**: "minhas tarefas pendentes". Vem agrupado por fase, sem id.
No repositório, `bash tests/paridade.sh` compara catálogo, plugin e MCP (exige `uv` e o venv do
Hermes); `tests/test_smoke.py` faz leitura real do quadro. Os scripts do repo são **bash**: no
Windows, rode em WSL ou Git Bash.

## Como usar no contexto da Leia

1. **O card vem antes do trabalho.** Qualquer atividade do LEIA — código, investigação, ajuste —
   começa achando/criando o card: `me_adicionar` (Responsável = eu), `iniciar` (coluna **Fazendo**) e,
   no mesmo passo, o cronômetro do `clockify_mcp` com o título **completo e idêntico** do card.
2. **"Eu" = dono do token.** Nunca escrever outra pessoa no Responsável. Escrita (mover, editar,
   concluir) só em card atribuído a mim; as exceções de propósito são `criar_card`, `comentar` e
   `arquivar` (e `me_adicionar`/`me_remover`, que só mexem em mim).
3. **Mover/concluir card sem dono = dois passos:** `me_adicionar` primeiro, movimento depois.
4. **Fases são lista fechada** (do processo no CRM, cresce sem aviso): Backlog, Refinamento, A Fazer,
   Com Impedimento, Fazendo, Aguardando Aprovação, Aguardando publicação, Feito. **Backlog e
   Refinamento não são fila de trabalho**; card novo nasce em **A Fazer**. "Bloqueado" = **Com
   Impedimento** (e sai de lá com o motivo escrito em comentário).
5. **Concluir** meu card o leva a **Aguardando Aprovação** — quem move para **Feito** é quem aprova.
   Implementação entregue e publicada no dev vai para **Aguardando publicação** (é onde ficam os
   testes manuais), não direto para Feito.
6. **Título = efeito que a pessoa vê**, em linguagem de leigo, sem aspas; o nome técnico fica na
   descrição. Sem aspas porque o título viaja como `descricao` do cronômetro.
7. **Trecho curto de título casa com o card errado, em silêncio.** Use o **id** (24 caracteres) do
   card que você já leu na conversa, ou 3+ palavras distintivas; id ambíguo volta com candidatos e
   quem escolhe é o usuário.
8. **Lista traz resumo.** O card inteiro (descrição, tipo, prioridade, cliente, checklist) é
   `card` — a chamada mais cara do catálogo; não montar chamada HTTP na mão para ler card.
9. **Registro ≠ estado:** checklist desmarcada não quer dizer trabalho não feito; o comentário do
   card é o diário da execução.

## Ferramentas (23)

Leitura: `minhas_pendentes`, `minhas_fazendo`, `minhas_na_fase`, `sem_responsavel`, `listar`,
`buscar`, `card`, `comentarios`. Escrita: `criar_card`, `iniciar`, `mover_fase`, `concluir`,
`arquivar`, `checklist`, `editar_descricao`, `renomear`, `me_adicionar`, `me_remover`,
`buscar_pessoas`, `participantes`, `comentar`, `excluir_comentario`.

Regra do repositório: **ferramenta nova nasce em `leia_tarefas/ferramentas.py`**, com docstring
(vira a descrição nos dois modos) e tipos na assinatura (viram o JSON Schema). Os adaptadores
(`servidor_mcp.py` e `hermes_plugin/`) não têm regra de negócio.

## A rotina de uma atividade do LEIA (dois servidores)

1. achar/abrir o card (`card`) e virar Responsável (`me_adicionar`);
2. mover para **Fazendo** (`iniciar`);
3. ligar o cronômetro com o título **completo e idêntico** do card
   (`iniciar_cronometro_paralelo` no servidor da Leia, que aceita vários timers ao mesmo tempo);
4. no fim: comentar o que foi entregue, **parar o cronômetro pelo id** e mover o card para a fase
   que o processo pede (implementação pronta → "Aguardando publicação").

O `clockify_mcp` não conhece o CRM e este repositório não conhece o relógio: o elo é o título do
card repetido como `descricao` do lançamento. É assim que o relatório do time casa hora e card.

## Layout e testes

```
mcp_server.py                 entry point do modo MCP (uv run --script; PEP 723)
leia_tarefas/
  crm.py                      cliente HTTP do CRM + regras do quadro (só stdlib)
  ferramentas.py              catálogo NEUTRO das 23 ferramentas — regra de negócio mora aqui
  servidor_mcp.py             adaptador fino: catálogo -> ferramentas MCP (FastMCP)
hermes_plugin/                adaptador fino: catálogo -> tools do registry do Hermes
scripts/remint.sh             reemite o token (ssh até a máquina do LEIA), com trava
scripts/mint_remoto.mjs       emissor que roda no container do backend (Mongo + JWT)
scripts/renova.sh             watchdog do token (silencioso quando está tudo bem)
tests/paridade.sh             compara os três conjuntos (catálogo, plugin, MCP)
```

Uma pegadinha do lado Hermes: o handler registrado precisa ser definido **dentro** de
`hermes_plugin/__init__.py` (o registry amarra a posse da tool ao namespace `hermes_plugins.*`);
registrar direto a função do núcleo quebra desabilitar/recarregar o plugin em runtime.

## Credencial e renovação

O token vive em `~/.config/leia-tarefas-mcp/credentials.json` (600; no Windows, sob o seu perfil), o
**mesmo arquivo para o chat e para o Claude**. Renova sozinho quando faltam menos de 5 dias. Vencido
(máquina > 30 dias parada), `bash scripts/remint.sh` emite de novo via ssh — é bash, então no Windows
rode em WSL ou Git Bash.

**Um** watchdog chama isso: o cron do Hermes (`leia_tarefas_renova.sh`, segunda 8h), que avisa no
chat quando falha — silêncio dele = está tudo bem. O agendador do launchd (macOS) foi descartado de
propósito: um só, para não haver dois lugares para manter (e um deles envelhecer).

## Regras do agente (o comportamento do time, versionado)

As ferramentas vêm do plugin/MCP; o **comportamento** que as usa bem mora em uma skill, versionada
aqui em [`.claude/skills/tarefas-leia/SKILL.md`](.claude/skills/tarefas-leia/SKILL.md): fases e o que
cada uma significa, card novo em A Fazer (a API põe Backlog quando a fase não é informada),
Responsável = `ownerId`, escrita só em card atribuído a mim, começo = Fazendo + cronômetro, término =
Aguardando publicação, PATCH e nunca PUT, o relato de quem abriu fica. Instale no agente para o
comportamento ser o mesmo da equipe:

```bash
# Claude Code (skill global, vale em qualquer projeto)
ln -s /caminho/absoluto/leia-tarefas/.claude/skills/tarefas-leia ~/.claude/skills/tarefas-leia

# Hermes
ln -s /caminho/absoluto/leia-tarefas/.claude/skills/tarefas-leia ~/.hermes/skills/tarefas-leia
```

```bat
:: Windows (cmd) — junção no lugar do symlink
mklink /J "%USERPROFILE%\.claude\skills\tarefas-leia" "C:\caminho\leia-tarefas\.claude\skills\tarefas-leia"
mklink /J "%USERPROFILE%\.hermes\skills\tarefas-leia" "C:\caminho\leia-tarefas\.claude\skills\tarefas-leia"
```

Ao atualizar a skill no repositório, o agente de todo mundo passa a ler a mesma regra (symlink, não
cópia).

## Passar para outra pessoa

Vai no repositório: o código, os testes e este README. **Não** vai: `credentials.json`, `token`,
`apiKey` do ambiente, chave ssh e o valor que o `mint_remoto.mjs` assinaria — o script lê a `API_KEY`
do `.env` do backend, e o `remint.sh` guarda host e caminho de chave da máquina do LEIA. Repositório
**privado**.

Cada pessoa precisa de: acesso ao repo, a `apiKey` do ambiente (mesma para todos) e o **próprio**
token, emitido para o e-mail dela com o `mint_remoto.mjs` (alguém com acesso à máquina do LEIA roda;
depois disso a renovação é automática).

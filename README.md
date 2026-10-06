# leia-tarefas

Quadro **"Tarefas Leia"** (kanban interno do CRM do ambiente `grav`) em **um**
código, servido de **duas** formas:

- **servidor MCP** (stdio) — consumido pelo Claude Code;
- **plugin nativo do Hermes** — ferramentas nativas no chat (desktop e Discord).

Antes existiam duas cópias do mesmo cliente, em pastas diferentes, que já tinham
divergido (o plugin do Hermes parou em 8 ferramentas; o servidor MCP foi para 19).
Aqui o núcleo é um só e os dois modos leem o **mesmo catálogo**.

## Layout

```
mcp_server.py                 entry point do modo MCP (uv run --script; PEP 723)
leia_tarefas/
  crm.py                      cliente HTTP do CRM + regras do quadro (só stdlib)
  ferramentas.py              catálogo NEUTRO das 19 ferramentas — regra de negócio mora aqui
  servidor_mcp.py             adaptador fino: catálogo -> ferramentas MCP (FastMCP)
hermes_plugin/
  __init__.py                 adaptador fino: catálogo -> tools do registry do Hermes
  plugin.yaml                 manifesto do plugin (nome: leia-tarefas)
scripts/
  remint.sh                   reemite o token na máquina do LEIA (ssh), com trava
  renova.sh                   watchdog do token (silencioso quando está tudo bem)
  mint_remoto.py              emissor que roda no servidor
tests/
  test_paridade.py            plugin do Hermes == catálogo
  test_mcp_stdio.py           servidor MCP (subido de verdade) == catálogo
  paridade.sh                 roda os dois e compara
```

## Regra do repositório

**Ferramenta nova nasce em `leia_tarefas/ferramentas.py`**, com docstring (vira a
descrição nos dois modos) e tipos na assinatura (viram o JSON Schema nos dois
modos), e entra em `CATALOGO` com emoji + linha curta. Os adaptadores não têm
regra de negócio: se você mexeu em comportamento e não mexeu em `ferramentas.py`,
mexeu no lugar errado.

Uma pegadinha do lado Hermes: o handler registrado precisa ser definido **dentro**
de `hermes_plugin/__init__.py` (o registry do Hermes amarra a posse da tool ao
namespace `hermes_plugins.*`). Registrar direto a função do núcleo quebra
desabilitar/recarregar o plugin em runtime — por isso o fecho em `_handler`.

## Como cada modo roda

**MCP (Claude Code)** — `~/.claude.json`:

```json
"leia-tarefas": {
  "type": "stdio",
  "command": "uv",
  "args": ["run", "--script", "/Users/vini/projects/vit/leia-tarefas/mcp_server.py"],
  "env": {}
}
```

**Plugin nativo do Hermes** — symlink (fonte única; nada é copiado):

```bash
ln -s ~/projects/vit/leia-tarefas/hermes_plugin ~/.hermes/plugins/leia-tarefas
```

O `__init__.py` acha o repositório por `Path(__file__).resolve().parents[1]`
(`resolve()` segue o symlink), então não precisa instalar nada no venv do Hermes —
o que também sobrevive a `hermes update`. `leia-tarefas` já está em
`plugins.enabled` do `config.yaml`. O toolset é montado quando o processo sobe:
mudança no plugin só aparece depois de reiniciar (desktop e gateway).

## Credencial

Uma só, compartilhada pelos dois modos:
`~/.config/leia-tarefas-mcp/credentials.json` (600), com override por
`LEIA_TAREFAS_CRED`. O cliente renova sozinho quando faltam menos de 5 dias;
vencido (máquina parada > 30 dias), `bash scripts/remint.sh` emite de novo direto
na máquina do LEIA, via ssh.

Dois watchdogs chamam isso — o cron do Hermes (`leia_tarefas_renova.sh`, segunda
8h, avisa no chat) e o launchd `digital.grav.leia-tarefas-renova`. `remint.sh`
tem trava (lock em `$TMPDIR`) para duas execuções simultâneas não emitirem dois
tokens. Silêncio dos dois = está tudo bem.

## Verificar

```bash
bash tests/paridade.sh          # exige uv e o venv do Hermes
```

Compara os três conjuntos (catálogo, plugin, MCP). Leitura real do quadro:

```bash
"$HOME/.hermes/hermes-agent/venv/bin/python" tests/test_smoke.py
```

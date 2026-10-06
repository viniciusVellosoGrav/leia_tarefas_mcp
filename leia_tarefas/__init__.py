"""Núcleo do quadro "Tarefas Leia" (CRM do ambiente grav) — um cliente, dois modos.

Este pacote é usado de duas formas, do mesmo código:

- **servidor MCP** (Claude Code e qualquer cliente MCP): `leia_tarefas.servidor_mcp`
- **plugin nativo do Hermes**: `hermes_plugin/__init__.py`

`leia_tarefas/crm.py` é o cliente HTTP e as regras do quadro; `ferramentas.py` é a
camada neutra com as ferramentas (é lá que ferramenta nova nasce); os adaptadores
são finos, um por modo. A credencial é uma só:
`~/.config/leia-tarefas-mcp/credentials.json` (ou o que `LEIA_TAREFAS_CRED` apontar).
"""

__version__ = '1.0.0'

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2"]
# ///
"""Entry point do modo servidor MCP do quadro "Tarefas Leia".

É o que o Claude Code executa (`uv run --script .../mcp_server.py`, via
`mcpServers.leia-tarefas` em `~/.claude.json`). Sobe o FastMCP com todas as
ferramentas de `leia_tarefas/ferramentas.py` no transporte stdio.

O mesmo repositório também roda como plugin nativo do Hermes
(`hermes_plugin/__init__.py`) — ver o README.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from leia_tarefas.servidor_mcp import mcp  # noqa: E402

if __name__ == '__main__':
    mcp.run()

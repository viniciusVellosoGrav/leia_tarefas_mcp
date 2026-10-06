"""Plugin nativo do Hermes para o quadro "Tarefas Leia".

Adaptador **fino** sobre `leia_tarefas/ferramentas.py`: registra no registry do
Hermes exatamente as ferramentas do catálogo compartilhado — as mesmas que o modo
servidor MCP expõe. Nenhuma regra de negócio mora aqui.

Duas coisas a saber antes de mexer:

- o JSON Schema de cada ferramenta é **derivado da assinatura** da função no
  catálogo (`_schema_de`), e o texto da descrição vem da docstring dela — assim os
  dois modos não divergem. Ferramenta nova se adiciona em `ferramentas.CATALOGO`.
- o *handler* que vai para o registry é um fecho criado dentro de `register()`, ou
  seja, definido neste módulo (`hermes_plugins.*`). O registry do Hermes amarra a
  posse da ferramenta ao namespace do plugin: se você registrar direto a função do
  núcleo (namespace `leia_tarefas.*`), desabilitar/recarregar o plugin em runtime
  passa a ser recusado.

Este diretório é consumido por symlink a partir de `~/.hermes/plugins/leia-tarefas`
— ver o README do repositório.
"""

import inspect
import pathlib
import sys
import typing

# O plugin é carregado de ~/.hermes/plugins/leia-tarefas (symlink para este
# diretório); resolve() segue o link, então dá para achar o repositório real e
# importar o pacote sem instalar nada no venv do Hermes.
_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from tools.registry import tool_error, tool_result  # noqa: E402

from leia_tarefas import crm, ferramentas  # noqa: E402

TOOLSET = 'leia_tarefas'

# Valores enumeráveis por nome de parâmetro (o mesmo parâmetro nos dois modos).
ENUMS = {'fase': crm.PHASE_ORDER, 'tipo': ferramentas.TIPOS}

_TIPOS_JSON = {str: 'string', int: 'integer', bool: 'boolean', float: 'number'}


def _schema_de(fn) -> dict:
    """JSON Schema dos parâmetros, derivado dos tipos da assinatura de `fn`."""
    dicas = typing.get_type_hints(fn)
    props, obrigatorios = {}, []
    for nome, parametro in inspect.signature(fn).parameters.items():
        tipo = dicas.get(nome, str)
        base = tipo
        if typing.get_origin(tipo) is typing.Union:  # Optional[...] / X | None
            opcoes = [a for a in typing.get_args(tipo) if a is not type(None)]
            base = opcoes[0] if opcoes else str
        if typing.get_origin(base) is list:
            argumentos = typing.get_args(base)
            item = _TIPOS_JSON.get(argumentos[0] if argumentos else str, 'object')
            especificacao: dict = {'type': 'array', 'items': {'type': item}}
        else:
            especificacao = {'type': _TIPOS_JSON.get(base, 'string')}
        if nome in ENUMS:
            especificacao['enum'] = list(ENUMS[nome])
        if parametro.default is not inspect.Parameter.empty:
            especificacao['default'] = parametro.default
        props[nome] = especificacao
        if parametro.default is inspect.Parameter.empty:
            obrigatorios.append(nome)
    return {'name': 'leia_tarefas_' + fn.__name__,
            'description': (fn.__doc__ or '').strip(),
            'parameters': {'type': 'object', 'properties': props, 'required': obrigatorios}}


def _handler(fn, aceitos: tuple):
    """Fecho que adapta a assinatura tipada ao contrato do dispatcher do Hermes.

    O registry chama `handler(args, **kw)` com o dicionário de argumentos como
    primeiro posicional. Precisa ser definido aqui neste módulo (posse da tool).
    """
    def chamar(args=None, **_kw):
        dados = args if isinstance(args, dict) else {}
        try:
            return tool_result(fn(**{k: v for k, v in dados.items() if k in aceitos}))
        except crm.LeiaError as exc:
            return tool_error(str(exc))
        except Exception as exc:
            return tool_error(f'Falha em {fn.__name__}: {type(exc).__name__}: {exc}')

    chamar.__name__ = 'leia_tarefas_' + fn.__name__
    return chamar


def register(ctx) -> None:
    """Registra no chat as ferramentas do catálogo compartilhado."""
    for item in ferramentas.CATALOGO:
        fn = item['funcao']
        ctx.register_tool(
            name='leia_tarefas_' + fn.__name__,
            toolset=TOOLSET,
            handler=_handler(fn, tuple(inspect.signature(fn).parameters)),
            emoji=item['emoji'],
            description=item['curta'],
            schema=_schema_de(fn),
        )


__all__ = ['register']

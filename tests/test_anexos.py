"""Lógica de anexos sem rede: validação de arquivos e escolha do campo `file`.

    python3 tests/test_anexos.py
"""

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from leia_tarefas import crm, ferramentas as f  # noqa: E402

um = [{'id': 'a', 'label': 'Anexos'}]
dois = um + [{'id': 'b', 'label': 'Prints'}]

assert f._escolher_campo_arquivo([], None)[0] is None and 'nenhum campo' in f._escolher_campo_arquivo([], None)[1]['erro']
assert f._escolher_campo_arquivo(um, None) == (um[0], None)
r = f._escolher_campo_arquivo(dois, None)
assert r[0] is None and r[1]['candidatos'] == dois
assert f._escolher_campo_arquivo(dois, 'prints')[0]['id'] == 'b'
assert f._escolher_campo_arquivo(dois, 'zzz')[0] is None

with tempfile.TemporaryDirectory() as d:
    ok = pathlib.Path(d, 'a.pdf')
    ok.write_bytes(b'x')
    v = crm.validar_arquivos([str(ok)])
    assert v[0]['mime'] == 'application/pdf' and v[0]['tamanho'] == 1
    for ruim in (str(pathlib.Path(d, 'nao.pdf')),):
        try:
            crm.validar_arquivos([str(ok), ruim])
            raise SystemExit('deveria falhar')
        except crm.LeiaError as exc:
            assert 'Nada foi enviado' in str(exc)
print('ok')

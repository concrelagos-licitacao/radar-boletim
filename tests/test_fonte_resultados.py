# -*- coding: utf-8 -*-
"""Teste OFFLINE minimo de fonte_resultados.py (sem rede).
Roda: C:/Users/.../python.exe -m pytest tests/test_fonte_resultados.py  (ou direto)."""
import fonte_resultados as fr


def test_import_sem_efeito_colateral():
    # se o import disparasse coleta, o modulo nem carregaria em ambiente sem rede/creds.
    assert callable(fr.resultado_da_compra)
    assert callable(fr.todos_resultados_da_compra)


def test_seq_de_nc():
    assert fr._seq_de_nc('66229717000118-1-000072/2025') == ('66229717000118', '2025', 72)
    assert fr._seq_de_nc('lixo') is None
    assert fr._seq_de_nc('') is None


def test_nc_invalido_retorna_none():
    assert fr.resultado_da_compra('nao-e-um-nc') is None
    assert fr.resultado_da_compra('') is None
    assert fr.todos_resultados_da_compra('lixo') == []


def test_resultado_do_item_monkeypatch(monkeypatch):
    """Simula o /resultados do PNCP (formato real capturado ao vivo) sem bater na rede:
    escolhe o vencedor (ordemClassificacaoSrp=1) e ignora cancelado."""
    fake = [
        {'nomeRazaoSocialFornecedor': 'PERDEDOR LTDA', 'ordemClassificacaoSrp': 2,
         'sequencialResultado': 2, 'niFornecedor': '00', 'valorTotalHomologado': 999.0},
        {'nomeRazaoSocialFornecedor': 'BR MATERIAIS DE CONSTRUCAO LTDA', 'ordemClassificacaoSrp': 1,
         'sequencialResultado': 1, 'niFornecedor': '15353996000196',
         'valorTotalHomologado': 8525.0, 'valorUnitarioHomologado': 55.0,
         'quantidadeHomologada': 155.0, 'dataCancelamento': None},
        {'nomeRazaoSocialFornecedor': 'CANCELADO SA', 'ordemClassificacaoSrp': 1,
         'sequencialResultado': 0, 'dataCancelamento': '2025-01-01'},
    ]
    monkeypatch.setattr(fr, '_get_json', lambda url: fake)
    res = fr._resultado_do_item('X', '2025', 1, 1)
    assert res['nomeRazaoSocialFornecedor'] == 'BR MATERIAIS DE CONSTRUCAO LTDA'
    assert res['niFornecedor'] == '15353996000196'


def test_sem_resultado_retorna_none(monkeypatch):
    # itens com concreto mas temResultado=False -> None (nao homologado, caso comum)
    monkeypatch.setattr(fr, '_itens_da_compra',
                        lambda c, a, s: [{'descricao': 'CONCRETO USINADO FCK 25 MPA',
                                          'temResultado': False, 'numeroItem': 1}])
    assert fr.resultado_da_compra('66229717000118-1-000072/2025') is None

    # PNCP falhou (itens vazio) -> None
    monkeypatch.setattr(fr, '_itens_da_compra', lambda c, a, s: [])
    assert fr.resultado_da_compra('66229717000118-1-000072/2025') is None


if __name__ == '__main__':
    import sys
    try:
        import pytest
        sys.exit(pytest.main([__file__, '-q']))
    except ImportError:
        # fallback sem pytest: roda os testes sem monkeypatch
        test_import_sem_efeito_colateral()
        test_seq_de_nc()
        test_nc_invalido_retorna_none()
        print('OK (testes sem monkeypatch passaram; instale pytest p/ os demais)')

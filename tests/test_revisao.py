"""Regressões da revisão PM; nenhuma coleta ou conexão remota real."""
import io
import json
import tarfile
from unittest.mock import Mock

import pytest

import coleta
import db
import deploy_update
import exportar_precos
import scraper
from conftest import PaginaFalsa, crear_scrape_con_observaciones, observacion

URL = "https://souenergy.com.br/kit.html"


def test_hash_igual_entre_coletas_sem_mudanca(conn):
    obs = observacion(URL, preco=10000)
    crear_scrape_con_observaciones(conn, [obs])
    a, _ = exportar_precos.construir_precios_json(conn)
    crear_scrape_con_observaciones(conn, [obs])
    b, _ = exportar_precos.construir_precios_json(conn)
    assert a['tabelas'] != b['tabelas']
    assert a['dataset_id'] == b['dataset_id']


def test_schema_exige_quatro_tabelas_e_datas(conn):
    crear_scrape_con_observaciones(conn, [observacion(URL, preco=10000)])
    dados, _ = exportar_precos.construir_precios_json(conn)
    dados['coleta_aprovada_em'] = 'inválida'
    assert exportar_precos.validar_contrato(dados)
    dados['coleta_aprovada_em'] = '2026-09-13T00:00:00Z'
    del dados['tabelas']['tri_solplanet_620']
    assert exportar_precos.validar_contrato(dados)


def test_exportacao_respeita_motivo_de_bloqueio(conn):
    crear_scrape_con_observaciones(conn, [observacion(URL, preco=10000, motivo_nao_exportavel='revisao')])
    _, relatorio = exportar_precos.construir_precios_json(conn)
    assert relatorio['elegidos'] == 0
    assert relatorio['no_mapeados'][0]['motivo'] == 'revisao'


def test_sessao_perdida_no_ultimo_detalhe_aborta(monkeypatch):
    page = PaginaFalsa({URL: {'es_producto': True}})
    monkeypatch.setattr(page, 'is_visible', lambda _: False)
    with pytest.raises(scraper.LoginError):
        scraper.analizar_producto(page, {'url': URL, 'nome': 'Kit'})


@pytest.mark.parametrize('url', ['https://externo.example/kit', 'https://souenergy.com.br/customer/account/logout/'])
def test_navegacao_fora_do_catalogo_bloqueada(url):
    page = PaginaFalsa({})
    with pytest.raises(scraper.BloqueadoError):
        scraper.navegar_autenticado(page, url, 1000)
    assert page.visitas == []


@pytest.mark.parametrize('texto,esperado', [('Em estoque', 'disponible'), ('Indisponível', 'indisponible')])
def test_disponibilidade_em_portugues(texto, esperado):
    page = PaginaFalsa({URL: {'disponibilidad': texto}})
    page.goto(URL)
    assert scraper.extraer_disponibilidad(page) == esperado


def test_consulta_legada_nao_mistura_marcas_e_escolhe_grupo(conn):
    crear_scrape_con_observaciones(conn, [
        observacion(URL, preco=10000, potencia_kwp=7.44),
        observacion(URL+'2', preco=20000, potencia_kwp=9.92),
        observacion(URL+'3', preco=30000, marca='HUAWEI')])
    assert len(db.obtener_precios_por_potencia(conn, 7)['solplanet']) == 1
    assert db.obtener_precios_por_potencia(conn, 99)['solplanet'][0]['potencia'] == 9.92


def test_desatualizacao_semanal(conn):
    from datetime import datetime, timedelta, timezone
    assert not db.coleta_desatualizada({'finalizado_em': (datetime.now(timezone.utc)-timedelta(days=7)).isoformat()})
    assert db.coleta_desatualizada({'finalizado_em': (datetime.now(timezone.utc)-timedelta(days=9)).isoformat()})


def test_cli_sem_promover_encerra_sem_exportar(tmp_path, monkeypatch):
    obs = observacion(URL, preco=10000)
    resultado = {'observaciones': [obs], 'inventario': [{'url': URL, 'categorias': []}],
                 'autenticado': True, 'metricas': dict(categorias_planeadas=1, categorias_visitadas=1,
                 paginas_visitadas=1, productos_descubiertos=1, detalles_validos=1,
                 indisponibles=0, fallos=0, duplicados=0, fuera_mapeo=0, fila_esgotada=True)}
    monkeypatch.setattr(coleta, 'scrapear_tudo', lambda **_: resultado)
    exportar = Mock(side_effect=AssertionError('Não deve exportar'))
    monkeypatch.setattr(exportar_precos, 'exportar_precios', exportar)
    banco = tmp_path/'teste.sqlite3'
    assert coleta.main(['--db', str(banco), '--lockfile', str(tmp_path/'lock'), '--no-promote']) == 0
    c = db.conectar(banco)
    assert c.execute('SELECT status, autenticado FROM scrapes').fetchone()[:] == ('parcial', 1)
    assert db.obtener_ultimo_scrape_aprobado(c) is None
    c.close()
    exportar.assert_not_called()


def test_cli_erro_de_permissao_sem_traceback(monkeypatch):
    monkeypatch.setattr(coleta, 'adquirir_lock', Mock(side_effect=PermissionError))
    assert coleta.main([]) == 1


def test_deploy_pacote_completo_sem_cache():
    pacote, manifesto = deploy_update._construir_paquete()
    with tarfile.open(fileobj=io.BytesIO(pacote), mode='r:gz') as tar:
        nomes = tar.getnames()
    assert 'coleta.py' in manifesto and 'ops/souenergy-scrape.timer' in manifesto
    assert not any('__pycache__' in n or n.endswith('.pyc') or n.startswith('.env') for n in nomes)


def test_rollback_usa_release_anterior(monkeypatch):
    ssh = Mock()
    monkeypatch.setattr(deploy_update, '_conectar', lambda: ssh)
    monkeypatch.setattr(deploy_update, '_release_actual', lambda _: '/release/atual')
    executar = Mock(return_value='/release/anterior')
    monkeypatch.setattr(deploy_update, '_ejecutar', executar)
    rollback = Mock()
    monkeypatch.setattr(deploy_update, '_rollback', rollback)
    assert deploy_update.main(['--rollback']) == 0
    rollback.assert_called_once_with(ssh, '/release/anterior')


def test_release_rejeita_injecao_shell(monkeypatch):
    conectar = Mock()
    monkeypatch.setattr(deploy_update, '_conectar', conectar)
    assert deploy_update.main(['--release', '../escape;comando']) == 1
    conectar.assert_not_called()


def test_registro_publicacao_permite_retentativa(conn):
    sid = crear_scrape_con_observaciones(conn, [observacion(URL, preco=10000)])
    a = db.registrar_publicacion(conn, scrape_id=sid, hash_conteudo='teste')
    db.actualizar_publicacion(conn, publicacion_id=a, status='erro')
    assert db.registrar_publicacion(conn, scrape_id=sid, hash_conteudo='teste') == a


def test_entradas_compartilham_deduplicacao_e_persistencia():
    from conftest import card, pagina_listado, pagina_producto
    raiz = 'https://souenergy.com.br/raiz.html'
    outra = 'https://souenergy.com.br/outra.html'
    page = PaginaFalsa({raiz: pagina_listado(raiz, cards=[card('Kit', URL, True)]),
                       outra: pagina_listado(outra, cards=[card('Kit', URL, True)]),
                       URL: pagina_producto(URL)})
    observadas = []
    resultado = scraper.recorrer_catalogo(page, {'url': raiz}, scraper.Limites(),
        entradas_adicionais=[{'url': outra}], on_observacao=observadas.append)
    assert page.visitas.count(URL) == 1
    assert len(observadas) == 1
    assert resultado['metricas']['productos_descubiertos'] == 1
    assert len(resultado['inventario'][0]['categorias']) == 2


def test_cli_completa_chama_publicador(tmp_path, monkeypatch):
    import publicar_precos
    monkeypatch.setenv('PUBLICAR_AUTOMATICAMENTE', 'true')
    obs = observacion(URL, preco=10000)
    def simular(**kwargs):
        kwargs['on_observacao'](obs)
        return {'observaciones': [obs], 'inventario': [{'url': URL, 'categorias': []}],
                'autenticado': True, 'metricas': dict(categorias_planeadas=1, categorias_visitadas=1,
                paginas_visitadas=1, productos_descubiertos=1, detalles_validos=1,
                indisponibles=0, fallos=0, duplicados=0, fuera_mapeo=0, fila_esgotada=True)}
    monkeypatch.setattr(coleta, 'scrapear_tudo', simular)
    monkeypatch.setattr(coleta, '_alertar_cambios', Mock())
    monkeypatch.setattr(exportar_precos, 'exportar_precios', lambda *a, **kw: (
        tmp_path/'precios.json', 'hash-teste', {'elegidos': 4, 'conflictos': [],
        'no_mapeados': [], 'por_tabla': {'a': 1, 'b': 1, 'c': 1, 'd': 1}}))
    publicar = Mock()
    monkeypatch.setattr(publicar_precos, 'publicar_si_cambia', publicar)
    assert coleta.main(['--db', str(tmp_path/'teste.sqlite3'), '--lockfile', str(tmp_path/'lock')]) == 0
    publicar.assert_called_once()


def test_deploy_prepara_release_antes_de_promover(monkeypatch):
    ssh = Mock()
    monkeypatch.setattr(deploy_update, '_conectar', lambda: ssh)
    monkeypatch.setattr(deploy_update, '_release_actual', lambda _: '/release/anterior')
    eventos = []
    for nome in ['_ejecutar', '_subir', '_verificar_checksums', '_preparar_release', '_desplegar', '_trocar_ponteiro']:
        monkeypatch.setattr(deploy_update, nome, lambda *a, _nome=nome, **kw: eventos.append(_nome))
    assert deploy_update.main(['--release', 'teste']) == 0
    assert eventos.index('_preparar_release') < eventos.index('_desplegar')
    assert eventos[-1] == '_trocar_ponteiro'


def test_sem_credenciais_nao_abre_browser(monkeypatch):
    monkeypatch.setattr(scraper, 'USUARIO', None)
    monkeypatch.setattr(scraper, 'SENHA', None)
    browser = Mock(side_effect=AssertionError('Não deve abrir browser'))
    monkeypatch.setattr(scraper, 'sync_playwright', browser)
    with pytest.raises(scraper.LoginError):
        scraper.scrapear_tudo()
    browser.assert_not_called()

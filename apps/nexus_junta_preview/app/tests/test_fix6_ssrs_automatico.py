"""FIX6: regresión aislada de migración mensual sin consultar sistemas corporativos.

Se ejecuta en el runtime del repositorio, con sus dependencias ya instaladas;
los conectores usan exclusivamente los simuladores previos, no SQL/SSRS real.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))

import dataset_demo as demo  # noqa: E402
import simuladores as sim  # noqa: E402
import test_preview as tp  # noqa: E402
from nexus_preview.conectores import confirmacion_ssrs, orquestador as orq  # noqa: E402
from nexus_preview.generaciones import PreviewRefresh  # noqa: E402
from nexus_preview.servicio import Config  # noqa: E402


def _prepare(root: Path) -> tuple[dict, PreviewRefresh]:
    paths, ref = tp.make_lab(root)
    ref.clock = sim.reloj("2026-11-06", "15:00:00")
    assert ref.ejecutar("legado")['estado'] == 'PASS'
    sim.config_conectores(paths['data'])  # SSRS/FG activos, CFDI inactivo (probado aparte en FIX5)
    sim.acreditar(paths['data'], 'fg_odbc')
    ref.deps = {'usuario': 'ucbaa', 'ssrs_fetch': lambda _url: sim.xml_ssrs(sim.plan_posterior()),
                'proveedor': sim.proveedor, 'fabrica_fg': sim.fabrica(sim.Conexion(sim.filas_fg_posterior()))}
    cfg_path = paths['data'] / 'preview.json'
    cfg = json.loads(cfg_path.read_text('utf-8'))
    cfg['ssrs_automatico'] = True
    cfg['approvers'] = str(root / 'aprobadores_privados.json')
    (root / 'aprobadores_privados.json').write_text(json.dumps({'approvers':['as0\\ucbaa']}), 'utf-8')
    cfg_path.write_text(json.dumps(cfg), 'utf-8')
    return paths, ref


def _december_portal(paths: dict) -> None:
    from physical_fixtures import portal_bytes  # noqa: PLC0415

    portal = paths['src'] / 'Portal_CAT_2026-12-02_0830.csv'
    portal.write_bytes(portal_bytes(rows=[
        ('PL-1', 'QZX-P-0001', 'Open', '1000', '100', '12/08/2026', '12/12/2026'),
        ('PL-2', 'QZX-P-0002', 'Open', '1500', '0', '12/09/2026', '12/13/2026'),
    ]))


def test_fix6_noviembre_conserva_frozen_y_toma_current_ssrs(tmp_path: Path) -> None:
    paths, ref = _prepare(tmp_path)
    locked = (paths['data'] / 'frozen_2026-11.json').read_bytes()
    prev = ref.store.vigente()
    ref.clock = sim.reloj('2026-11-10', '15:00:00')
    result = ref.ejecutar('fix6-sintetico')
    assert result['estado'] == 'PASS', result.get('motivos')
    doc = ref.store.vigente()
    assert doc['sources']['frozen_r']['file'] == prev['sources']['frozen_r']['file']
    assert doc['sources']['frozen_p']['file'] == prev['sources']['frozen_p']['file']
    assert doc['sources']['current_r']['origen'] == 'CONSULTA_DIRECTA'
    assert doc['sources']['current_r']['file'].startswith('backlog_ssrs:')
    assert (paths['data'] / 'frozen_2026-11.json').read_bytes() == locked
    assert not (paths['data'] / 'acreditacion_backlog_ssrs.json').exists()


def test_fix6_diciembre_requiere_confirmacion_y_primer_ssrs(tmp_path: Path) -> None:
    paths, ref = _prepare(tmp_path)
    _december_portal(paths)
    prior = ref.store.vigente()['gen_id']
    ref.deps['ssrs_fetch'] = lambda _url: sim.xml_ssrs(sim.plan_posterior(), periodo='12/2026')
    ref.clock = sim.reloj('2026-12-02', '15:00:00')
    pending = ref.ejecutar('sin-confirmar')
    assert pending['estado'] == 'ERROR'
    assert 'PENDIENTE' in ' '.join(pending['motivos'])
    assert ref.store.vigente()['gen_id'] == prior
    assert not (paths['data'] / 'frozen_2026-12.json').exists()

    first = confirmacion_ssrs.primer_corte(paths['data'], '2026-12')
    confirmacion_ssrs.emitir(paths['data'], '2026-12', first['archivo'], first['sha256'],
                             'as0\\ucbaa', 'Prueba sintética: ETL nocturno confirmado')
    sealed = ref.ejecutar('diciembre-confirmado')
    assert sealed['estado'] == 'PASS', sealed.get('motivos')
    lockpath = paths['data'] / 'frozen_2026-12.json'
    lock = json.loads(lockpath.read_text('utf-8'))
    assert lock['oe965']['file'] == 'backlog_ssrs:' + first['archivo']
    assert 'confirmacion_ssrs_sha256' in lock
    assert lock['portal']['file'] == 'Portal_CAT_2026-12-02_0830.csv'
    frozen_bytes = lockpath.read_bytes()
    new = ref.store.vigente()
    assert new['month'] == '2026-12'
    assert new['sources']['frozen_r']['origen'] == 'CONSULTA_DIRECTA'
    assert new['sources']['frozen_r']['confirmacion_ssrs'] == lock['confirmacion_ssrs_sha256']
    assert 'ERP_URGENTE' not in new['sources']['current_r']['origen']

    # Dos consultas posteriores nunca mueven ni el lock ni el universo.
    ref.clock = sim.reloj('2026-12-03', '15:00:00')
    ref.deps['ssrs_fetch'] = lambda _url: sim.xml_ssrs(demo._plan(), periodo='12/2026')
    result2 = ref.ejecutar('nuevo-ssrs')
    assert result2['estado'] == 'PASS', result2.get('motivos')
    assert lockpath.read_bytes() == frozen_bytes
    assert ref.store.vigente()['sources']['frozen_r']['file'] == lock['oe965']['file']


def test_fix6_nuevo_mes_con_ssrs_del_mes_anterior_no_congela(tmp_path: Path) -> None:
    paths, ref = _prepare(tmp_path)
    _december_portal(paths)
    ref.clock = sim.reloj('2026-12-01', '07:00:00')
    ref.deps['ssrs_fetch'] = lambda _url: sim.xml_ssrs(sim.plan_posterior(), periodo='11/2026')
    before = ref.store.vigente()['gen_id']
    status = ref.ejecutar('antes-over-night')
    assert status['estado'] == 'ERROR'
    assert ref.store.vigente()['gen_id'] == before
    assert not (paths['data'] / 'frozen_2026-12.json').exists()


def test_fix6_excepcion_erp_es_una_sola_generacion(tmp_path: Path) -> None:
    paths, ref = _prepare(tmp_path)
    ref.clock = sim.reloj('2026-11-10', '15:00:00')
    assert ref.ejecutar('ssrs-normal')['estado'] == 'PASS'
    frozen = (paths['data'] / 'frozen_2026-11.json').read_bytes()
    override = demo.corte_posterior(paths['src'], date='11/10/26', name='R_20261110.TXT')
    ref.clock = sim.reloj('2026-11-10', '16:00:00')
    result = ref.ejecutar('erp-urgente', override, 'as0\\ucbaa', 'Pedido urgente del día')
    assert result['estado'] == 'PASS', result.get('motivos')
    gen = ref.store.vigente()
    assert gen['sources']['current_r']['origen'] == 'ERP_EXCEPCIONAL'
    assert gen['sources']['current_r']['conector']['aprobador'] == 'as0\\ucbaa'
    assert gen['sources']['current_r']['conector']['motivo'] == 'Pedido urgente del día'
    assert (paths['data'] / 'frozen_2026-11.json').read_bytes() == frozen
    ref.clock = sim.reloj('2026-11-10', '17:00:00')
    assert ref.ejecutar('siguiente-ordinario')['estado'] == 'PASS'
    assert ref.store.vigente()['sources']['current_r']['origen'] == 'CONSULTA_DIRECTA'
    assert (paths['data'] / 'frozen_2026-11.json').read_bytes() == frozen


def test_fix6_erp_falso_rechazado_sin_consultar_conectores(tmp_path: Path) -> None:
    paths, ref = _prepare(tmp_path)
    flag = {'calls':0}
    def ssrs(_url):  # noqa: ANN202
        flag['calls'] += 1
        return sim.xml_ssrs(sim.plan_posterior())
    ref.deps['ssrs_fetch'] = ssrs
    fake = tmp_path / 'R_20261110.TXT'
    fake.write_text('FALSO NO ES OE965', 'utf-8')
    ref.clock = sim.reloj('2026-11-10', '15:00:00')
    status = ref.ejecutar('erp-falso', fake, 'as0\\ucbaa', 'Urgente')
    assert status['estado'] == 'ERROR'
    assert flag['calls'] == 0
    assert not list((paths['data'] / 'capturas').glob('erp_urgente/*.raw'))


def test_fix6_config_typed_y_ssrs_sin_erp_acreditacion(tmp_path: Path) -> None:
    paths, ref = _prepare(tmp_path)
    assert Config.load(paths['data'] / 'preview.json').ssrs_automatico is True
    doc = orq.capturar_todo(paths['data'], '2026-11', 'America/Mexico_City',
                            sim.reloj('2026-11-10'), ref.deps, ssrs_automatico=True)
    assert doc['backlog_ssrs']['estado'] == 'CAPTURADO'
    assert doc['backlog_ssrs']['alcance'] == 'SSRS_CONTRATO_TECNICO'
    assert not (paths['data'] / 'acreditacion_backlog_ssrs.json').exists()


def test_fix6_elige_primera_captura_confirmada_no_primer_intento(tmp_path: Path) -> None:
    """Un XML anterior al overnight no puede ser congelado por mera precedencia temporal."""
    paths, ref = _prepare(tmp_path)
    _december_portal(paths)
    ref.clock = sim.reloj('2026-12-02', '15:00:00')
    ref.deps['ssrs_fetch'] = lambda _url: sim.xml_ssrs(sim.plan_posterior(), periodo='12/2026')
    assert ref.ejecutar('antes-del-lote')['estado'] == 'ERROR'
    antiguos = orq.listar(paths['data'], 'backlog_ssrs')
    anterior = [d for d in antiguos if d['fecha_documento'].startswith('2026-12')][0]
    # Dos XML distintos del mismo mes: solo se congela el elegido y aprobado.
    ref.clock = sim.reloj('2026-12-03', '15:00:00')
    ref.deps['ssrs_fetch'] = lambda _url: sim.xml_ssrs(demo._plan(), periodo='12/2026')
    assert ref.ejecutar('captura-confirmable')['estado'] == 'ERROR'
    docs = [d for d in orq.listar(paths['data'], 'backlog_ssrs')
            if d['fecha_documento'].startswith('2026-12')]
    assert len(docs) >= 2
    confirmado = docs[-1]
    assert confirmado['archivo'] != anterior['archivo']
    confirmacion_ssrs.emitir(paths['data'], '2026-12', confirmado['archivo'], confirmado['sha256'],
                             'as0\\ucbaa', 'Confirmación sintética del lote de prueba')
    resultado = ref.ejecutar('sellar-primera-confirmada')
    assert resultado['estado'] == 'PASS', resultado.get('motivos')
    lock = json.loads((paths['data'] / 'frozen_2026-12.json').read_text('utf-8'))
    assert lock['oe965']['file'] == 'backlog_ssrs:' + confirmado['archivo']
    assert lock['oe965']['file'] != 'backlog_ssrs:' + anterior['archivo']


def test_fix6_impide_importacion_interactiva_como_ssrs_automatico(tmp_path: Path) -> None:
    paths, ref = _prepare(tmp_path)
    cfg_path = paths['data'] / 'conectores.json'
    config = json.loads(cfg_path.read_text('utf-8'))
    config['backlog_ssrs']['modo'] = 'importacion_interactiva'
    cfg_path.write_text(json.dumps(config), 'utf-8')
    result = orq.capturar_todo(paths['data'], '2026-11', 'America/Mexico_City',
                               sim.reloj('2026-11-10'), ref.deps, ssrs_automatico=True)
    assert result['backlog_ssrs']['estado'] == 'FALLO'
    assert result['backlog_ssrs']['codigo'] == 'SSRS_MODO_NO_AUTOMATICO'

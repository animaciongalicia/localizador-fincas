"""Inventario Forestal Continuo de Galicia: parseo y contraste con el cultivo catastral (cero red)."""
from pathlib import Path

from openpyxl import load_workbook

from fincas.cache import Cache
from fincas.entrada import leer_fincas
from fincas.inventario import (ServicioInventario, aviso_alteracion, contrastar, esperadas, evolucion,
                               interpretar, texto_reparto)
from fincas.pipeline import Opciones, Servicios, ejecutar
from fincas.registro import Registro
from test_pipeline import Catastro, Resolutor, Sigpac

EJEMPLO = Path(__file__).parent / "ejemplo.csv"
E22, E23, E24 = 1640991600000, 1672527600000, 1704063600000      # 1 de enero de 2022, 2023 y 2024 (hora peninsular)


def _rec(fecini, sutil, **s):
    return dict({"ADM_FECINI": fecini, "ADM_ESTADO": 1, "SUTIL": sutil, "USO": "FORESTAL ARBOLADO",
                 "REFCAT": "15062A03400176"}, **s)


# respuesta real del servicio para Ortigueira 34/176 (2026-11-12), reducida a las columnas con dato
REAL_176 = [_rec(E22, 1723.81, S01=853.11, S02=669.17, S03=201.53),
            _rec(E23, 1723.81, S01=1161.09, S02=361.2, S03=201.53),
            _rec(E24, 1723.81, S01=1326.12, S02=196.17, S03=201.53)]


def test_interpreta_la_edicion_mas_reciente_y_guarda_el_historico():
    d = interpretar(REAL_176)
    assert d.anio == 2024 and d.dominante == "Eucaliptos" and d.recintos == 1
    assert d.coberturas == {"Eucaliptos": 1326.1, "Coníferas": 196.2, "Frondosas": 201.5}
    assert sorted(d.historico) == ["2022", "2023", "2024"] and d.volumen_m3 is None
    assert texto_reparto(d).startswith("Eucaliptos 77 % (1.326 m²)")
    assert evolucion(d) == "Eucaliptos: 853 m² en 2022 → 1.326 m² en 2024"


def test_suma_recintos_y_columnas_de_detalle():
    d = interpretar([_rec(E24, 1000.0, S01=600.0, S51=400.0), _rec(E24, 500.0, S03=500.0, Vm3=12.5)])
    assert d.recintos == 2 and d.superficie_m2 == 1500.0 and d.volumen_m3 == 12.5
    assert d.coberturas == {"Eucaliptos": 600.0, "Regeneración": 400.0, "Frondosas": 500.0}


def test_no_cuenta_dos_veces_si_el_detalle_ya_esta_en_la_agregada():
    d = interpretar([_rec(E24, 1000.0, S01=400.0, S04=600.0, S41=600.0)])
    assert d.coberturas == {"Eucaliptos": 400.0, "Cortas": 600.0}


def test_sin_registros_o_sin_cobertura_es_none():
    assert interpretar([]) is None
    assert interpretar([_rec(E24, 0.0)]) is None


def test_equivalencias_con_el_cultivo_catastral():
    assert esperadas("PINO NEGRAL MADERABLE (PINUS PINASTER)") == ["Coníferas"]
    assert esperadas("EUCALIPTUS; MATORRAL; ARBOLES DE RIBERA") == ["Eucaliptos", "Matorral", "Frondosas"]
    assert esperadas("LABOR O LABRADÍO SECANO; PRADOS O PRADERAS") == ["Prado o cultivo"]
    assert esperadas("VIÑA SECANO") == ["Prado o cultivo"] and esperadas("COSA RARA") == []


def test_contraste():
    d = interpretar(REAL_176)
    assert contrastar("EUCALIPTUS", d) == ("COHERENTE", "")
    estado, det = contrastar("PINAR MADERABLE", d)                # el Catastro dice pinar y ya es eucaliptal
    assert estado == "REVISAR" and "solo el 11 %" in det and "domina eucaliptos" in det
    assert contrastar("COSA RARA", d)[0] == "NO COMPARABLE"
    assert contrastar("EUCALIPTUS", None) == ("SIN DATO", "")


def test_corta_o_incendio_se_avisan_aunque_quede_arbolado():
    d = interpretar([_rec(E24, 1000.0, S01=700.0, S41=300.0)])
    estado, det = contrastar("EUCALIPTUS", d)
    assert estado == "REVISAR" and "cortas 30 %" in det
    assert aviso_alteracion(d) == "Inventario forestal 2024: cortas 30 % de la parcela"
    assert aviso_alteracion(interpretar(REAL_176)) == ""
    assert contrastar("PRADOS O PRADERAS", interpretar([_rec(E24, 100.0, S06=100.0)]))[0] == "COHERENTE"


class _Http:
    def __init__(self, cuerpo):
        self.cuerpo, self.llamadas = cuerpo, []

    def get(self, url, params=None):
        self.llamadas.append((url, params))
        cuerpo = self.cuerpo

        class R:
            def json(self):
                return cuerpo
        return R()


def test_servicio_pregunta_por_los_14_caracteres_y_detecta_errores():
    http = _Http({"features": [{"attributes": a} for a in REAL_176]})
    d = ServicioInventario(http, base="https://x/rest/").consultar("15062a03400176000ab")
    assert d.anio == 2024
    url, params = http.llamadas[0]
    assert url == "https://x/rest/INVEGA/InvegaRecintos/MapServer/0/query" and params["where"] == "REFCAT='15062A03400176'"
    assert ServicioInventario(_Http({"features": []})).consultar("15062A03400176") is None
    for malo, exc in (("15062A0340'; --", ValueError), ("15062A03400176", RuntimeError)):
        try:
            ServicioInventario(_Http({"error": {"code": 400}})).consultar(malo)
            assert False
        except exc:
            pass


class Inventario:
    def __init__(self):
        self.llamadas = []

    def consultar(self, refcat):
        self.llamadas.append(refcat)
        if refcat.endswith("00007"):
            return None                                   # no figura en el inventario
        if refcat.endswith("00009"):
            raise RuntimeError("servicio caído")
        return interpretar([_rec(E24, 2500.0, S01=2000.0, S06=500.0)])


def test_pipeline_anota_cobertura_y_contraste_y_cachea(tmp_path):
    fincas, _ = leer_fincas(str(EJEMPLO))
    inv = Inventario()
    srv = Servicios(Resolutor(), Catastro(), Sigpac(), Cache(tmp_path / ".cache"), inventario=inv)
    reg = Registro(eco=None)
    raiz = ejecutar(fincas, Opciones("lote", salidas=tmp_path, fecha="2026-01-01"), srv, reg)   # Catastro de test: 'Pastos'
    wb = load_workbook(raiz / "resultados.xlsx")
    ws = wb["Fincas"]
    cab = [c.value for c in ws[1]]
    filas = list(ws.iter_rows(min_row=2, values_only=True))
    f0 = dict(zip(cab, filas[0]))
    assert f0["cobertura actual (inventario forestal)"] == "Eucaliptos" and f0["edición inventario"] == 2024
    assert f0["contraste con cultivo catastral"] == "REVISAR" and "Eucaliptos 80 %" in f0["detalle cobertura"]
    assert filas[0][9] == "OK" and filas[0][12] == "SI"                      # las columnas de siempre no se mueven
    assert dict(zip(cab, filas[2]))["contraste con cultivo catastral"] == "SIN DATO"
    assert not dict(zip(cab, filas[3]))["contraste con cultivo catastral"]   # sin Catastro no se consulta
    assert len(filas) == 5
    textos = [c.value for fila in wb["Resumen"].iter_rows() for c in fila if c.value]
    assert "Cobertura actual según el Inventario Forestal de Galicia" in textos
    n = len(inv.llamadas)
    ejecutar(fincas, Opciones("lote", salidas=tmp_path, fecha="2026-01-02"), srv, Registro(eco=None))
    assert len(inv.llamadas) - n <= 2          # solo se repiten la que no figura y la que falló


def test_fuera_de_galicia_no_se_consulta(tmp_path):
    from fincas.modelos import Codigos

    class Madrid(Resolutor):
        def resolver(self, prov, mun, preguntar=None):
            return Codigos("28", "MADRID", "060", "60", mun.upper())

    fincas, _ = leer_fincas(str(EJEMPLO))
    inv = Inventario()
    srv = Servicios(Madrid(), Catastro(), None, Cache(tmp_path / ".cache"), inventario=inv)
    ejecutar(fincas, Opciones("lote", salidas=tmp_path, fecha="2026-01-01", prueba=2), srv, Registro(eco=None))
    assert inv.llamadas == []


def test_monte_vecinal_se_lee_de_la_ultima_edicion():
    base = {"ADM_FECINI": 1704063600000, "SUTIL": 100.0, "S01": 100.0}
    assert interpretar([dict(base, MVMC_MP=1)]).monte_vecinal is True
    assert interpretar([dict(base, MVMC_MP=0)]).monte_vecinal is False
    assert interpretar([base]).monte_vecinal is None          # entradas antiguas o sin el campo: no consta


def test_informe_destaca_las_diferencias_con_el_catastro(tmp_path):
    import io
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas
    from fincas.informe import escribir_informe
    from fincas.modelos import Codigos, DatosCatastro, DatosInventario, Finca, Resultado

    rs = []
    for i, (uso, cob, mv) in enumerate([("PINAR MADERABLE", {"Eucaliptos": 900.0, "Coníferas": 100.0}, True),
                                        ("EUCALIPTUS", {"Eucaliptos": 1000.0}, False)], 1):
        r = Resultado(finca=Finca("Ortigueira", "A Coruña", "34", str(i)),
                      codigos=Codigos("15", "X", "061", "62", "ORTIGUEIRA"),
                      catastro=DatosCatastro(refcat=f"15062A034{i:05d}", superficie_m2=1000.0, uso=uso))
        r.inventario = DatosInventario(anio=2024, superficie_m2=1000.0, coberturas=cob,
                                       dominante=max(cob, key=cob.get), monte_vecinal=mv)
        r.contraste, r.contraste_detalle = contrastar(uso, r.inventario)
        r.pdf_catastro = f"{i}_c.pdf"
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(100, 700, "x")
        c.save()
        (tmp_path / "pdf_catastro").mkdir(exist_ok=True)
        (tmp_path / "pdf_catastro" / r.pdf_catastro).write_bytes(buf.getvalue())
        rs.append(r)
    texto = " ".join((p.extract_text() or "") for p in PdfReader(str(escribir_informe(rs, tmp_path, "T"))).pages)
    texto = " ".join(texto.split())
    assert "Cotejo con el Inventario Forestal Continuo de Galicia" in texto
    assert "De 2 fincas cotejadas, 1 presentan diferencias" in texto
    assert "Fincas sin diferencias apreciables: nº 2" in texto
    assert "Figuran como monte vecinal o público en el inventario: nº 1" in texto

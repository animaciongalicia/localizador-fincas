"""Mapa de conjunto: geometría pura y servicio con red/navegador simulados (cero red)."""
import io
from pathlib import Path

from openpyxl import load_workbook
from pypdf import PdfReader

from fincas import mapa
from fincas.cache import Cache
from fincas.entrada import leer_fincas
from fincas.informe import escribir_informe
from fincas.mapa import (Punto, ServicioMapa, agrupar, formato_dist, haversine_m, html_pagina,
                         matriz_distancias, svg_mapa, vecina_mas_cercana, vista_para)
from fincas.modelos import Codigos, DatosCatastro, Finca, Resultado
from fincas.pipeline import Opciones, Servicios, ejecutar
from fincas.registro import Registro
from test_pipeline import Catastro, Resolutor, Sigpac

EJEMPLO = Path(__file__).parent / "ejemplo.csv"


def _p(n, lon, lat, sup=1000.0, d=0.0004):
    return Punto(n, f"Finca {n}", f"15062A0340{n:04d}", sup, lon, lat, (lon - d, lat - d, lon + d, lat + d))


def _pdf_real(html: str, destino: Path) -> bool:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(80, 700, "mapa")
    c.save()
    destino.write_bytes(buf.getvalue())
    return True


def test_haversine_un_grado_de_latitud():
    assert abs(haversine_m(0, 0, 0, 1) - 111_195) < 300
    assert haversine_m(-8.0, 43.5, -8.0, 43.5) == 0


def test_agrupar_por_cercania_y_aisladas():
    a, b = _p(1, -7.800, 43.600), _p(2, -7.805, 43.600)       # ~400 m entre ellas
    c = _p(3, -7.300, 43.100)                                  # a decenas de km
    grupos = agrupar([a, b, c])
    assert [sorted(p.n for p in g) for g in grupos] == [[1, 2], [3]]
    q, d = vecina_mas_cercana(a, grupos[0])
    assert q is b and 300 < d < 500
    assert vecina_mas_cercana(c, grupos[1]) is None


def test_agrupar_encadena_fincas():
    cadena = [_p(i, -7.80 + i * 0.02, 43.6) for i in range(1, 5)]   # ~1,6 km entre vecinas
    assert len(agrupar(cadena)) == 1


def test_matriz_simetrica_y_formato():
    pts = [_p(1, -7.8, 43.6), _p(2, -7.79, 43.6), _p(3, -7.8, 43.61)]
    m = matriz_distancias(pts)
    assert m[0][0] == 0 and m[0][1] == m[1][0] and m[0][2] == m[2][0]
    assert formato_dist(320) == "320 m" and formato_dist(2500) == "2,5 km"


def test_svg_numera_fincas_y_detecta_fuera():
    g = [_p(1, -7.800, 43.600), _p(2, -7.805, 43.601)]
    v = vista_para(g)
    teselas = {(c, f): "data:x" for c, f, _, _ in v.teselas_ortofoto()}
    svg, fuera = svg_mapa(v, teselas, g)
    assert fuera == [] and svg.count("<circle") >= 3          # dos marcadores + norte
    assert ">1</text>" in svg and ">2</text>" in svg
    lejos = _p(9, -7.0, 43.0)
    _, fuera = svg_mapa(v, teselas, g + [lejos])
    assert fuera == [lejos]


def test_html_escapa_y_lista_vecinas():
    g = [_p(1, -7.800, 43.600), _p(2, -7.805, 43.600)]
    g[0].etiqueta = "Finca <1> & co"
    h = html_pagina("T", "sub", "<svg/>", g, [])
    assert "Finca &lt;1&gt; &amp; co" in h and "nº 2" in h


def _resultados(n_galicia=3, prov="15"):
    out = []
    for i in range(1, n_galicia + 1):
        f = Finca("Ortigueira", "A Coruña", "34", str(170 + i))
        out.append(Resultado(finca=f, codigos=Codigos(prov, "X", "061", "62", "ORTIGUEIRA"),
                             catastro=DatosCatastro(refcat=f"15062A034{170 + i:05d}", superficie_m2=500.0 * i)))
    return out


def _servicio(tmp_path, llamadas):
    def bbox(prov, mun, pol, par):
        llamadas.append(par)
        lon = -7.80 - (int(par) - 170) * 0.003          # ~250 m entre fincas
        return lon - 0.0003, 43.60 - 0.0003, lon + 0.0003, 43.60 + 0.0003

    return ServicioMapa(None, Cache(tmp_path / "c"), pausa_s=0, bbox_fn=bbox,
                        teselas_fn=lambda base, v: {(c, f): "data:x" for c, f, _, _ in v.teselas_ortofoto()},
                        pdf_fn=_pdf_real)


def test_servicio_genera_pdf_y_cachea_cajas(tmp_path):
    llamadas = []
    srv = _servicio(tmp_path, llamadas)
    conj = srv.generar(_resultados(), tmp_path, "Lote")
    assert conj.pdf == tmp_path / "mapa_conjunto.pdf" and conj.pdf.exists()
    assert len(conj.puntos) == 3 and len(conj.grupos) == 1 and conj.aisladas == []
    assert sorted(llamadas) == ["171", "172", "173"]
    srv.generar(_resultados(), tmp_path, "Lote")               # segunda vez: todo sale de la caché
    assert len(llamadas) == 3


def test_servicio_ignora_fincas_fuera_de_galicia(tmp_path):
    llamadas = []
    conj = _servicio(tmp_path, llamadas).generar(_resultados(prov="28"), tmp_path, "Lote")
    assert conj is None and llamadas == []


def test_servicio_aisladas_y_caja_que_falla(tmp_path):
    rs = _resultados(4)

    def bbox(prov, mun, pol, par):
        if par == "174":
            raise RuntimeError("servicio caído")
        lon = -7.80 if par != "173" else -7.0                  # la 173 está lejísimos
        return lon - 0.0003, 43.6 - 0.0003, lon + 0.0003, 43.6 + 0.0003 + (int(par) - 171) * 0.002

    avisos = []
    srv = ServicioMapa(None, Cache(tmp_path / "c"), pausa_s=0, bbox_fn=bbox, log=avisos.append,
                       teselas_fn=lambda base, v: {(c, f): "x" for c, f, _, _ in v.teselas_ortofoto()},
                       pdf_fn=_pdf_real)
    conj = srv.generar(rs, tmp_path, "Lote")
    assert [p.n for p in conj.aisladas] == [3]
    assert any("sin posición" in a for a in avisos)            # la 174 se registra y se sigue
    assert len(conj.puntos) == 3


def test_pipeline_integra_mapa_excel_e_informe(tmp_path):
    fincas, _ = leer_fincas(str(EJEMPLO))
    llamadas = []
    srv = Servicios(Resolutor(), Catastro(), Sigpac(), Cache(tmp_path / ".cache"),
                    mapa=_servicio(tmp_path, llamadas))
    # el Resolutor de test devuelve provincia 36 (Pontevedra): Galicia
    raiz = ejecutar(fincas, Opciones("lote", salidas=tmp_path, fecha="2026-01-01"), srv, Registro(eco=None))
    wb = load_workbook(raiz / "resultados.xlsx")
    assert wb.sheetnames == ["Resumen", "Fincas", "Distancias"]
    assert (raiz / "mapa_conjunto.pdf").exists()


def test_informe_con_extras_ajusta_indice_y_marcadores(tmp_path):
    from reportlab.pdfgen import canvas

    def pdf(ruta, paginas):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        for i in range(paginas):
            c.drawString(100, 700, f"{ruta.name} {i}")
            c.showPage()
        c.save()
        ruta.write_bytes(buf.getvalue())

    rs = _resultados(2)
    for r in rs:
        r.pdf_catastro = f"{r.finca.parcela}_c.pdf"
        pdf(tmp_path / "pdf_catastro" / r.pdf_catastro, 1)
    pdf(tmp_path / "mapa.pdf", 2)
    ruta = escribir_informe(rs, tmp_path, "T", extras=[("Mapa de conjunto", tmp_path / "mapa.pdf")])
    lector = PdfReader(str(ruta))
    titulos = [o.title for o in lector.outline]
    assert titulos[:2] == ["Resumen e índice", "Mapa de conjunto"]
    pos_mapa = lector.get_destination_page_number(lector.outline[1])
    pos_f1 = lector.get_destination_page_number(lector.outline[2])
    assert pos_f1 == pos_mapa + 2 and len(lector.pages) == pos_f1 + 2 * 2   # cada finca: su hoja + 1 ficha

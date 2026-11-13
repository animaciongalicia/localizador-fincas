"""Resumen, informe.pdf y hoja Resumen del Excel. Sin red: los PDF se fabrican aquí."""
import io
from pathlib import Path

from openpyxl import load_workbook
from pypdf import PdfReader

from fincas.excel import escribir_excel
from fincas.informe import escribir_informe
from fincas.modelos import Codigos, Cuota, DatosCatastro, Finca, Resultado, ResultadoSigpac
from fincas.resumen import calcular, formato_ha, formato_m2
from fincas.superficie import explicar_control


def _pdf(ruta: Path, paginas: int = 1) -> None:
    from reportlab.pdfgen import canvas
    ruta.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(paginas):
        c.drawString(100, 700, f"{ruta.name} p{i + 1}")
        c.showPage()
    c.save()
    ruta.write_bytes(buf.getvalue())


def _res(mun="Ortigueira", pol="34", par="175", sup=518.0, uso="EUCALIPTUS", cuota=None,
         control="OK", cliente=518.0) -> Resultado:
    f = Finca(mun, "A Coruña", pol, par, superficie_m2=cliente, cuota=cuota or Cuota())
    return Resultado(
        finca=f, codigos=Codigos("15", "A CORUÑA", "061", "62", mun.upper()),
        catastro=DatosCatastro(refcat=f"15062A{pol.zfill(3)}{par.zfill(5)}", superficie_m2=sup, uso=uso,
                               referencias_completas=[f"15062A{pol.zfill(3)}{par.zfill(5)}0000TF"]),
        control_superficie=control, coincide="SI")


def test_resumen_cifras():
    a = _res(sup=1000.0, uso="PASTOS")
    b = _res(par="176", sup=3000.0, uso="EUCALIPTUS", control="REVISAR", cuota=Cuota("1/2", 1, 2, False))
    c = _res(mun="Cedeira", par="9", sup=500.0, uso="PASTOS")
    c.errores.append("algo")
    rs = calcular([a, b, c])
    assert rs.total == 3 and rs.superficie_m2 == 4500.0
    assert rs.por_municipio[0] == ("Ortigueira", 2, 4000.0)        # ordenado por superficie
    assert dict((n, k) for n, k, _ in rs.por_uso) == {"PASTOS": 2, "EUCALIPTUS": 1}
    assert rs.control == {"OK": 2, "REVISAR": 1}
    assert rs.cuotas_parciales == 1 and rs.con_errores == 1


def test_formatos():
    assert formato_m2(12345.6) == "12.346 m²"
    assert formato_ha(20601) == "2,0601 ha"


def test_explicar_control():
    q = Cuota()
    assert explicar_control(518, 518, q, "OK") == ""
    t = explicar_control(799, 159, q, "REVISAR")
    assert "799 m²" in t and "159 m²" in t and "+403 %" in t
    parcial = explicar_control(1000, 100, Cuota("1/5", 1, 5, False), "REVISAR")
    assert "500 m²" in parcial and "1/5" in parcial
    assert "no indicó" in explicar_control(500, None, q, "SIN DATO CLIENTE")
    assert "Sin superficie" in explicar_control(None, 10, q, "REVISAR")


def test_informe_une_pdfs_con_indice_y_marcadores(tmp_path):
    rs = [_res(), _res(par="176"), _res(par="999")]
    for r in rs[:2]:
        r.pdf_catastro = f"{r.finca.nombre_base}_catastro.pdf"
        _pdf(tmp_path / "pdf_catastro" / r.pdf_catastro, 1)
        r.sigpac = ResultadoSigpac("ENCONTRADA", refcat=r.catastro.refcat,
                                   pdf=f"{r.finca.nombre_base}_sigpac.pdf")
        _pdf(tmp_path / "pdf_sigpac" / r.sigpac.pdf, 2)
    # la tercera finca no tiene ninguna ficha: lleva igualmente su hoja (con la incidencia), sin documentos
    from fincas.modelos import Meta
    ruta = escribir_informe(rs, tmp_path, "Lote de prueba", fecha="01/01/2026",
                            meta=Meta(cliente="Cliente Uno", expediente="FER-2026-0007"))
    assert ruta == tmp_path / "informe.pdf" and ruta.exists()
    lector = PdfReader(str(ruta))
    por_finca = [1 + 1 + 2, 1 + 1 + 2, 1]                      # hoja propia + Catastro + SIXPAC
    titulos = [o.title for o in lector.outline]
    assert titulos == ["Portada y contenido", "Resumen ejecutivo", "Resumen para la compraventa",
                       "Precio de referencia de la zona",
                       "1. Ortigueira pol 34 parc 175", "2. Ortigueira pol 34 parc 176", "3. Ortigueira pol 34 parc 999",
                       "Anexo. Fuentes y fechas de consulta", "Anexo. Metodología y aviso legal"]
    delanteras = lector.get_destination_page_number(lector.outline[4])   # todo lo que va antes de la primera finca
    assert delanteras >= 5                    # portada, contenido, resumen ejecutivo, compraventa y precios
    assert lector.get_destination_page_number(lector.outline[5]) == delanteras + 4
    assert lector.get_destination_page_number(lector.outline[7]) == delanteras + sum(por_finca)
    textos = [pag.extract_text() for pag in lector.pages]
    frente = "\n".join(textos[:2])
    assert "ferrados.com" in frente and "Cliente Uno" in frente and "FER-2026-0007" in frente
    assert "Contenido" in textos[1] and "Índice de fincas" in textos[1]
    # el contenido cita la página real de cada apartado y de cada finca
    plano = " ".join(textos[1].split())
    assert f"Resumen ejecutivo {lector.get_destination_page_number(lector.outline[1]) + 1}" in plano
    assert f"Fincas, una a una {delanteras + 1}" in plano
    assert "Aviso legal" in textos[-1] and "no es una certificación" in textos[-1].lower().replace("\n", " ")
    # todas las páginas, también las fichas externas, llevan marca, expediente, pie y numeración
    total = len(lector.pages)
    for i, pag in enumerate(lector.pages, start=1):
        t = pag.extract_text()
        assert "ferrados.com" in t and "FER-2026-0007" in t, f"sin cabecera en la página {i}"
        assert "Inversiones SHISO SL" in t and f"Página {i} de {total}" in t, f"sin pie en la página {i}"
    hoja = lector.pages[delanteras].extract_text()
    assert "Trazabilidad" not in hoja and "15062A03400175" in hoja         # las fuentes van en un anexo final
    assert any("No se obtuvo ningún documento" in x for x in textos)
    assert any("Fuentes y fechas de consulta" in x and "Sede del Catastro" in " ".join(x.split()) for x in textos)


def test_hoja_de_finca_muestra_fecha_de_consulta_e_incidencias(tmp_path):
    r = _res(sup=799.0, cliente=159.0, control="REVISAR")
    r.catastro.consultado = "2026-11-12T09:01"
    r.avisos.append("aviso de prueba")
    r.pdf_catastro = "a_catastro.pdf"
    _pdf(tmp_path / "pdf_catastro" / r.pdf_catastro, 1)
    r.sigpac = ResultadoSigpac("ENCONTRADA", refcat=r.catastro.refcat, pdf="", consultado="2026-11-10T18:30")
    lector = PdfReader(str(escribir_informe([r], tmp_path, "L")))
    hoja = next(p.extract_text() for p in lector.pages if "Finca 1 de 1" in p.extract_text())
    assert "aviso de prueba" in hoja and "+403 %" in hoja and "SIXPAC" in hoja
    fuentes = next(p.extract_text() for p in lector.pages if "Fuentes y fechas de consulta" in p.extract_text()
                   and "Fuentes" in p.extract_text() and "Finca 1 de 1" not in p.extract_text()
                   and "Índice de fincas" not in p.extract_text())
    assert "12/11/2026 09:01" in fuentes and "10/11/2026 18:30" in fuentes     # fecha real de cada consulta


def test_informe_omite_pdf_corrupto_y_sin_pdf(tmp_path):
    r = _res()
    r.pdf_catastro = "x_catastro.pdf"
    (tmp_path / "pdf_catastro").mkdir()
    (tmp_path / "pdf_catastro" / r.pdf_catastro).write_bytes(b"%PDF-1.4 fake")
    avisos = []
    assert escribir_informe([r], tmp_path, "t", log=avisos.append) is None   # nada válido que unir
    assert not (tmp_path / "informe.pdf").exists()
    assert any("se omite" in a for a in avisos)


def test_excel_hoja_resumen_y_enlaces(tmp_path):
    r = _res(sup=799.0, cliente=159.0, control="REVISAR")
    r.pdf_catastro = "o_catastro.pdf"
    r.sigpac = ResultadoSigpac("ENCONTRADA", refcat=r.catastro.refcat, pdf="o_sigpac.pdf")
    ruta = escribir_excel([r, _res(par="2")], tmp_path / "r.xlsx", "Cliente X")
    wb = load_workbook(ruta)
    assert wb.sheetnames == ["Resumen", "Fincas"]
    textos = [c for fila in wb["Resumen"].iter_rows(values_only=True) for c in fila if c]
    assert "Cliente X" in textos and "REVISAR" in textos and "Por municipio" in textos
    ws = wb["Fincas"]
    cab = [c.value for c in ws[1]]
    detalle = ws.cell(row=2, column=cab.index("detalle del control") + 1).value
    assert "+403 %" in detalle
    assert ws.cell(row=2, column=cab.index("PDF Catastro") + 1).hyperlink.target == "pdf_catastro/o_catastro.pdf"
    sede = ws.cell(row=2, column=cab.index("ficha web Catastro") + 1).hyperlink.target
    assert "RefC=15062A03400175" in sede and "del=15" in sede and "mun=62" in sede


def test_anexos_al_final_y_hoja_de_finca_sin_geologia(tmp_path):
    """La geología y las construcciones van en anexos al final; la página de afecciones resume el lote."""
    from fincas.modelos import DatosAprovechamiento, DatosGeologia
    a = _res()
    a.pdf_catastro = "a_catastro.pdf"
    _pdf(tmp_path / "pdf_catastro" / "a_catastro.pdf")
    a.aprovechamiento = DatosAprovechamiento(afecciones=[
        {"tipo": "Patrimonio cultural", "detalle": "Castro de ejemplo", "organismo": "Patrimonio Cultural"},
        {"tipo": "Registro forestal", "detalle": "Monte veciñal en man común: Monte de Ejemplo",
         "organismo": "Consellería do Medio Rural"}])
    a.geologia = DatosGeologia(hoja="1 Hoja", litologia="Granito de ejemplo", derechos_consultados=True,
                               indicios=[{"nombre": "Mina De Ejemplo", "sustancia": "Estaño", "morfologia": "Filoniana",
                                          "tamano": "desconocido", "codigo": "1", "distancia_m": 1500}],
                               veredicto="Indicios en la comarca: 1 indicio(s) minero(s) a menos de 5 km; "
                                         "el más cercano, Mina De Ejemplo (estaño), a 1,5 km")
    b = _res(par="176")
    b.catastro.clase, b.catastro.construida_m2, b.catastro.anio = "Urbano", 120.0, "1950"
    b.catastro.construcciones = ["VIVIENDA 120 m²"]
    c = _res(par="177")
    c.catastro.otros_inmuebles = ["15062A034001770001XX"]
    destino = escribir_informe([a, b, c], tmp_path, "lote", fecha="01/01/2026")
    textos = [" ".join((p.extract_text() or "").split()) for p in PdfReader(str(destino)).pages]
    hoja = next(t for t in textos if "Finca 1 de 3" in t)
    assert "Mina De Ejemplo" not in hoja and "Granito de ejemplo" not in hoja      # la hoja vuelve a ser la de antes
    afe = next(t for t in textos if "Afecciones y limitaciones detectadas" in t and "Castro de ejemplo" in t)
    assert "Monte de Ejemplo" in afe and "Castro de ejemplo" in afe and "nº 1" in afe
    geo = next(i for i, t in enumerate(textos) if "Informe geológico y minero" in t and "Mina De Ejemplo" in t)
    cons = next(i for i, t in enumerate(textos) if "Construcciones e inmuebles urbanos" in t and "VIVIENDA" in t)
    ultima_finca = max(i for i, t in enumerate(textos) if "Finca 3 de 3" in t)
    assert ultima_finca < cons < geo                                              # anexos después de las fincas
    assert "15062A034001770001XX" in textos[cons]                                 # construcción en parcela rústica
    indice = next(t for t in textos if "Índice de fincas" in t)
    assert f"Anexo. Informe geológico y minero {geo + 1}" in indice           # el contenido cita su página real
    afe_i = next(i for i, t in enumerate(textos) if "Afecciones y limitaciones detectadas" in t and "Castro" in t)
    assert afe_i < min(i for i, t in enumerate(textos) if "Finca 1 de 3" in t)   # los resúmenes van antes de las fincas


def test_sin_geologia_ni_construcciones_no_hay_anexos(tmp_path):
    a = _res()
    a.pdf_catastro = "a_catastro.pdf"
    _pdf(tmp_path / "pdf_catastro" / "a_catastro.pdf")
    textos = " ".join(p.extract_text() or "" for p in PdfReader(str(escribir_informe([a], tmp_path, "lote"))).pages)
    assert "Informe geológico y minero" not in textos and "Construcciones e inmuebles" not in textos
    assert "Afecciones y limitaciones" not in textos and "Fuentes y fechas de consulta" in textos


def test_puntos_a_comprobar_solo_hechos():
    from fincas.informe import puntos_a_comprobar
    from fincas.modelos import DatosAprovechamiento, DatosGeologia
    assert puntos_a_comprobar(_res()) == []                                   # finca sin nada destacable
    r = _res(sup=799.0, cliente=159.0, control="REVISAR", cuota=Cuota("1/2", 1, 2, False))
    r.contraste, r.contraste_detalle = "REVISAR", "El Catastro declara MATORRAL; domina eucaliptos"
    r.aprovechamiento = DatosAprovechamiento(
        afecciones=[{"tipo": "Patrimonio cultural", "detalle": "Castro de ejemplo", "organismo": "Patrimonio"},
                    {"tipo": "Patrimonio cultural", "detalle": "Contorno", "organismo": "Patrimonio"}],
        saca={"Desfavorable": 0.6, "Favorable": 0.4})
    r.geologia = DatosGeologia(derechos_parcela=[{"numero": "XX/1", "nombre": "Manantial", "tipo": "Autorización",
                                                  "recursos": "Agua"}])
    p = puntos_a_comprobar(r)
    assert any("cuota (1/2)" in x for x in p) and any("superficie no cuadra" in x for x in p)
    assert any("domina eucaliptos" in x for x in p) and any("XX/1 Manantial" in x for x in p)
    assert sum("Patrimonio cultural" in x for x in p) == 1                      # una línea por tipo, con sus detalles
    assert any("Castro de ejemplo; Contorno" in x for x in p) and any("60 %" in x for x in p)
    sin = Resultado(finca=Finca("X", "Y", "", "", sin_datos="SIN DATOS"), errores=["SIN DATOS: falta referencia"])
    assert "falta referencia" in puntos_a_comprobar(sin)[0]

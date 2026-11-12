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
    delanteras = len(lector.pages) - sum(por_finca)
    assert delanteras >= 3                                          # portada + índice + metodología
    titulos = [o.title for o in lector.outline]
    assert titulos == ["Resumen e índice", "1. Ortigueira pol 34 parc 175", "2. Ortigueira pol 34 parc 176",
                       "3. Ortigueira pol 34 parc 999"]
    assert lector.get_destination_page_number(lector.outline[1]) == delanteras
    assert lector.get_destination_page_number(lector.outline[2]) == delanteras + 4
    frente = "\n".join(lector.pages[i].extract_text() for i in range(delanteras))
    assert "ferrados.com" in frente and "Cliente Uno" in frente and "FER-2026-0007" in frente
    assert "Aviso legal" in frente and "no es una certificación" in frente.lower().replace("\n", " ")
    # todas las páginas, también las fichas externas, llevan marca, expediente, pie y numeración
    total = len(lector.pages)
    for i, pag in enumerate(lector.pages, start=1):
        t = pag.extract_text()
        assert "ferrados.com" in t and "FER-2026-0007" in t, f"sin cabecera en la página {i}"
        assert "Inversiones SHISO SL" in t and f"Página {i} de {total}" in t, f"sin pie en la página {i}"
    hoja = lector.pages[delanteras].extract_text()
    assert "Trazabilidad de las consultas" in hoja and "15062A03400175" in hoja
    assert "No se obtuvo ningún documento" in lector.pages[total - 1].extract_text()


def test_hoja_de_finca_muestra_fecha_de_consulta_e_incidencias(tmp_path):
    r = _res(sup=799.0, cliente=159.0, control="REVISAR")
    r.catastro.consultado = "2026-11-12T09:01"
    r.avisos.append("aviso de prueba")
    r.pdf_catastro = "a_catastro.pdf"
    _pdf(tmp_path / "pdf_catastro" / r.pdf_catastro, 1)
    r.sigpac = ResultadoSigpac("ENCONTRADA", refcat=r.catastro.refcat, pdf="", consultado="2026-11-10T18:30")
    lector = PdfReader(str(escribir_informe([r], tmp_path, "L")))
    hoja = next(p.extract_text() for p in lector.pages if "Trazabilidad" in p.extract_text())
    assert "12/11/2026 09:01" in hoja and "10/11/2026 18:30" in hoja
    assert "aviso de prueba" in hoja and "+403 %" in hoja and "SIXPAC" in hoja


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

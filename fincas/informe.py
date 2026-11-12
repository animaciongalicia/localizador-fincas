"""informe.pdf: un solo PDF por lote con portada-resumen, índice y, por finca, su ficha del Catastro y la de
SIGPAC/SIXPAC (con marcadores). Une PDF ya descargados: no hace ninguna consulta."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

from .modelos import Resultado
from .resumen import calcular, formato_ha, formato_m2

AZUL = "#1F4E78"


@dataclass
class _Entrada:
    n: int
    etiqueta: str
    refcat: str
    sup: str
    uso: str
    control: str
    paginas: List[Tuple[Path, int]]   # (pdf, nº de páginas)
    inicio: int = 0                   # nº de página (1 = primera) donde empieza en el informe


def _leer_paginas(ruta: Path, log: Optional[Callable[[str], None]]) -> int:
    """Nº de páginas de un PDF válido; 0 si no existe o está corrupto (se omite sin tumbar el informe)."""
    from pypdf import PdfReader
    try:
        if not ruta.exists():
            return 0
        return len(PdfReader(str(ruta)).pages)
    except Exception as e:  # noqa: BLE001
        if log:
            log(f"Informe: se omite {ruta.name} ({e})")
        return 0


def _entradas(resultados: Sequence[Resultado], raiz: Path,
              log: Optional[Callable[[str], None]]) -> List[_Entrada]:
    out: List[_Entrada] = []
    for i, r in enumerate(resultados, start=1):
        c = r.catastro
        pdfs: List[Tuple[Path, int]] = []
        candidatos = [raiz / "pdf_catastro" / r.pdf_catastro if r.pdf_catastro else None,
                      raiz / "pdf_sigpac" / r.sigpac.pdf if r.sigpac and r.sigpac.pdf else None]
        for p in candidatos:
            if p is None:
                continue
            n = _leer_paginas(p, log)
            if n:
                pdfs.append((p, n))
        out.append(_Entrada(
            n=i, etiqueta=r.finca.etiqueta, refcat=c.refcat if c else "",
            sup=formato_m2(c.superficie_m2) if c and c.superficie_m2 else "—",
            uso=(c.uso if c and c.uso else "—"), control=r.control_superficie or "—", paginas=pdfs))
    return out


def _frontal(resultados: Sequence[Resultado], entradas: List[_Entrada], titulo: str,
             fecha: str, paginas_previas: int) -> bytes:
    """Portada + resumen + índice. `paginas_previas` = nº de páginas de esta parte (para numerar el índice)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    est = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=est["Title"], textColor=colors.HexColor(AZUL), fontSize=24, leading=28)
    h2 = ParagraphStyle("h2", parent=est["Heading2"], textColor=colors.HexColor(AZUL), spaceBefore=10)
    normal = ParagraphStyle("n", parent=est["Normal"], fontSize=10)
    celda = ParagraphStyle("c", parent=est["Normal"], fontSize=8, leading=10)

    def tabla(filas, anchos, cabecera=True):
        t = Table(filas, colWidths=anchos, repeatRows=1 if cabecera else 0)
        estilo = [("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                  ("VALIGN", (0, 0), (-1, -1), "TOP")]
        if cabecera:
            estilo += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(AZUL)),
                       ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
        t.setStyle(TableStyle(estilo))
        return t

    rs = calcular(resultados)
    el = [Spacer(1, 30 * mm), Paragraph("Informe de localización de fincas", h1),
          Paragraph(escape(titulo), est["Heading2"]),
          Paragraph(f"Fecha: {escape(fecha)}", normal), Spacer(1, 8 * mm),
          Paragraph("Resumen", h2)]
    filas = [["Concepto", "Valor"],
             ["Fincas del lote", str(rs.total)],
             ["Con datos del Catastro", str(rs.con_catastro)],
             ["Superficie catastral total", f"{formato_m2(rs.superficie_m2)}  ({formato_ha(rs.superficie_m2)})"],
             ["Fincas con cuota distinta de completa", str(rs.cuotas_parciales)]]
    for k in ("OK", "REVISAR", "SIN DATO CLIENTE"):
        if rs.control.get(k):
            filas.append([f"Control de superficie: {k}", str(rs.control[k])])
    for k, v in sorted(rs.coincide.items()):
        filas.append([f"Catastro / SIGPAC-SIXPAC: {k}", str(v)])
    el.append(tabla(filas, [95 * mm, 80 * mm]))
    if rs.por_municipio:
        el += [Paragraph("Por municipio", h2),
               tabla([["Municipio", "Fincas", "Superficie"]] +
                     [[escape(n), str(c), formato_m2(m)] for n, c, m in rs.por_municipio[:15]],
                     [95 * mm, 30 * mm, 50 * mm])]
    if rs.por_uso:
        el += [Paragraph("Por uso / cultivo", h2),
               tabla([["Uso", "Fincas", "Superficie"]] +
                     [[escape(n), str(c), formato_m2(m)] for n, c, m in rs.por_uso[:12]],
                     [95 * mm, 30 * mm, 50 * mm])]
    el += [Spacer(1, 6 * mm), Paragraph(
        "Los datos proceden de la consulta pública de datos no protegidos del Catastro y del visor "
        "SIGPAC/SIXPAC. No es una certificación catastral.", celda), PageBreak(),
        Paragraph("Índice de fincas", h2)]
    filas = [["Nº", "Finca", "Ref. catastral", "Superficie", "Uso", "Control", "Pág."]]
    for e in entradas:
        pag = str(e.inicio + paginas_previas) if e.paginas else "—"
        filas.append([str(e.n), Paragraph(escape(e.etiqueta), celda), e.refcat,
                      e.sup, Paragraph(escape(e.uso), celda), e.control, pag])
    el.append(tabla(filas, [9 * mm, 48 * mm, 30 * mm, 24 * mm, 36 * mm, 20 * mm, 10 * mm]))
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                      bottomMargin=15 * mm, title=titulo).build(el)
    return buf.getvalue()


def escribir_informe(resultados: Sequence[Resultado], raiz: Path, titulo: str,
                     fecha: Optional[str] = None,
                     log: Optional[Callable[[str], None]] = None) -> Optional[Path]:
    """Crea raiz/informe.pdf. Devuelve None si no hay ninguna ficha que incluir."""
    from pypdf import PdfReader, PdfWriter

    fecha = fecha or date.today().strftime("%d/%m/%Y")
    entradas = _entradas(resultados, raiz, log)
    if not any(e.paginas for e in entradas):
        if log:
            log("Informe: no hay PDF que unir, no se genera informe.pdf")
        return None

    # el índice cita la página de cada finca, que depende de cuántas páginas ocupa la parte frontal:
    # se genera una vez para contarlas y otra con los números definitivos
    previas = len(PdfReader(io.BytesIO(_frontal(resultados, entradas, titulo, fecha, 0))).pages)
    for _ in range(2):
        pagina = previas + 1
        for e in entradas:
            e.inicio = pagina - previas  # relativo; _frontal le suma `previas`
            if e.paginas:
                pagina += sum(n for _, n in e.paginas)
        frontal = _frontal(resultados, entradas, titulo, fecha, previas)
        real = len(PdfReader(io.BytesIO(frontal)).pages)
        if real == previas:
            break
        previas = real

    w = PdfWriter()
    for p in PdfReader(io.BytesIO(frontal)).pages:
        w.add_page(p)
    w.add_outline_item("Resumen e índice", 0)
    for e in entradas:
        if not e.paginas:
            continue
        primera = len(w.pages)
        for ruta, _ in e.paginas:
            for p in PdfReader(str(ruta)).pages:
                w.add_page(p)
        w.add_outline_item(f"{e.n}. {e.etiqueta}", primera)
    w.add_metadata({"/Title": titulo})
    destino = raiz / "informe.pdf"
    tmp = destino.with_name(destino.name + ".tmp")
    with open(tmp, "wb") as fh:
        w.write(fh)
    tmp.replace(destino)
    return destino

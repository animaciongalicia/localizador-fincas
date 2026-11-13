"""informe.pdf: dossier del lote. Une PDF ya descargados; no hace ninguna consulta.

Estructura: portada (marca, cliente, expediente, fecha) + resumen + índice + metodología y aviso legal,
páginas extra (mapa de conjunto) y, por cada finca, una hoja propia con sus datos y la TRAZABILIDAD
(fuente, fecha y hora de cada consulta, enlace de verificación) seguida de sus fichas del Catastro y
de SIGPAC/SIXPAC. Todas las páginas llevan cabecera (marca · cliente · expediente) y pie (emisor y
"página X de Y"); las fichas externas se reducen un poco para que la cabecera y el pie no las pisen.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

from . import config
from .aprovechamiento import texto_clases
from .inventario import evolucion, texto_reparto
from .modelos import Meta, Resultado, fecha_legible
from .resumen import calcular, formato_ha, formato_m2
from .superficie import explicar_control

AZUL = "#1F4E78"
BANDA_SUP, BANDA_INF, LATERAL = 36.0, 34.0, 14.0   # puntos reservados a cabecera, pie y márgenes

METODOLOGIA = [
    ("Qué contiene este informe",
     "Para cada finca de la lista del cliente: identificación catastral (referencia, superficie y uso o cultivo) "
     "y su plano sobre ortofoto en el sistema de parcelas agrícolas (SIXPAC en Galicia, SIGPAC en el resto)."),
    ("Fuentes",
     "Sede Electrónica del Catastro (consulta pública de datos no protegidos), visor SIXPAC (Xunta de Galicia) o "
     "SIGPAC (FEGA) y servicios cartográficos de la Xunta. La fecha y hora de cada consulta figuran en la hoja "
     "de cada finca."),
    ("Control de superficie",
     "Se compara la superficie catastral con la indicada por el cliente (margen del 10 %), admitiendo que sea la "
     "de la finca entera o la de la cuota. «OK»: coincide. «REVISAR»: no coincide y conviene comprobarlo. "
     "«SIN DATO CLIENTE»: no se facilitó superficie. Es un aviso: no se descarta ninguna finca."),
    ("Coincidencia Catastro / SIXPAC-SIGPAC",
     "Se compara la referencia catastral del Catastro con la que el visor asocia a la parcela. «SI»: coinciden. "
     "«NO»: difieren. «NO ENCONTRADA»: la parcela no aparece en el visor."),
    ("Cobertura forestal actual (solo Galicia)",
     "El cultivo del Catastro puede estar desactualizado; se contrasta con el Inventario Forestal Continuo de "
     "Galicia (Xunta de Galicia), que cada año clasifica el terreno por teledetección. «COHERENTE»: lo declarado "
     "en el Catastro ocupa al menos la mitad de la parcela. «REVISAR»: no es así, o hay una corta o un incendio "
     "en una quinta parte o más. «NO COMPARABLE»: el cultivo catastral no tiene equivalencia. Indica también si "
     "figura como monte vecinal o público. Es orientativo: no mide edad, densidad ni calidad de la madera."),
    ("Condicionantes de una corta o una venta (solo Galicia)",
     "Se comprueba si la parcela toca alguna capa publicada por la Xunta de Galicia: aguas, carreteras, costas, "
     "patrimonio cultural, Camino de Santiago, espacios naturales, nematodo del pino, registro forestal (montes "
     "vecinales, de utilidad pública u ordenados, masas de frondosas autóctonas), faja de protección de 50 m, "
     "líneas eléctricas y gasoductos, concentración parcelaria e incendios registrados de 2010 a 2021. Se indica "
     "a quién corresponde cada una y la dificultad de saca (pendiente y accesos). Es orientativo: no indica si "
     "la corta está permitida; el trámite lo confirma la Consellería do Medio Rural. Que no se detecte una "
     "afección no garantiza que no exista."),
    ("Qué no incluye",
     "Titulares, valor catastral y cargas: son datos protegidos o registrales que no ofrece la consulta pública."),
]
AVISO_LEGAL = (
    "Este informe tiene carácter meramente informativo. No es una certificación catastral ni registral, no "
    "acredita la titularidad ni los linderos de las fincas y no sustituye a la información del Registro de la "
    "Propiedad ni a un levantamiento topográfico. Los datos proceden de fuentes públicas en las fechas "
    "indicadas y pueden haber cambiado con posterioridad; su titularidad corresponde a los organismos citados. "
    "Las distancias y posiciones de los planos son orientativas.")


@dataclass
class _Entrada:
    n: int
    r: Resultado
    etiqueta: str
    refcat: str
    sup: str
    uso: str
    control: str
    paginas: List[Tuple[Path, int]]   # (pdf, nº de páginas)
    inicio: int = 0                   # nº de página (1 = primera) donde empieza su hoja en el informe
    hoja: int = 1                     # páginas que ocupa su hoja propia (casi siempre 1)

    @property
    def n_paginas(self) -> int:
        return self.hoja + sum(n for _, n in self.paginas)   # su hoja + sus fichas


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
            n=i, r=r, etiqueta=r.finca.etiqueta, refcat=c.refcat if c else "",
            sup=formato_m2(c.superficie_m2) if c and c.superficie_m2 else "—",
            uso=(c.uso if c and c.uso else "—"), control=r.control_superficie or "—", paginas=pdfs))
    return out


# ------------------------------------------------------------------ páginas propias (reportlab)
def _estilos():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    est = getSampleStyleSheet()
    verde = colors.HexColor(config.MARCA_COLOR)
    return {
        "marca": ParagraphStyle("marca", parent=est["Title"], textColor=verde, fontName="Helvetica-Bold",
                                fontSize=34, leading=40, alignment=0),
        "h1": ParagraphStyle("h1", parent=est["Title"], textColor=colors.HexColor(AZUL), fontSize=20,
                             leading=24, alignment=0),
        "h2": ParagraphStyle("h2", parent=est["Heading2"], textColor=colors.HexColor(AZUL), spaceBefore=10),
        "h3": ParagraphStyle("h3", parent=est["Heading3"], textColor=verde, spaceBefore=8, spaceAfter=2),
        "n": ParagraphStyle("n", parent=est["Normal"], fontSize=10, leading=13),
        "c": ParagraphStyle("c", parent=est["Normal"], fontSize=8, leading=10),
        "p": ParagraphStyle("p", parent=est["Normal"], fontSize=9.5, leading=13, spaceAfter=4),
    }


def _tabla(filas, anchos, cabecera=True, clave=False):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    t = Table(filas, colWidths=anchos, repeatRows=1 if cabecera else 0)
    estilo = [("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
              ("VALIGN", (0, 0), (-1, -1), "TOP")]
    if cabecera:
        estilo += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(AZUL)),
                   ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
    if clave:  # primera columna como rótulo
        estilo += [("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF2EE")),
                   ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold")]
    t.setStyle(TableStyle(estilo))
    return t


def _construir(elementos, titulo: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=20 * mm,
                      bottomMargin=18 * mm, title=titulo).build(elementos)
    return buf.getvalue()


def _frontal(resultados: Sequence[Resultado], entradas: List[_Entrada], titulo: str, meta: Meta,
             paginas_previas: int) -> bytes:
    """Portada + resumen + índice + metodología. `paginas_previas` = páginas antes de la primera finca."""
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    e_ = _estilos()
    rs = calcular(resultados)
    el = [Spacer(1, 22 * mm), Paragraph(escape(config.MARCA), e_["marca"]),
          Paragraph("Informe de localización de fincas", e_["h1"]), Spacer(1, 4 * mm),
          _tabla([["Cliente", Paragraph(escape(meta.cliente or titulo), e_["n"])],
                  ["Expediente", meta.expediente or "—"],
                  ["Fecha de emisión", meta.fecha],
                  ["Lote", Paragraph(escape(titulo), e_["n"])]],
                 [45 * mm, 130 * mm], cabecera=False, clave=True),
          Paragraph("Resumen", e_["h2"])]
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
    el.append(_tabla(filas, [95 * mm, 80 * mm]))
    if rs.por_municipio:
        el += [Paragraph("Por municipio", e_["h2"]),
               _tabla([["Municipio", "Fincas", "Superficie"]] +
                      [[escape(n), str(c), formato_m2(m)] for n, c, m in rs.por_municipio[:15]],
                      [95 * mm, 30 * mm, 50 * mm])]
    if rs.por_uso:
        el += [Paragraph("Por uso / cultivo", e_["h2"]),
               _tabla([["Uso", "Fincas", "Superficie"]] +
                      [[Paragraph(escape(n), e_["c"]), str(c), formato_m2(m)] for n, c, m in rs.por_uso[:12]],
                      [95 * mm, 30 * mm, 50 * mm])]
    el += [PageBreak(), Paragraph("Índice de fincas", e_["h2"])]
    filas = [["Nº", "Finca", "Ref. catastral", "Superficie", "Uso", "Control", "Pág."]]
    for e in entradas:
        filas.append([str(e.n), Paragraph(escape(e.etiqueta), e_["c"]), e.refcat, e.sup,
                      Paragraph(escape(e.uso), e_["c"]), Paragraph(escape(e.control), e_["c"]),   # Paragraph: parte línea
                      str(e.inicio + paginas_previas)])
    el.append(_tabla(filas, [9 * mm, 46 * mm, 30 * mm, 24 * mm, 32 * mm, 26 * mm, 10 * mm]))
    el += _cotejo(entradas, e_)
    el += [PageBreak(), Paragraph("Metodología y fuentes", e_["h2"])]
    for t, txt in METODOLOGIA:
        el += [Paragraph(escape(t), e_["h3"]), Paragraph(escape(txt), e_["p"])]
    el += [Paragraph("Aviso legal", e_["h3"]), Paragraph(escape(AVISO_LEGAL), e_["p"]),
           Paragraph(f"Emitido por {escape(config.PIE_INFORME)}.", e_["c"])]
    return _construir(el, titulo)


def _cotejo(entradas: List[_Entrada], e_) -> list:
    """Página 'Cotejo con el Inventario Forestal': qué fincas difieren del Catastro y en qué. [] si no hay cotejo."""
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    cotejadas = [e for e in entradas if e.r.contraste]
    if not cotejadas:
        return []
    grupos = {k: [e for e in cotejadas if e.r.contraste == k]
              for k in ("REVISAR", "COHERENTE", "NO COMPARABLE", "SIN DATO")}
    anio = max((e.r.inventario.anio for e in cotejadas if e.r.inventario), default=0)
    edicion = f", edición {anio}" if anio else ""
    dif, coh = grupos["REVISAR"], grupos["COHERENTE"]

    def lista(es: List[_Entrada]) -> str:
        return ", ".join(f"nº {e.n}" for e in es)

    el = [PageBreak(), Paragraph("Cotejo con el Inventario Forestal Continuo de Galicia", e_["h2"]),
          Paragraph(escape(
              f"El cultivo que figura en el Catastro se ha cotejado, finca a finca, con el Inventario Forestal "
              f"Continuo de Galicia (Xunta de Galicia{edicion}), que refleja lo que hay hoy sobre el terreno. "
              f"De {len(cotejadas)} fincas cotejadas, {len(dif)} presentan diferencias con el Catastro y "
              f"{len(coh)} son coherentes."), e_["p"])]
    if dif:
        filas = [["Nº", "Finca", "Según el Catastro", f"Según el inventario{' ' + str(anio) if anio else ''}"]]
        for e in dif:
            inv = e.r.inventario
            filas.append([str(e.n), Paragraph(escape(e.etiqueta), e_["c"]), Paragraph(escape(e.uso), e_["c"]),
                          Paragraph(escape(texto_reparto(inv) if inv else "—"), e_["c"])])
        el += [Paragraph("Fincas con diferencias", e_["h3"]),
               _tabla(filas, [9 * mm, 44 * mm, 50 * mm, 72 * mm]), Spacer(1, 3 * mm)]
    for titulo, clave, texto in (
            ("Fincas sin diferencias apreciables", "COHERENTE", ""),
            ("No comparables", "NO COMPARABLE", " (el cultivo catastral no tiene equivalencia en el inventario)"),
            ("Sin dato", "SIN DATO", " (la parcela no figura en el inventario)")):
        if grupos[clave]:
            el.append(Paragraph(f"<b>{titulo}:</b> {escape(lista(grupos[clave]) + texto)}.", e_["p"]))
    vecinales = [e for e in cotejadas if e.r.inventario and e.r.inventario.monte_vecinal]
    if vecinales:
        el.append(Paragraph(f"<b>Figuran como monte vecinal o público en el inventario:</b> "
                            f"{escape(lista(vecinales))}. Conviene confirmarlo antes de cualquier operación.", e_["p"]))
    el.append(Paragraph("El inventario se elabora por teledetección y es orientativo: señala dónde conviene mirar, "
                        "no sustituye la visita a la finca. El detalle de cada finca figura en su hoja.", e_["c"]))
    return el


def _enlace_sede(r: Resultado) -> str:
    c, cod = r.catastro, r.codigos
    if not c or not cod:
        return ""
    return config.SEDE_CONSULTA_URL.format(
        prov=cod.cod_provincia, mun=cod.cod_municipio_catastro,
        rc20=c.referencias_completas[0] if c.referencias_completas else c.refcat)


def _hoja_finca(e: _Entrada, total: int, titulo: str) -> bytes:
    """Una página: datos de la finca, comprobaciones, incidencias y trazabilidad de cada consulta."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    e_ = _estilos()
    r, f, c, s = e.r, e.r.finca, e.r.catastro, e.r.sigpac

    def P(txt: object):
        return Paragraph(escape(str(txt)) if txt not in (None, "") else "—", e_["c"])

    en_galicia = bool(r.codigos and r.codigos.cod_provincia in {"15", "27", "32", "36"})
    visor = "SIXPAC (Xunta de Galicia)" if en_galicia else "SIGPAC (FEGA)"
    el = [Paragraph(f"Finca {e.n} de {total}", e_["h3"]), Paragraph(escape(e.etiqueta), e_["h1"]),
          Paragraph("Identificación", e_["h2"]),
          _tabla([["Municipio", P(f"{f.municipio} ({f.provincia})" if f.provincia else f.municipio)],
                  ["Paraje", P(f.paraje or (c.paraje if c else ""))],
                  ["Polígono / parcela", P(f"{f.poligono} / {f.parcela}" if f.poligono else "")],
                  ["Referencia catastral", P(c.refcat if c else "")],
                  ["Localización (Catastro)", P(c.localizacion if c else "")],
                  ["Uso / cultivo", P(c.uso_detallado if c else "")]],
                 [50 * mm, 125 * mm], cabecera=False, clave=True),
          Paragraph("Superficie y comprobaciones", e_["h2"])]
    detalle = explicar_control(c.superficie_m2 if c else None, f.superficie_m2, f.cuota, r.control_superficie)
    el.append(_tabla(
        [["Superficie catastral", P(f"{formato_m2(c.superficie_m2)}  ({formato_ha(c.superficie_m2)})"
                                    if c and c.superficie_m2 else "")],
         ["Superficie indicada por el cliente", P(formato_m2(f.superficie_m2) if f.superficie_m2 else "")],
         ["Cuota", P(f.cuota.texto)],
         ["Control de superficie", P(f"{r.control_superficie}" + (f" — {detalle}" if detalle else ""))],
         [f"Referencia según {visor.split(' ')[0]}", P(s.refcat if s else "")],
         ["Coincide con Catastro", P(r.coincide)]],
        [50 * mm, 125 * mm], cabecera=False, clave=True))
    inv = r.inventario
    if inv:
        filas_inv = [["Cobertura actual", P(texto_reparto(inv))]]
        if evolucion(inv):
            filas_inv.append(["Evolución", P(evolucion(inv))])
        if inv.volumen_m3:
            filas_inv.append(["Volumen de madera", P(f"{inv.volumen_m3:,.0f} m³".replace(",", ".") +
                                                     " (estimación provisional del inventario, pendiente de validar)")])
        if inv.monte_vecinal is not None:
            filas_inv.append(["Monte vecinal o público",
                              P("SÍ figura como monte vecinal o público en el inventario: conviene confirmarlo"
                                if inv.monte_vecinal else "No figura como tal en el inventario")])
        filas_inv.append(["Frente al cultivo del Catastro",
                          P(r.contraste + (f" — {r.contraste_detalle}" if r.contraste_detalle else ""))])
        el += [Paragraph(f"Cobertura actual (Inventario Forestal de Galicia, edición {inv.anio})", e_["h2"]),
               _tabla(filas_inv, [50 * mm, 125 * mm], cabecera=False, clave=True)]
    elif r.contraste == "SIN DATO":
        el += [Paragraph("Cobertura actual (Inventario Forestal de Galicia)", e_["h2"]),
               Paragraph("La parcela no figura en el inventario.", e_["c"])]
    ap = r.aprovechamiento
    if ap:
        el.append(Paragraph("Si se plantea una corta o una venta: qué conviene saber", e_["h2"]))
        if ap.afecciones:
            el.append(_tabla([["Qué se ha detectado", "Detalle", "A quién corresponde"]] +
                             [[P(a["tipo"]), P(a["detalle"]), P(a["organismo"])] for a in ap.afecciones[:8]],
                             [50 * mm, 62 * mm, 63 * mm]))
            if len(ap.afecciones) > 8:
                el.append(Paragraph(f"… y {len(ap.afecciones) - 8} más (ver Excel).", e_["c"]))
        else:
            el.append(Paragraph("No se detecta ninguna afección en las capas consultadas (aguas, carreteras, "
                                "costas, patrimonio cultural y natural, Camino de Santiago, nematodo del pino, "
                                "registro forestal, faja de 50 m, líneas eléctricas, concentración parcelaria, "
                                "incendios 2010-2021).", e_["c"]))
        extra = []
        if ap.saca:
            extra.append(["Dificultad de saca", P(texto_clases(ap.saca) + " (pendiente y accesos, mapa de la Xunta)")])
        if ap.integrada:
            extra.append(["Disponibilidad técnica y legal", P(texto_clases(ap.integrada) + " (clasificación de la Xunta)")])
        if ap.sin_consultar:
            extra.append(["Sin consultar", P("No se pudo consultar: " + ", ".join(ap.sin_consultar))])
        if extra:
            el += [Spacer(1, 2 * mm), _tabla(extra, [50 * mm, 125 * mm], cabecera=False, clave=True)]
        el.append(Paragraph("Orientativo: no indica si la corta está permitida. El trámite lo confirma la "
                            "Consellería do Medio Rural.", e_["c"]))
    incid = [x for x in [f.notas, *r.avisos, *r.errores] if x]
    if incid:
        el.append(Paragraph("Observaciones e incidencias", e_["h2"]))
        el += [Paragraph("• " + escape(x), e_["p"]) for x in incid]
    url = _enlace_sede(r)
    enlace = (Paragraph(f'<link href="{escape(url)}" color="#0563C1"><u>Verificar en la Sede del Catastro</u></link>',
                        e_["c"]) if url else P(""))
    filas = [["Dato", "Fuente", "Fecha y hora de consulta", "Verificación / documento"],
             [P("Referencia, superficie y uso"), P("Catastro: consulta de datos no protegidos"),
              P(fecha_legible(c.consultado) if c else "sin datos"), enlace],
             [P("Ficha descriptiva y gráfica"), P("Sede Electrónica del Catastro"),
              P("ver fecha impresa en la ficha" if r.pdf_catastro else "no obtenida"), P(r.pdf_catastro)],
             [P("Plano, ortofoto y referencia"), P(f"Visor {visor}"),
              P(fecha_legible(s.consultado) if s and s.estado != "ERROR" else "no obtenida"),
              P(s.pdf if s else "")]]
    if inv:
        filas.append([P("Cobertura forestal actual"),
                      P(f"Inventario Forestal Continuo de Galicia (Xunta), edición {inv.anio}"),
                      P(fecha_legible(inv.consultado) or "sin fecha"),
                      Paragraph(f'<link href="{escape(config.INVEGA_VISOR_URL)}" color="#0563C1">'
                                "<u>Visor del inventario</u></link>", e_["c"])])
    if ap:
        filas.append([P("Condicionantes de corta"),
                      P("Capas de afecciones y de restricciones de la Xunta de Galicia (IDEG)"),
                      P(fecha_legible(ap.consultado) or "sin fecha"), P("")])
    from reportlab.platypus import KeepTogether
    el.append(KeepTogether([Paragraph("Trazabilidad de las consultas", e_["h2"]),      # no se parte entre páginas
                            _tabla(filas, [40 * mm, 48 * mm, 37 * mm, 50 * mm])]))
    el += [Spacer(1, 3 * mm),
           Paragraph("Las páginas siguientes reproducen los documentos obtenidos de cada fuente."
                     if e.paginas else "No se obtuvo ningún documento de las fuentes para esta finca.", e_["c"])]
    return _construir(el, titulo)


# ------------------------------------------------------------------ cabecera y pie en todas las páginas
def _marco(ancho: float, alto: float, meta: Meta, n: int, total: int):
    """Página transparente del tamaño dado con la cabecera y el pie, lista para superponer."""
    from pypdf import PdfReader
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(ancho, alto))
    gris = colors.HexColor("#555555")
    y = alto - 22
    c.setFillColor(colors.HexColor(config.MARCA_COLOR))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(LATERAL + 4, y, config.MARCA)
    x = LATERAL + 4 + c.stringWidth(config.MARCA, "Helvetica-Bold", 11) + 8
    c.setFillColor(gris)
    c.setFont("Helvetica", 8)
    partes = [p for p in (meta.cliente, f"Expediente {meta.expediente}" if meta.expediente else "",
                          meta.fecha) if p]
    texto = "  ·  ".join(partes)
    while texto and c.stringWidth(texto, "Helvetica", 8) > ancho - x - LATERAL - 4:
        texto = texto[:-2]
    c.drawString(x, y, texto)
    c.setStrokeColor(colors.HexColor(config.MARCA_COLOR))
    c.setLineWidth(0.8)
    c.line(LATERAL, alto - 28, ancho - LATERAL, alto - 28)
    c.setStrokeColor(colors.HexColor("#BBBBBB"))
    c.setLineWidth(0.4)
    c.line(LATERAL, 26, ancho - LATERAL, 26)
    c.setFillColor(gris)
    c.setFont("Helvetica", 7)
    c.drawString(LATERAL + 4, 15, config.PIE_INFORME)
    c.drawRightString(ancho - LATERAL - 4, 15, f"Página {n} de {total}")
    c.showPage()
    c.save()
    return PdfReader(io.BytesIO(buf.getvalue())).pages[0]


def _enmarcar(escritor, pagina, externa: bool, meta: Meta, n: int, total: int) -> None:
    """Añade al escritor una página nueva con cabecera y pie. Las externas (fichas de las fuentes, mapa) se
    reducen para caber entre ambas bandas; las propias ya dejan ese margen."""
    from pypdf import Transformation

    if getattr(pagina, "rotation", 0):  # solo si viene girada (evita reescribir páginas sin necesidad)
        try:
            pagina.transfer_rotation_to_content()
        except Exception:  # noqa: BLE001
            pass
    caja = pagina.mediabox
    w, h = float(caja.width), float(caja.height)
    nueva = escritor.add_blank_page(width=w, height=h)  # ya pertenece al escritor: la fusión es fiable
    if externa:
        s = min((w - 2 * LATERAL) / w, (h - BANDA_SUP - BANDA_INF) / h)
        tx = (w - w * s) / 2
        ty = BANDA_INF + ((h - BANDA_SUP - BANDA_INF) - h * s) / 2
        op = Transformation().translate(-float(caja.left), -float(caja.bottom)).scale(s, s).translate(tx, ty)
    else:
        op = Transformation().translate(-float(caja.left), -float(caja.bottom))
    nueva.merge_transformed_page(pagina, op)
    nueva.merge_page(_marco(w, h, meta, n, total))


def escribir_informe(resultados: Sequence[Resultado], raiz: Path, titulo: str,
                     fecha: Optional[str] = None,
                     log: Optional[Callable[[str], None]] = None,
                     extras: Sequence[Tuple[str, Path]] = (),
                     meta: Optional[Meta] = None) -> Optional[Path]:
    """Crea raiz/informe.pdf. `extras` = páginas sueltas (p. ej. el mapa de conjunto) que van entre la
    metodología y las fincas. Devuelve None si ninguna finca tiene documentos que incluir."""
    from pypdf import PdfReader, PdfWriter

    meta = meta or Meta(cliente=titulo)
    meta = Meta(cliente=meta.cliente or titulo, expediente=meta.expediente,
                fecha=fecha or meta.fecha or date.today().strftime("%d/%m/%Y"))
    entradas = _entradas(resultados, raiz, log)
    if not any(e.paginas for e in entradas):
        if log:
            log("Informe: no hay PDF que unir, no se genera informe.pdf")
        return None
    extras_ok = [(t, Path(r), _leer_paginas(Path(r), log)) for t, r in extras]
    extras_ok = [x for x in extras_ok if x[2]]
    n_extra = sum(n for _, _, n in extras_ok)

    # el índice cita la página de cada finca, que depende de cuántas páginas ocupa la parte frontal:
    # se genera una vez para contarlas y otra con los números definitivos
    pagina = 1
    for e in entradas:
        e.hoja = len(PdfReader(io.BytesIO(_hoja_finca(e, len(entradas), titulo))).pages)
        e.inicio = pagina                 # relativo a la primera finca; _frontal le suma las previas
        pagina += e.n_paginas
    frontal_n = len(PdfReader(io.BytesIO(_frontal(resultados, entradas, titulo, meta, 0))).pages)
    for _ in range(2):
        frontal = _frontal(resultados, entradas, titulo, meta, frontal_n + n_extra)
        real = len(PdfReader(io.BytesIO(frontal)).pages)
        if real == frontal_n:
            break
        frontal_n = real

    # (página, ¿externa?) en orden, y marcadores (título, índice de página)
    paginas: List[Tuple[object, bool]] = [(p, False) for p in PdfReader(io.BytesIO(frontal)).pages]
    marcadores: List[Tuple[str, int]] = [("Resumen e índice", 0)]
    for nombre, ruta, _ in extras_ok:
        marcadores.append((nombre, len(paginas)))
        paginas += [(p, True) for p in PdfReader(str(ruta)).pages]
    for e in entradas:
        marcadores.append((f"{e.n}. {e.etiqueta}", len(paginas)))
        paginas += [(p, False) for p in PdfReader(io.BytesIO(_hoja_finca(e, len(entradas), titulo))).pages]
        for ruta, _ in e.paginas:
            paginas += [(p, True) for p in PdfReader(str(ruta)).pages]

    w = PdfWriter()
    total = len(paginas)
    for i, (p, externa) in enumerate(paginas, start=1):
        antes = len(w.pages)
        try:
            _enmarcar(w, p, externa, meta, i, total)
        except Exception as ex:  # noqa: BLE001  una página rara no tumba el dossier: va sin marco
            if log:
                log(f"Informe: la página {i} va sin cabecera/pie ({ex})")
            while len(w.pages) > antes:
                w.remove_page(len(w.pages) - 1)
            w.add_page(p)
    for nombre, idx in marcadores:
        w.add_outline_item(nombre, idx)
    w.add_metadata({"/Title": f"{titulo} — {meta.expediente}".strip(" —"), "/Author": config.MARCA})
    try:
        w.compress_identical_objects()
    except Exception:  # noqa: BLE001
        pass
    destino = raiz / "informe.pdf"
    tmp = destino.with_name(destino.name + ".tmp")
    with open(tmp, "wb") as fh:
        w.write(fh)
    tmp.replace(destino)
    return destino

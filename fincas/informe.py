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
from . import precios
from .colindantes import texto as texto_colindantes
from .geologia import km, texto_derechos, texto_geoquimica
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
    ("Geología y minería",
     "Con el centro de cada finca se consulta al IGME-CSIC la hoja y la litología del Mapa Geológico 1:50.000 "
     "(MAGNA), los indicios mineros y explotaciones de su base de datos (BDMIN) a menos de 5 km y el Atlas "
     "Geoquímico de España; en Galicia, también los derechos mineros vigentes que publica la Xunta. Es "
     "orientativo: un indicio cercano o un valor alto del atlas, que es regional, no significan que haya mineral "
     "en la finca. Figura en el anexo geológico y minero, al final."),
    ("Precio de referencia de la zona (solo Galicia)",
     "Precio medio de las compraventas de tierra rústica de la comarca, separado en monte y tierra agraria, que "
     "publica el Observatorio Galego de Mobilidade de Terras (Xunta de Galicia) a partir de lo declarado a la "
     "Axencia Tributaria de Galicia. Es un dato comarcal y orientativo: no es el valor de ninguna finca ni una "
     "tasación."),
    ("Parcelas colindantes y acceso (solo Galicia)",
     "Parcelas que tocan cada finca según el parcelario SIXPAC, identificadas por polígono y parcela (nunca por "
     "titular), y si linda con terreno de dominio público (camino, vía o cauce)."),
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
        "h2": ParagraphStyle("h2", parent=est["Heading2"], textColor=colors.HexColor(AZUL), spaceBefore=10,
                             keepWithNext=1),   # un título nunca queda solo al pie de la página
        "h3": ParagraphStyle("h3", parent=est["Heading3"], textColor=verde, spaceBefore=8, spaceAfter=2,
                             keepWithNext=1),
        "n": ParagraphStyle("n", parent=est["Normal"], fontSize=10, leading=13),
        "c": ParagraphStyle("c", parent=est["Normal"], fontSize=8, leading=10, splitLongWords=1),
        "p": ParagraphStyle("p", parent=est["Normal"], fontSize=9.5, leading=13, spaceAfter=4),
    }


def _tabla(filas, anchos, cabecera=True, clave=False, extra=()):
    """Tabla en la que ningún texto se sale de su celda: las cadenas sueltas se convierten en párrafos
    (que parten línea) con el estilo de su posición: cabecera, rótulo de la primera columna o normal."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle

    normal = ParagraphStyle("t_n", fontName="Helvetica", fontSize=8.5, leading=10.5, splitLongWords=1)
    negrita = ParagraphStyle("t_b", parent=normal, fontName="Helvetica-Bold")
    blanca = ParagraphStyle("t_h", parent=negrita, textColor=colors.white)

    def celda(valor, i: int, j: int):
        if not isinstance(valor, str):
            return valor
        estilo = blanca if (cabecera and i == 0) else negrita if (clave and j == 0) else normal
        return Paragraph(escape(valor), estilo)

    filas = [[celda(v, i, j) for j, v in enumerate(fila)] for i, fila in enumerate(filas)]
    t = Table(filas, colWidths=anchos, repeatRows=1 if cabecera else 0)
    estilo = [("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
              ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]
    if cabecera:
        estilo += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(AZUL))]
    if clave:  # primera columna como rótulo
        estilo += [("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF2EE"))]
    t.setStyle(TableStyle(estilo + list(extra)))
    return t


def _construir(elementos, titulo: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=20 * mm,
                      bottomMargin=18 * mm, title=titulo).build(elementos)
    return buf.getvalue()


VERDE_C, AMBAR_C, ROJO_C = "#C6E0B4", "#FFE699", "#F8CBAD"


def _tarjetas(cifras: Sequence[Tuple[str, str]]):
    """Fila de recuadros con una cifra grande y su rótulo (portada)."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    verde = colors.HexColor(config.MARCA_COLOR)
    num = ParagraphStyle("tj_n", fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=verde, alignment=1)
    rot = ParagraphStyle("tj_r", fontName="Helvetica", fontSize=8.5, leading=10.5, alignment=1,
                         textColor=colors.HexColor("#333333"))
    ancho = 177 * mm / len(cifras)
    t = Table([[[Paragraph(escape(v), num), Paragraph(escape(r), rot)] for v, r in cifras]],
              colWidths=[ancho] * len(cifras))
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, verde), ("INNERGRID", (0, 0), (-1, -1), 0.6, verde),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F7F2")),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 9),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
    return t


def _frontal(resultados: Sequence[Resultado], entradas: List[_Entrada], titulo: str, meta: Meta,
             paginas_previas: int, contenido: Sequence[Tuple[str, int]] = ()) -> bytes:
    """Portada con las cifras clave + contenido del documento + índice de fincas. `paginas_previas` = páginas de
    esta parte frontal; las páginas de `contenido` y de cada finca son relativas a la primera que la sigue."""
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    e_ = _estilos()
    rs = calcular(resultados)
    difieren = sum(1 for r in resultados if r.contraste == "REVISAR")
    con_puntos = sum(1 for r in resultados if puntos_a_comprobar(r))
    el = [Spacer(1, 22 * mm), Paragraph(escape(config.MARCA), e_["marca"]),
          Paragraph("Informe de localización de fincas", e_["h1"]), Spacer(1, 4 * mm),
          _tabla([["Cliente", Paragraph(escape(meta.cliente or titulo), e_["n"])],
                  ["Expediente", meta.expediente or "—"],
                  ["Fecha de emisión", meta.fecha],
                  ["Lote", Paragraph(escape(titulo), e_["n"])]],
                 [45 * mm, 132 * mm], cabecera=False, clave=True),
          Spacer(1, 6 * mm),
          _tarjetas([(str(rs.total), "fincas en el lote"),
                     (formato_ha(rs.superficie_m2) if rs.superficie_m2 else "—", "superficie catastral total"),
                     (str(difieren), "con un uso real distinto del que declara el Catastro"),
                     (str(con_puntos), "con algo que aclarar antes de una operación")]),
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
    el.append(_tabla(filas, [97 * mm, 80 * mm]))
    if rs.por_municipio:
        el += [Paragraph("Por municipio", e_["h2"]),
               _tabla([["Municipio", "Fincas", "Superficie"]] +
                      [[n, str(c), formato_m2(m)] for n, c, m in rs.por_municipio[:15]],
                      [97 * mm, 30 * mm, 50 * mm])]
    if rs.por_uso:
        el += [Paragraph("Por uso / cultivo según el Catastro", e_["h2"]),
               _tabla([["Uso", "Fincas", "Superficie"]] +
                      [[Paragraph(escape(n), e_["c"]), str(c), formato_m2(m)] for n, c, m in rs.por_uso[:12]],
                      [97 * mm, 30 * mm, 50 * mm])]
    el += [PageBreak(), Paragraph("Contenido", e_["h1"])]
    if contenido:
        el.append(_tabla([["Apartado", "Pág."]] +
                         [[nombre, str(pag + paginas_previas)] for nombre, pag in contenido], [165 * mm, 12 * mm]))
    el.append(Paragraph("Índice de fincas", e_["h2"]))
    filas = [["Nº", "Finca", "Ref. catastral", "Superficie", "Uso", "Control", "Pág."]]
    for e in entradas:
        filas.append([str(e.n), Paragraph(escape(e.etiqueta), e_["c"]), e.refcat, e.sup,
                      Paragraph(escape(e.uso), e_["c"]), Paragraph(escape(e.control), e_["c"]),   # Paragraph: parte línea
                      str(e.inicio + paginas_previas)])
    el.append(_tabla(filas, [9 * mm, 45 * mm, 30 * mm, 24 * mm, 31 * mm, 26 * mm, 12 * mm]))
    return _construir(el, titulo)


def _resumen_ejecutivo(entradas: List[_Entrada], titulo: str) -> Optional[bytes]:
    """Una página para leer primero: todas las fincas en una tabla con marcas de color. Verde = sin nada que
    señalar; ámbar = conviene mirarlo; rojo = afecta a la titularidad. Solo resume lo que consta en el informe."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    con = [e for e in entradas if e.r.catastro]
    if not con:
        return None
    e_ = _estilos()

    def P(txt: object):
        return Paragraph(escape(str(txt)) if txt not in (None, "") else "—", e_["c"])

    filas = [["Nº", "Finca", "Superficie", "Hoy sobre el terreno", "Frente al Catastro", "Afecciones", "Acceso",
              "A aclarar"]]
    fondo = []

    def marca(col: int, fila: int, color: Optional[str]) -> None:
        if color:
            fondo.append(("BACKGROUND", (col, fila), (col, fila), colors.HexColor(color)))

    for i, e in enumerate(con, start=1):
        r, c, inv, ap, col = e.r, e.r.catastro, e.r.inventario, e.r.aprovechamiento, e.r.colindantes
        if inv and inv.dominante:
            total = sum(inv.coberturas.values()) or 1
            hoy = f"{inv.dominante} {inv.coberturas.get(inv.dominante, 0) / total * 100:.0f} %"
        else:
            hoy = c.uso or "—"
        frente = {"COHERENTE": ("Coincide", VERDE_C), "REVISAR": ("No coincide", AMBAR_C)}.get(r.contraste, ("—", None))
        vecinal = bool(inv and inv.monte_vecinal) or any(
            p in (a["tipo"] + a["detalle"]).lower() for a in (ap.afecciones if ap else [])
            for p in ("vecinal", "veciñal"))
        tipos = list(dict.fromkeys(a["tipo"].split(" (")[0].replace(" registrado en la parcela", "")
                                   for a in (ap.afecciones if ap else [])))
        if vecinal:
            afe = ("Monte vecinal" + (f" y {len(tipos)} más" if len(tipos) > 1 else ""), ROJO_C)
        elif tipos:
            afe = ("; ".join(tipos), AMBAR_C)
        else:
            afe = ("Ninguna", VERDE_C) if ap else ("—", None)
        if col is None:
            acc = ("—", None)
        else:
            acc = ("Linda con dominio público", VERDE_C) if col.dominio_publico else ("No consta", AMBAR_C)
        n = len(puntos_a_comprobar(r))
        acl = ("Nada", VERDE_C) if not n else (f"{n} punto" + ("s" if n > 1 else ""), AMBAR_C)
        filas.append([str(e.n), P(e.etiqueta), P(e.sup), P(hoy), P(frente[0]), P(afe[0]), P(acc[0]), P(acl[0])])
        for j, x in zip((4, 5, 6, 7), (frente, afe, acc, acl)):
            marca(j, i, x[1])
    el = [Paragraph("Resumen ejecutivo", e_["h1"]),
          Paragraph("Todas las fincas del lote de un vistazo. El detalle de cada marca está en el resumen para la "
                    "compraventa y en la hoja de cada finca.", e_["p"]),
          Spacer(1, 2 * mm),
          _tabla(filas, [8 * mm, 33 * mm, 20 * mm, 29 * mm, 21 * mm, 28 * mm, 20 * mm, 18 * mm], extra=fondo),
          Spacer(1, 3 * mm),
          _tabla([["", "Sin nada que señalar en las fuentes consultadas"],
                  ["", "Conviene mirarlo antes de una operación"],
                  ["", "Afecta a quién es el titular de la finca"]], [8 * mm, 169 * mm], cabecera=False,
                 extra=[("BACKGROUND", (0, 0), (0, 0), colors.HexColor(VERDE_C)),
                        ("BACKGROUND", (0, 1), (0, 1), colors.HexColor(AMBAR_C)),
                        ("BACKGROUND", (0, 2), (0, 2), colors.HexColor(ROJO_C))]),
          Spacer(1, 3 * mm),
          Paragraph("<b>Hoy sobre el terreno:</b> cobertura dominante según el Inventario Forestal Continuo de Galicia. "
                    "<b>Frente al Catastro:</b> si lo que declara el Catastro coincide con esa cobertura. "
                    "<b>Acceso:</b> si la finca linda con terreno de dominio público (camino, vía o cauce) según "
                    "SIXPAC. <b>A aclarar:</b> puntos del resumen para la compraventa. Las marcas resumen datos "
                    "públicos; no valoran la finca ni recomiendan la operación.", e_["c"])]
    return _construir(el, titulo)


def _seccion_precios(entradas: List[_Entrada], titulo: str) -> Optional[bytes]:
    """'Precio de referencia de la zona': precio medio comarcal de la tierra rústica (Xunta). Nunca se multiplica
    por la superficie: no es una tasación. None si ninguna finca tiene comarca con dato."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    refs = [(e, precios.referencia(e.r)) for e in entradas]
    refs = [(e, x) for e, x in refs if x]
    if not refs:
        return None
    e_ = _estilos()
    f = precios.fuente()

    def P(txt: object):
        return Paragraph(escape(str(txt)) if txt not in (None, "") else "—", e_["c"])

    el = [Paragraph("Precio de referencia de la zona", e_["h1"]),
          Paragraph(f"Precio medio al que se vendió la tierra rústica en la comarca de cada finca en {f['periodo']}, "
                    "según las compraventas declaradas a la Axencia Tributaria de Galicia. Sirve para situarse: es "
                    "la media de la comarca, no el valor de ninguna finca.", e_["p"]), Spacer(1, 2 * mm)]
    filas = [["Nº", "Finca", "Comarca", "Tipo de terreno hoy", "Precio medio de la comarca", "Equivale a"]]
    for e, x in refs:
        if e.r.catastro.clase == "Urbano":
            tipo, pm, ph = "Inmueble urbano", "No aplica (solo suelo rústico)", "—"
        elif not x["tipo"]:
            tipo, pm, ph = "Sin clasificar", "Ver la tabla de la comarca", "—"
        else:
            tipo, pm, ph = x["tipo"].capitalize(), precios.euros_m2(x["precio"]), precios.euros_ha(x["precio"])
        filas.append([str(e.n), P(e.etiqueta), P(x["comarca"]), P(tipo), P(pm), P(ph)])
    el.append(_tabla(filas, [9 * mm, 44 * mm, 28 * mm, 30 * mm, 38 * mm, 28 * mm]))
    comarcas = {x["comarca"]: x for _, x in refs}
    el += [Paragraph("Precios medios por comarca", e_["h2"]),
           _tabla([["Comarca", "Monte", "Tierra agraria (prados y cultivos)"]] +
                  [[k, f"{precios.euros_m2(v['monte'])}  ({precios.euros_ha(v['monte'])})",
                    f"{precios.euros_m2(v['agrario'])}  ({precios.euros_ha(v['agrario'])})"]
                   for k, v in sorted(comarcas.items())], [47 * mm, 65 * mm, 65 * mm]),
           Paragraph("Cómo leerlo", e_["h2"])]
    for txt in (
            "<b>Es una media de toda la comarca.</b> Dentro de ella los precios cambian mucho de una parroquia a otra "
            "y de una finca a otra: accesos, pendiente, tamaño, arbolado y cercanía a núcleos pesan más que la media.",
            "<b>No incluye el arbolado como tal.</b> Es el precio de la tierra en las compraventas declaradas; una "
            "finca con madera lista para cortar puede valer más, y una recién cortada o quemada, menos.",
            "<b>El tipo de terreno</b> se toma de lo que hay hoy según el Inventario Forestal (o del Catastro si la "
            "finca no figura en él). Si una finca mezcla monte y prado, mire los dos precios de su comarca.",
            "<b>No es una tasación</b> ni el valor a efectos de impuestos. El propio Observatorio advierte de que sus "
            "cifras son estimaciones orientativas."):
        el.append(Paragraph("• " + txt, e_["p"]))
    el += [Spacer(1, 2 * mm),
           Paragraph(f'Fuente: {escape(f["fuente"])}. <link href="{escape(f["url"])}" color="#0563C1"><u>Ver el '
                     "informe</u></link>.", e_["c"])]
    return _construir(el, titulo)


def _seccion_metodologia(titulo: str) -> bytes:
    from reportlab.platypus import Paragraph

    e_ = _estilos()
    el = [Paragraph("Anexo", e_["h3"]), Paragraph("Metodología y aviso legal", e_["h1"])]
    for t, txt in METODOLOGIA:
        el += [Paragraph(escape(t), e_["h3"]), Paragraph(escape(txt), e_["p"])]
    el += [Paragraph("Aviso legal", e_["h3"]), Paragraph(escape(AVISO_LEGAL), e_["p"]),
           Paragraph(f"Emitido por {escape(config.PIE_INFORME)}.", e_["c"])]
    return _construir(el, titulo)


def _seccion(elementos: list, titulo: str) -> Optional[bytes]:
    """Convierte la lista de elementos de un apartado (que empieza con un salto de página) en un PDF propio."""
    return _construir(elementos[1:], titulo) if elementos else None


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

    el = [PageBreak(), Paragraph("Cotejo con el Inventario Forestal Continuo de Galicia", e_["h1"]),
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


def _nums(es: Sequence[_Entrada]) -> str:
    return ", ".join(f"nº {e.n}" for e in es)


def _afecciones(entradas: List[_Entrada], e_) -> list:
    """Página 'Afecciones y limitaciones detectadas': resumen del lote con lo que ya consta en cada hoja.
    Primero lo que afecta a la titularidad (monte vecinal o público); después el resto. [] si no se consultó."""
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    consultadas = [e for e in entradas if e.r.aprovechamiento or e.r.inventario]
    if not consultadas:
        return []

    def P(txt: object):
        return Paragraph(escape(str(txt)) if txt not in (None, "") else "—", e_["c"])

    grupos: dict = {}     # (tipo, detalle, organismo) -> entradas
    for e in consultadas:
        for a in (e.r.aprovechamiento.afecciones if e.r.aprovechamiento else []):
            grupos.setdefault((a["tipo"], a["detalle"], a["organismo"]), []).append(e)
    for e in entradas:   # un derecho minero vigente sobre la finca también la condiciona
        for x in (e.r.geologia.derechos_parcela if e.r.geologia else []):
            grupos.setdefault(("Derecho minero vigente sobre la finca",
                               f"{x['numero']} {x['nombre']} ({x['tipo']}; {x['recursos'] or 'recurso no indicado'})",
                               "Administración minera (Xunta de Galicia)"), []).append(e)

    def es_monte(k) -> bool:
        return any(p in (k[0] + " " + k[1]).lower() for p in ("vecinal", "veciñal", "utilidad pública", "utilidade"))
    montes = {k: v for k, v in grupos.items() if es_monte(k)}
    resto = {k: v for k, v in grupos.items() if not es_monte(k)}
    inv_vecinal = [e for e in consultadas if e.r.inventario and e.r.inventario.monte_vecinal]

    el = [PageBreak(), Paragraph("Afecciones y limitaciones detectadas", e_["h1"]),
          Paragraph("Resumen de lo que condiciona las fincas del lote según las capas públicas consultadas. "
                    "El detalle de cada finca figura en su hoja.", e_["p"]),
          Paragraph("Monte vecinal o de utilidad pública", e_["h3"])]
    if montes or inv_vecinal:
        filas = [["Qué consta", "Detalle", "Fincas", "A quién corresponde"]]
        filas += [[P(k[0]), P(k[1]), P(_nums(v)), P(k[2])] for k, v in montes.items()]
        if inv_vecinal:
            filas.append([P("Monte vecinal o público según el Inventario Forestal"), P("Marca del inventario"),
                          P(_nums(inv_vecinal)), P("Consellería do Medio Rural")])
        el += [_tabla(filas, [44 * mm, 52 * mm, 28 * mm, 51 * mm]),
               Paragraph("Afecta a quién es el titular de la finca: conviene aclararlo antes de cualquier operación.",
                         e_["c"])]
    else:
        el.append(Paragraph("Ninguna finca del lote figura como monte vecinal en mano común ni de utilidad pública "
                            "en las capas consultadas.", e_["p"]))
    el.append(Paragraph("Otras afecciones", e_["h3"]))
    if resto:
        el.append(_tabla([["Qué se ha detectado", "Detalle", "Fincas", "A quién corresponde"]] +
                         [[P(k[0]), P(k[1]), P(_nums(v)), P(k[2])] for k, v in resto.items()],
                         [44 * mm, 52 * mm, 28 * mm, 51 * mm]))
    else:
        el.append(Paragraph("No se detecta ninguna otra afección en las capas consultadas.", e_["p"]))
    limpias = [e for e in consultadas if e.r.aprovechamiento and not e.r.aprovechamiento.afecciones]
    if limpias:
        el.append(Paragraph(f"<b>Sin afecciones detectadas:</b> {escape(_nums(limpias))}.", e_["p"]))
    sin = [e for e in consultadas if e.r.aprovechamiento and e.r.aprovechamiento.sin_consultar]
    if sin:
        el.append(Paragraph(f"<b>Con alguna capa sin consultar:</b> {escape(_nums(sin))} (ver su hoja).", e_["p"]))
    el += [Spacer(1, 2 * mm),
           Paragraph("Orientativo: indica qué capa toca cada finca y a qué organismo corresponde, no si una "
                     "actuación está permitida. Que no se detecte una afección no garantiza que no exista.", e_["c"])]
    return el


def _anexo_construcciones(entradas: List[_Entrada], titulo: str) -> Optional[bytes]:
    """Anexo con los inmuebles construidos del lote (urbanos y construcciones en parcelas rústicas). None si no hay."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    e_ = _estilos()

    def P(txt: object):
        return Paragraph(escape(str(txt)) if txt not in (None, "") else "—", e_["c"])

    urbanos = [e for e in entradas if e.r.catastro and (e.r.catastro.construida_m2 or e.r.catastro.construcciones
                                                        or e.r.catastro.clase == "Urbano")]
    mixtas = [e for e in entradas if e.r.catastro and e.r.catastro.otros_inmuebles and e not in urbanos]
    if not urbanos and not mixtas:
        return None
    el = [Paragraph("Anexo", e_["h3"]), Paragraph("Construcciones e inmuebles urbanos", e_["h1"]),
          Paragraph("Inmuebles del lote que tienen construcciones dadas de alta en el Catastro: casas, naves, "
                    "almacenes, alpendres u otros edificios. Los números son los del índice de fincas.", e_["p"])]
    if urbanos:
        filas = [["Nº", "Inmueble", "Ref. catastral", "Uso", "Construido", "Año", "Destinos según el Catastro"]]
        for e in urbanos:
            c = e.r.catastro
            filas.append([str(e.n), P(c.localizacion or e.etiqueta), P(c.refcat), P(c.uso),
                          P(formato_m2(c.construida_m2) if c.construida_m2 else ""), P(c.anio),
                          P("; ".join(c.construcciones))])
        el += [Paragraph("Inmuebles construidos", e_["h2"]),
               _tabla(filas, [9 * mm, 38 * mm, 29 * mm, 22 * mm, 23 * mm, 12 * mm, 44 * mm])]
    if mixtas:
        el += [Paragraph("Fincas rústicas con construcciones", e_["h2"]),
               _tabla([["Nº", "Finca", "Inmuebles construidos en la misma parcela (referencia catastral)"]] +
                      [[str(e.n), P(e.etiqueta), P(", ".join(e.r.catastro.otros_inmuebles))] for e in mixtas],
                      [9 * mm, 60 * mm, 108 * mm]),
               Paragraph("El Catastro da de alta esas construcciones como inmuebles aparte dentro de la parcela. "
                         "Sus datos no se han consultado: pueden pedirse por su referencia.", e_["c"])]
    el += [Paragraph("Cómo leer estos datos", e_["h2"])]
    for txt in (
            "<b>Uso:</b> el uso principal que el Catastro asigna al inmueble (residencial, almacén, industrial...).",
            "<b>Construido:</b> suma de las superficies construidas de todas las plantas y dependencias. No es la "
            "superficie de la parcela, que figura en la ficha catastral de cada inmueble.",
            "<b>Año:</b> año de construcción que consta en el Catastro. Puede ser aproximado en edificaciones antiguas.",
            "<b>Destinos:</b> reparto de la superficie construida por destino (vivienda, almacén, aparcamiento...).",
            "<b>Estado de conservación:</b> el Catastro no lo indica. Una construcción puede figurar dada de alta y "
            "estar en ruina, o haberse reformado sin que conste; conviene comprobarlo en una visita.",
            "<b>Legalidad urbanística:</b> que una construcción figure en el Catastro no acredita que tenga licencia "
            "ni que sea legalizable. Eso lo informa el ayuntamiento."):
        el.append(Paragraph("• " + txt, e_["p"]))
    el += [Spacer(1, 2 * mm), Paragraph("Fuente: Catastro, consulta de datos no protegidos.", e_["c"])]
    return _construir(el, titulo)


def _anexo_geologia(entradas: List[_Entrada], titulo: str) -> Optional[bytes]:
    """Anexo geológico y minero de todo el lote, identificando cada finca por su número. None si no se consultó."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    con = [e for e in entradas if e.r.geologia]
    if not con:
        return None
    e_ = _estilos()

    def P(txt: object):
        return Paragraph(escape(str(txt)) if txt not in (None, "") else "—", e_["c"])

    def agrupar(clave) -> List[Tuple[str, List[_Entrada]]]:
        g: dict = {}
        for e in con:
            g.setdefault(clave(e.r.geologia), []).append(e)
        return list(g.items())

    radio = con[0].r.geologia.radio_km
    el = [Paragraph("Anexo", e_["h3"]), Paragraph("Informe geológico y minero", e_["h1"]),
          Paragraph(f"Contexto geológico y minero de las fincas del lote, consultado por el centro de cada una y un "
                    f"radio de {radio:g} km. Los números son los del índice de fincas. Es orientativo: describe "
                    "la zona, no acredita que haya mineral en ninguna finca.", e_["p"]),
          Paragraph("Valoración", e_["h2"])]
    el.append(_tabla([["Fincas", "Valoración"]] +
                     [[P(_nums(es)), P(v)] for v, es in agrupar(lambda g: g.veredicto.split("; el más cercano")[0])],
                     [35 * mm, 142 * mm]))

    filas = [["Nº", "Finca", "Ref. catastral", "Hoja MAGNA", "Litología bajo la finca", "Derechos mineros sobre la finca"]]
    for e in con:
        g = e.r.geologia
        filas.append([str(e.n), P(e.etiqueta), P(e.refcat), P(g.hoja or "sin verificar"),
                      P(g.litologia or "sin verificar"),
                      P(texto_derechos(g.derechos_parcela) if g.derechos_consultados else "Sin verificar")])
    el += [Paragraph("Geología y derechos mineros, finca a finca", e_["h2"]),
           _tabla(filas, [9 * mm, 36 * mm, 29 * mm, 22 * mm, 45 * mm, 36 * mm]),
           Paragraph("Hoja y litología: Mapa Geológico de España 1:50.000 (MAGNA, IGME-CSIC). Derechos mineros: "
                     "solo los vigentes que publica la Xunta de Galicia; no incluye solicitudes en tramitación.",
                     e_["c"])]

    indicios: dict = {}      # código -> (indicio, distancia mínima, entrada más cercana)
    for e in con:
        for i in e.r.geologia.indicios:
            k = i.get("codigo") or (i["nombre"], i["sustancia"])
            if k not in indicios or i["distancia_m"] < indicios[k][1]:
                indicios[k] = (i, i["distancia_m"], e)
    el.append(Paragraph(f"Indicios mineros a menos de {radio:g} km", e_["h2"]))
    if indicios:
        orden = sorted(indicios.values(), key=lambda x: x[1])
        el.append(_tabla([["Indicio (BDMIN, IGME)", "Sustancias", "Tipo", "Tamaño", "Distancia", "Finca más cercana"]] +
                         [[P(i["nombre"]), P(str(i["sustancia"]).strip()), P(i["morfologia"]), P(i["tamano"]),
                           km(d), f"nº {e.n}"] for i, d, e in orden[:30]],
                         [48 * mm, 44 * mm, 22 * mm, 25 * mm, 19 * mm, 19 * mm]))
        if len(orden) > 30:
            el.append(Paragraph(f"… y {len(orden) - 30} más (ver Excel).", e_["c"]))
        el.append(Paragraph("Un indicio es un punto donde consta presencia de mineral o una labor antigua; no "
                            "implica que sea explotable ni que continúe hasta las fincas. Distancia en línea recta.",
                            e_["c"]))
    else:
        el.append(Paragraph("La base de datos de indicios del IGME no recoge ninguno en ese radio.", e_["p"]))

    geoq = [(t, es) for t, es in agrupar(texto_geoquimica) if t]
    if geoq:
        el += [Paragraph("Atlas Geoquímico de España (sedimentos)", e_["h2"]),
               _tabla([["Fincas", "Tramo de la leyenda del atlas en el que cae la zona"]] +
                      [[P(_nums(es)), P(t)] for t, es in geoq], [35 * mm, 142 * mm]),
               Paragraph("El atlas (IGME, 2012) interpola muestras muy separadas: da una idea de la comarca, no una "
                         "medida en la finca. El tramo más alto indica una zona con valores altos de ese elemento.",
                         e_["c"])]

    cerca = {x["numero"] or x["nombre"]: x for e in con for x in e.r.geologia.derechos_cerca}
    canteras = {x.get("codigo") or x["sustancia"]: x for e in con for x in e.r.geologia.explotaciones}
    otros = []
    if any(e.r.geologia.derechos_consultados for e in con):
        otros.append([f"Derechos mineros vigentes a menos de {radio:g} km", P(texto_derechos(list(cerca.values())))])
    if canteras:
        otros.append(["Canteras y rocas industriales (BDMIN)", P("; ".join(
            f"{str(x['sustancia']).strip()} ({str(x['estado']).lower()})" for x in canteras.values()))])
    sin = sorted({s for e in con for s in e.r.geologia.sin_consultar})
    if sin:
        otros.append(["Sin consultar", P("No se pudo consultar: " + ", ".join(sin))])
    if otros:
        el += [Paragraph("Entorno minero", e_["h2"]), _tabla(otros, [55 * mm, 122 * mm], cabecera=False, clave=True)]

    patrimonio = [e for e in con if e.r.aprovechamiento and any("atrimonio cultural" in a["tipo"]
                                                                for a in e.r.aprovechamiento.afecciones)]
    el.append(Paragraph("A tener en cuenta", e_["h2"]))
    notas = ["Los yacimientos minerales son bienes de dominio público (Ley 22/1973, de Minas): la propiedad del "
             "suelo no da derecho a explotarlos, y cualquier investigación o explotación necesita título minero.",
             "No se han consultado sondeos, geofísica ni muestras: este anexo no sustituye a un estudio geológico."]
    if patrimonio:
        notas.insert(1, f"Las fincas {_nums(patrimonio)} tocan una zona de protección de patrimonio cultural: "
                        "cualquier cata o movimiento de tierras necesita autorización previa de la Dirección Xeral "
                        "de Patrimonio Cultural.")
    el += [Paragraph("• " + escape(n), e_["p"]) for n in notas]

    def enlace(url: str, txt: str) -> str:
        return f'<link href="{escape(url)}" color="#0563C1"><u>{txt}</u></link>'
    fechas = sorted({fecha_legible(e.r.geologia.consultado)[:10] for e in con if e.r.geologia.consultado})
    el += [Spacer(1, 2 * mm),
           Paragraph("Fuentes: IGME-CSIC (Mapa Geológico MAGNA, base de datos BDMIN, Atlas Geoquímico de España 2012) y "
                     "Xunta de Galicia (derechos mineros). Consultado: " + (", ".join(fechas[:2]) or "sin fecha") + ". "
                     + " · ".join([enlace(config.IGME_VISOR_URL, "Visor del IGME"), enlace(config.IGME_BDMIN_URL, "BDMIN"),
                                   enlace(config.CATASTRO_MINERO_URL, "Catastro Minero"),
                                   enlace(config.PBA_VISOR_URL, "Visor de la Xunta")]), e_["c"])]
    return _construir(el, titulo)


def puntos_a_comprobar(r: Resultado) -> List[str]:
    """Hechos objetivos de una finca que conviene aclarar antes de comprarla o venderla. Solo lo que consta en
    las fuentes consultadas; no valora ni recomienda. Lista vacía si no hay nada destacable."""
    f, c, inv, ap, geo = r.finca, r.catastro, r.inventario, r.aprovechamiento, r.geologia
    out: List[str] = []
    if not c:
        return ["No se obtuvieron datos del Catastro: " + ("; ".join(r.errores) or "sin detalle")]
    if not f.cuota.completa:
        out.append(f"Se transmite una cuota ({f.cuota.texto}), no la finca entera")
    if r.control_superficie == "REVISAR":
        out.append("La superficie no cuadra: " + explicar_control(c.superficie_m2, f.superficie_m2, f.cuota, "REVISAR"))
    if r.coincide == "NO":
        out.append("La referencia catastral del visor SIXPAC/SIGPAC no coincide con la del Catastro")
    elif r.coincide == "NO ENCONTRADA":
        out.append("La parcela no aparece en el visor SIXPAC/SIGPAC")
    if r.contraste == "REVISAR":
        out.append("Lo que declara el Catastro no coincide con lo que hay hoy: "
                   + (r.contraste_detalle or "ver la hoja de la finca"))
    if inv and inv.monte_vecinal:
        out.append("Figura como monte vecinal o público en el Inventario Forestal: aclarar la titularidad")
    vistos = set()
    for a in (ap.afecciones if ap else []):
        tipo = a["tipo"].split(" (")[0]
        if tipo not in vistos:
            vistos.add(tipo)
            detalles = "; ".join(dict.fromkeys(x["detalle"] for x in ap.afecciones if x["tipo"].split(" (")[0] == tipo))
            out.append(f"{tipo}: {detalles}. Organismo: {a['organismo']}"
                       if a["organismo"] != "Dato informativo" else f"{tipo}: {detalles}")
    if ap and ap.saca:
        mala = sum(v for k, v in ap.saca.items() if "esfavorable" in k)
        if mala >= 0.5:
            out.append(f"Saca de madera difícil en el {mala * 100:.0f} % de la finca (pendiente y accesos, mapa de la Xunta)")
    if ap and ap.sin_consultar:
        out.append("Capas de afecciones sin consultar: " + ", ".join(ap.sin_consultar))
    for x in (geo.derechos_parcela if geo else []):
        out.append(f"Derecho minero vigente sobre la finca: {x['numero']} {x['nombre']} ({x['tipo']})")
    if r.colindantes is not None and not r.colindantes.dominio_publico:
        out.append("No consta que linde con camino o vía pública según SIXPAC: comprobar el acceso")
    if c.otros_inmuebles:
        out.append("Hay construcciones dadas de alta en la misma parcela: " + ", ".join(c.otros_inmuebles))
    if c.clase == "Urbano":
        out.append("Estado de conservación y situación urbanística de la construcción: no constan en el Catastro")
    return out


def _anexo_compraventa(entradas: List[_Entrada], titulo: str) -> Optional[bytes]:
    """Anexo 'Resumen para la compraventa': por finca, qué es y qué conviene aclarar. Solo hechos de las fuentes."""
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, Spacer

    if not any(e.r.catastro for e in entradas):
        return None
    e_ = _estilos()

    def P(txt: object):
        return Paragraph(escape(str(txt)) if txt not in (None, "") else "—", e_["c"])

    el = [Paragraph("Resumen para la compraventa", e_["h1"]),
          Paragraph("Lo esencial de cada finca en una ficha: qué es, qué hay hoy sobre el terreno y qué conviene "
                    "aclarar antes de comprarla o venderla. Solo recoge hechos que constan en las fuentes públicas "
                    "consultadas; no valora la finca ni recomienda la operación. Los números son los del índice.",
                    e_["p"])]
    for e in entradas:
        r, f, c, inv = e.r, e.r.finca, e.r.catastro, e.r.inventario
        filas = []
        if c:
            filas.append(["Referencia catastral", P(c.refcat)])
            if c.superficie_m2:
                filas.append(["Superficie catastral", P(f"{formato_m2(c.superficie_m2)}  ({formato_ha(c.superficie_m2)})")])
            filas.append(["Según el Catastro", P(c.uso_detallado)])
            if inv:
                filas.append([f"Sobre el terreno hoy ({inv.anio})", P(texto_reparto(inv))])
        puntos = puntos_a_comprobar(r)
        filas.append(["A aclarar antes de la operación",
                      [Paragraph("• " + escape(x), e_["c"]) for x in puntos] if puntos
                      else P("Nada destacable en las fuentes consultadas")])
        nombre = f"{e.n}. {e.etiqueta}" + (f" — {f.paraje}" if f.paraje else "")
        el.append(KeepTogether([Paragraph(escape(nombre), e_["h2"]),
                                _tabla(filas, [50 * mm, 127 * mm], cabecera=False, clave=True)]))
    el += [Spacer(1, 4 * mm),
           Paragraph("Qué no cubre este resumen: quién es el titular y si la finca tiene cargas (hipotecas, embargos, "
                     "servidumbres), que constan en el Registro de la Propiedad; los linderos reales sobre el terreno; "
                     "y el valor de la finca. Que no se detecte una afección no garantiza que no exista.", e_["c"])]
    return _construir(el, titulo)


def _anexo_fuentes(entradas: List[_Entrada], titulo: str) -> Optional[bytes]:
    """Anexo final 'Fuentes y fechas de consulta': una fila por finca con cuándo se consultó cada fuente y el
    enlace para verificarla en la Sede del Catastro. None si no hay ninguna finca consultada."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    con = [e for e in entradas if e.r.catastro]
    if not con:
        return None
    e_ = _estilos()

    def P(txt: object):
        return Paragraph(escape(str(txt)) if txt not in (None, "") else "—", e_["c"])

    def enlace(url: str, txt: str) -> str:
        return f'<link href="{escape(url)}" color="#0563C1"><u>{txt}</u></link>'

    hay_inv = any(e.r.inventario for e in con)
    hay_ap = any(e.r.aprovechamiento for e in con)
    hay_geo = any(e.r.geologia for e in con)
    cab = ["Nº", "Finca", "Catastro", "Plano SIXPAC / SIGPAC"]
    cab += (["Inventario forestal"] if hay_inv else []) + (["Afecciones"] if hay_ap else [])
    cab += (["Geología y minería"] if hay_geo else []) + ["Verificar"]
    filas = [cab]
    for e in con:
        r, s = e.r, e.r.sigpac
        url = _enlace_sede(r)
        fila = [str(e.n), P(e.etiqueta), P(fecha_legible(r.catastro.consultado) or "sin fecha"),
                P(fecha_legible(s.consultado) if s and s.estado == "ENCONTRADA" else "no obtenido")]
        if hay_inv:
            fila.append(P(fecha_legible(r.inventario.consultado) if r.inventario else ""))
        if hay_ap:
            fila.append(P(fecha_legible(r.aprovechamiento.consultado) if r.aprovechamiento else ""))
        if hay_geo:
            fila.append(P(fecha_legible(r.geologia.consultado) if r.geologia else ""))
        fila.append(Paragraph(enlace(url, "Sede del Catastro"), e_["c"]) if url else P(""))
        filas.append(fila)
    n_fechas = len(cab) - 3
    ancho_fecha = (177 - 9 - 36 - 24) / n_fechas
    el = [Paragraph("Anexo", e_["h3"]), Paragraph("Fuentes y fechas de consulta", e_["h1"]),
          Paragraph("Fecha y hora en que se consultó cada fuente para cada finca. Los datos reflejan lo que publicaba "
                    "cada organismo en ese momento. El enlace abre la ficha pública del inmueble en la Sede "
                    "Electrónica del Catastro, donde puede comprobarse.", e_["p"]),
          Spacer(1, 2 * mm),
          _tabla(filas, [9 * mm, 36 * mm] + [ancho_fecha * mm] * n_fechas + [24 * mm]),
          Paragraph("Fuentes", e_["h2"])]
    anios = sorted({e.r.inventario.anio for e in con if e.r.inventario and e.r.inventario.anio})
    fuentes = [("Catastro", "Dirección General del Catastro: consulta de datos no protegidos y ficha descriptiva y "
                            "gráfica de la Sede Electrónica (la fecha de la ficha va impresa en ella)."),
               ("Plano", "Visor SIXPAC de la Xunta de Galicia (en Galicia) o SIGPAC del FEGA: ortofoto, parcelario "
                         "y referencia catastral asociada.")]
    if hay_inv:
        fuentes.append(("Inventario forestal", "Inventario Forestal Continuo de Galicia (Xunta de Galicia)"
                        + (f", edición {anios[-1]}" if anios else "") + ". "
                        + enlace(config.INVEGA_VISOR_URL, "Visor del inventario") + "."))
    if hay_ap:
        fuentes.append(("Afecciones", "Capas de afecciones y de restricciones de la Xunta de Galicia (IDEG). "
                        + enlace(config.PBA_VISOR_URL, "Visor de la Xunta") + "."))
    if hay_geo:
        fuentes.append(("Geología y minería", "IGME-CSIC (Mapa Geológico MAGNA, base de datos BDMIN, Atlas Geoquímico "
                        "de España 2012) y derechos mineros de la Xunta de Galicia. "
                        + enlace(config.IGME_VISOR_URL, "Visor del IGME") + " · "
                        + enlace(config.CATASTRO_MINERO_URL, "Catastro Minero") + "."))
    el += [Paragraph(f"• <b>{k}:</b> {v}", e_["p"]) for k, v in fuentes]
    el += [Spacer(1, 2 * mm)]
    return _construir(el, titulo)


def _enlace_sede(r: Resultado) -> str:
    c, cod = r.catastro, r.codigos
    if not c or not cod:
        return ""
    return config.SEDE_CONSULTA_URL.format(
        prov=cod.cod_provincia, mun=cod.cod_municipio_catastro,
        rc20=c.referencias_completas[0] if c.referencias_completas else c.refcat)


def _hoja_finca(e: _Entrada, total: int, titulo: str) -> bytes:
    """Hoja de la finca: datos, comprobaciones e incidencias. Las fuentes y fechas van en un anexo final."""
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
         ["Coincide con Catastro", P(r.coincide)]]
        + ([["Linda con", P(texto_colindantes(r.colindantes) + (
            ". También con terreno de dominio público (camino, vía o cauce)" if r.colindantes.dominio_publico
            else ". No consta que linde con camino o vía pública") + f" (SIXPAC {r.colindantes.anio})")]]
           if r.colindantes else []),
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

    # Orden del documento: frontal (portada, contenido e índice) · apartados previos · fincas · anexos.
    # Cada pieza es ("propia", título, bytes) o ("externa", título, ruta). Las páginas se numeran relativas a la
    # primera que sigue al frontal; el frontal se genera al final, cuando ya se sabe cuánto ocupa todo.
    e_ = _estilos()
    previas = [("propia", "Resumen ejecutivo", _resumen_ejecutivo(entradas, titulo))]
    previas += [("externa", nombre, ruta) for nombre, ruta, _ in extras_ok]
    previas += [("propia", "Resumen para la compraventa", _anexo_compraventa(entradas, titulo)),
                ("propia", "Precio de referencia de la zona", _seccion_precios(entradas, titulo)),
                ("propia", "Afecciones y limitaciones detectadas", _seccion(_afecciones(entradas, e_), titulo)),
                ("propia", "Cotejo con el Inventario Forestal de Galicia", _seccion(_cotejo(entradas, e_), titulo))]
    finales = [("propia", "Anexo. Construcciones e inmuebles urbanos", _anexo_construcciones(entradas, titulo)),
               ("propia", "Anexo. Informe geológico y minero", _anexo_geologia(entradas, titulo)),
               ("propia", "Anexo. Fuentes y fechas de consulta", _anexo_fuentes(entradas, titulo)),
               ("propia", "Anexo. Metodología y aviso legal", _seccion_metodologia(titulo))]

    def lector(pieza):
        return PdfReader(io.BytesIO(pieza[2])) if pieza[0] == "propia" else PdfReader(str(pieza[2]))

    previas = [x for x in previas if x[2]]
    finales = [x for x in finales if x[2]]
    contenido: List[Tuple[str, int]] = []
    pagina = 1
    for pieza in previas:
        contenido.append((pieza[1], pagina))
        pagina += len(lector(pieza).pages)
    contenido.append(("Fincas, una a una", pagina))
    for e in entradas:
        e.hoja = len(PdfReader(io.BytesIO(_hoja_finca(e, len(entradas), titulo))).pages)
        e.inicio = pagina                 # relativo a la primera página tras el frontal
        pagina += e.n_paginas
    for pieza in finales:
        contenido.append((pieza[1], pagina))
        pagina += len(lector(pieza).pages)
    frontal_n = len(PdfReader(io.BytesIO(_frontal(resultados, entradas, titulo, meta, 0, contenido))).pages)
    for _ in range(2):
        frontal = _frontal(resultados, entradas, titulo, meta, frontal_n, contenido)
        real = len(PdfReader(io.BytesIO(frontal)).pages)
        if real == frontal_n:
            break
        frontal_n = real

    # (página, ¿externa?) en orden, y marcadores (título, índice de página)
    paginas: List[Tuple[object, bool]] = [(p, False) for p in PdfReader(io.BytesIO(frontal)).pages]
    marcadores: List[Tuple[str, int]] = [("Portada y contenido", 0)]
    for pieza in previas:
        marcadores.append((pieza[1], len(paginas)))
        paginas += [(p, pieza[0] == "externa") for p in lector(pieza).pages]
    for e in entradas:
        marcadores.append((f"{e.n}. {e.etiqueta}", len(paginas)))
        paginas += [(p, False) for p in PdfReader(io.BytesIO(_hoja_finca(e, len(entradas), titulo))).pages]
        for ruta, _ in e.paginas:
            paginas += [(p, True) for p in PdfReader(str(ruta)).pages]
    for pieza in finales:
        marcadores.append((pieza[1], len(paginas)))
        paginas += [(p, False) for p in lector(pieza).pages]

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

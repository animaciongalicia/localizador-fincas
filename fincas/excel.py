"""Generación de resultados.xlsx: hoja Resumen + hoja Fincas."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import os

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import config
from .modelos import Meta, Resultado, fecha_legible
from .aprovechamiento import resumen_afecciones, texto_clases
from .inventario import evolucion, texto_reparto
from .mapa import Conjunto, formato_dist, matriz_distancias
from .resumen import calcular, formato_ha, formato_m2
from .superficie import explicar_control

COLUMNAS = [
    ("municipio", 22), ("paraje", 22), ("polígono", 9), ("parcela", 9), ("cuota", 10),
    ("referencia catastral", 22), ("superficie catastral (m²)", 14), ("uso/cultivo", 28),
    ("superficie del cliente (m²)", 14), ("control de superficie", 14),
    ("detalle del control", 44),
    ("ref. catastral SIGPAC", 22), ("coincide con Catastro", 14),
    ("cobertura actual (inventario forestal)", 18), ("detalle cobertura", 48), ("edición inventario", 10),
    ("contraste con cultivo catastral", 15),
    ("afecciones para una corta", 30), ("organismos", 40), ("dificultad de saca", 22),
    ("PDF Catastro", 36), ("PDF SIGPAC", 36), ("ficha web Catastro", 18),
    ("consulta Catastro", 17), ("consulta SIGPAC", 17), ("notas", 50),
]
AMARILLO = PatternFill("solid", fgColor="FFF2CC")
ROJO = PatternFill("solid", fgColor="F8CBAD")
VERDE = PatternFill("solid", fgColor="C6E0B4")
CABECERA = PatternFill("solid", fgColor="1F4E78")
GRIS = PatternFill("solid", fgColor="D9E1F2")
ENLACE = Font(color="0563C1", underline="single")


def _enlace_sede(r: Resultado) -> str:
    c, cod = r.catastro, r.codigos
    if not c or not cod:
        return ""
    url = config.SEDE_CONSULTA_URL.format(
        prov=cod.cod_provincia, mun=cod.cod_municipio_catastro,
        rc20=c.referencias_completas[0] if c.referencias_completas else c.refcat)
    return url.replace("UrbRus=R", "UrbRus=U") if c.clase == "Urbano" else url


def _fila(r: Resultado) -> List[object]:
    f, c, s = r.finca, r.catastro, r.sigpac
    notas = " | ".join(x for x in [f.notas, *r.avisos, *r.errores] if x)
    detalle = explicar_control(c.superficie_m2 if c else None, f.superficie_m2, f.cuota,
                               r.control_superficie)
    inv, ap = r.inventario, r.aprovechamiento
    det_inv = ""
    if inv:
        det_inv = " | ".join(x for x in [
            texto_reparto(inv), f"Evolución: {evolucion(inv)}" if evolucion(inv) else "",
            f"Volumen estimado provisional: {inv.volumen_m3:,.0f} m³".replace(",", ".") if inv.volumen_m3 else "",
            "Figura como monte vecinal o público en el inventario" if inv.monte_vecinal else "",
            r.contraste_detalle] if x)
    return [_seguro(x) for x in [
        f.municipio, f.paraje or (c.paraje if c else ""), f.poligono, f.parcela, f.cuota.texto,
        c.refcat if c else "", c.superficie_m2 if c else None, c.uso_detallado if c else "",
        f.superficie_m2, r.control_superficie, detalle,
        s.refcat if s else "", r.coincide,
        inv.dominante if inv else "", det_inv, inv.anio if inv else None, r.contraste,
        resumen_afecciones(ap) if ap else "",
        "; ".join(dict.fromkeys(a["organismo"] for a in ap.afecciones)) if ap else "",
        texto_clases(ap.saca) if ap else "",
        r.pdf_catastro, s.pdf if s else "", "Abrir en la Sede" if _enlace_sede(r) else "",
        fecha_legible(c.consultado) if c else "", fecha_legible(s.consultado) if s else "", notas,
    ]]


def _seguro(v: object) -> object:
    """Texto del cliente/errores: sin caracteres de control (openpyxl falla) ni fórmulas (=, +, -, @)."""
    if not isinstance(v, str):
        return v
    v = ILLEGAL_CHARACTERS_RE.sub("", v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


def _hoja_fincas(ws, resultados: List[Resultado]) -> None:
    ws.append([n for n, _ in COLUMNAS])
    for i, (_, ancho) in enumerate(COLUMNAS, start=1):
        celda = ws.cell(row=1, column=i)
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = CABECERA
        celda.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = ancho

    nombres = [n for n, _ in COLUMNAS]
    col = {n: nombres.index(n) + 1 for n in nombres}

    for r in resultados:
        ws.append(_fila(r))
        n = ws.max_row
        if not r.finca.cuota.completa:  # la cuota es una marca, no un filtro
            for c in range(1, len(COLUMNAS) + 1):
                ws.cell(row=n, column=c).fill = AMARILLO
        ctrl = ws.cell(row=n, column=col["control de superficie"])
        if r.control_superficie == "REVISAR":
            ctrl.fill = ROJO
        elif r.control_superficie == "OK":
            ctrl.fill = VERDE
        co = ws.cell(row=n, column=col["coincide con Catastro"])
        if r.coincide in ("NO", "NO ENCONTRADA"):
            co.fill = ROJO
        elif r.coincide == "SI":
            co.fill = VERDE
        ct = ws.cell(row=n, column=col["contraste con cultivo catastral"])
        if r.contraste == "REVISAR":
            ct.fill = ROJO
        elif r.contraste == "COHERENTE":
            ct.fill = VERDE
        # enlaces: PDF (ruta relativa al Excel) y ficha web de la Sede
        for nombre, carpeta in (("PDF Catastro", "pdf_catastro"), ("PDF SIGPAC", "pdf_sigpac")):
            cel = ws.cell(row=n, column=col[nombre])
            if cel.value:
                cel.hyperlink = f"{carpeta}/{cel.value}"
                cel.font = ENLACE
        url = _enlace_sede(r)
        if url:
            cel = ws.cell(row=n, column=col["ficha web Catastro"])
            cel.hyperlink = url
            cel.font = ENLACE
        for nombre in ("detalle del control", "detalle cobertura", "notas"):
            ws.cell(row=n, column=col[nombre]).alignment = Alignment(wrap_text=True, vertical="top")

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _hoja_resumen(ws, resultados: List[Resultado], titulo: str, meta: Optional[Meta] = None) -> None:
    rs = calcular(resultados)
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 20

    def titulo_seccion(txt: str) -> None:
        ws.append([])
        ws.append([txt])
        for c in range(1, 4):
            ws.cell(row=ws.max_row, column=c).fill = GRIS
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)

    ws.append([_seguro(titulo)])
    ws["A1"].font = Font(bold=True, size=14)
    if meta and (meta.expediente or meta.cliente):
        ws.append([_seguro(config.MARCA)])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, color=config.MARCA_COLOR.lstrip("#"))
        ws.append(["Expediente", _seguro(meta.expediente or "—")])
        ws.append(["Cliente", _seguro(meta.cliente)])
        ws.append(["Fecha de emisión", meta.fecha])
    titulo_seccion("Totales")
    ws.append(["Fincas del lote", rs.total])
    ws.append(["Con datos del Catastro", rs.con_catastro])
    ws.append(["Superficie catastral total", formato_m2(rs.superficie_m2), formato_ha(rs.superficie_m2)])
    ws.append(["Fincas con cuota distinta de completa", rs.cuotas_parciales])
    ws.append(["Fincas con algún error o dato pendiente", rs.con_errores])

    titulo_seccion("Control de superficie")
    ws.append(["Concepto", "Fincas"])
    for k in ("OK", "REVISAR", "SIN DATO CLIENTE"):
        if rs.control.get(k):
            ws.append([k, rs.control[k]])
            ws.cell(row=ws.max_row, column=1).fill = {"OK": VERDE, "REVISAR": ROJO}.get(k, AMARILLO)

    titulo_seccion("Coincidencia Catastro / SIGPAC-SIXPAC")
    ws.append(["Concepto", "Fincas"])
    for k, v in sorted(rs.coincide.items()):
        ws.append([k, v])
        ws.cell(row=ws.max_row, column=1).fill = VERDE if k == "SI" else ROJO

    if rs.por_cobertura:
        titulo_seccion("Cobertura actual según el Inventario Forestal de Galicia")
        ws.append([f"Edición {rs.edicion_inventario} (teledetección, orientativo)", "Fincas donde domina", "Superficie"])
        for nombre, n, m2 in rs.por_cobertura:
            ws.append([nombre, n, formato_m2(m2)])
        titulo_seccion("Cultivo del Catastro frente a cobertura actual")
        ws.append(["Concepto", "Fincas"])
        for k in ("COHERENTE", "REVISAR", "NO COMPARABLE", "SIN DATO"):
            if rs.contraste.get(k):
                ws.append([k, rs.contraste[k]])
                ws.cell(row=ws.max_row, column=1).fill = {"COHERENTE": VERDE, "REVISAR": ROJO}.get(k, AMARILLO)

    titulo_seccion("Por municipio")
    ws.append(["Municipio", "Fincas", "Superficie catastral"])
    for nombre, n, m2 in rs.por_municipio:
        ws.append([_seguro(nombre), n, formato_m2(m2)])

    titulo_seccion("Por uso / cultivo")
    ws.append(["Uso", "Fincas", "Superficie catastral"])
    for nombre, n, m2 in rs.por_uso:
        ws.append([_seguro(nombre), n, formato_m2(m2)])


def _hoja_distancias(ws, conjunto: Conjunto) -> None:
    """Matriz de distancias en línea recta entre fincas (km), por número de finca."""
    pts = sorted(conjunto.puntos, key=lambda p: p.n)
    m = matriz_distancias(pts)
    ws.append(["Distancias en línea recta entre centros de parcela (km). Nº = orden de la finca en el lote."])
    ws["A1"].font = Font(bold=True)
    ws.append(["Nº", "Finca"] + [p.n for p in pts])
    for i in range(1, len(pts) + 3):
        c = ws.cell(row=2, column=i)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = CABECERA
    ws.column_dimensions["B"].width = 30
    for i, p in enumerate(pts):
        ws.append([p.n, _seguro(p.etiqueta)] + [round(d / 1000, 2) for d in m[i]])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    ws.freeze_panes = "C3"


def escribir_excel(resultados: List[Resultado], ruta: Path, titulo: Optional[str] = None,
                   conjunto: Optional[Conjunto] = None, meta: Optional[Meta] = None) -> Path:
    wb = Workbook()
    ws_res = wb.active
    ws_res.title = "Resumen"
    _hoja_resumen(ws_res, resultados, titulo or "Resumen del lote", meta)
    _hoja_fincas(wb.create_sheet("Fincas"), resultados)
    if conjunto and len(conjunto.puntos) >= 2:
        _hoja_distancias(wb.create_sheet("Distancias"), conjunto)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    wb.save(tmp)
    os.replace(tmp, ruta)
    return ruta

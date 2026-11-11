"""Generación de resultados.xlsx."""
from __future__ import annotations

from pathlib import Path
from typing import List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .modelos import Resultado

COLUMNAS = [
    ("municipio", 22), ("paraje", 22), ("polígono", 9), ("parcela", 9), ("cuota", 10),
    ("referencia catastral", 22), ("superficie catastral (m²)", 14), ("uso/cultivo", 28),
    ("superficie del cliente (m²)", 14), ("control de superficie", 14),
    ("ref. catastral SIGPAC", 22), ("coincide con Catastro", 14),
    ("PDF Catastro", 36), ("PDF SIGPAC", 36), ("notas", 50),
]
AMARILLO = PatternFill("solid", fgColor="FFF2CC")
ROJO = PatternFill("solid", fgColor="F8CBAD")
VERDE = PatternFill("solid", fgColor="C6E0B4")
CABECERA = PatternFill("solid", fgColor="1F4E78")


def _fila(r: Resultado) -> List[object]:
    f, c, s = r.finca, r.catastro, r.sigpac
    notas = " | ".join(x for x in [f.notas, *r.avisos, *r.errores] if x)
    return [
        f.municipio, f.paraje or (c.paraje if c else ""), f.poligono, f.parcela, f.cuota.texto,
        c.refcat if c else "", c.superficie_m2 if c else None, c.uso if c else "",
        f.superficie_m2, r.control_superficie,
        s.refcat if s else "", r.coincide,
        r.pdf_catastro, s.pdf if s else "", notas,
    ]


def escribir_excel(resultados: List[Resultado], ruta: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Fincas"
    ws.append([n for n, _ in COLUMNAS])
    for i, (_, ancho) in enumerate(COLUMNAS, start=1):
        celda = ws.cell(row=1, column=i)
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = CABECERA
        celda.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = ancho

    col_control = [n for n, _ in COLUMNAS].index("control de superficie") + 1
    col_coincide = [n for n, _ in COLUMNAS].index("coincide con Catastro") + 1

    for r in resultados:
        ws.append(_fila(r))
        n = ws.max_row
        if not r.finca.cuota.completa:  # la cuota es una marca, no un filtro
            for col in range(1, len(COLUMNAS) + 1):
                ws.cell(row=n, column=col).fill = AMARILLO
        ctrl = ws.cell(row=n, column=col_control)
        if r.control_superficie == "REVISAR":
            ctrl.fill = ROJO
        elif r.control_superficie == "OK":
            ctrl.fill = VERDE
        co = ws.cell(row=n, column=col_coincide)
        if r.coincide in ("NO", "NO ENCONTRADA"):
            co.fill = ROJO
        elif r.coincide == "SI":
            co.fill = VERDE
        ws.cell(row=n, column=len(COLUMNAS)).alignment = Alignment(wrap_text=True, vertical="top")

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ruta.parent.mkdir(parents=True, exist_ok=True)
    wb.save(ruta)
    return ruta

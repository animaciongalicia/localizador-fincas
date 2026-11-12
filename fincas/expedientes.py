"""Número de expediente correlativo por año (FER-2026-0001) y libro de expedientes emitidos.

El libro es un CSV (por defecto en la carpeta de clientes, fuera del repositorio): una línea por
expediente con fecha, cliente y lote. Sirve de registro de trazabilidad: qué informe se emitió y cuándo.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

CABECERA = ["expediente", "fecha", "cliente", "lote"]


def siguiente(libro: Path, cliente: str = "", lote: str = "", prefijo: str = "FER",
              ahora: Optional[datetime] = None) -> str:
    """Reserva y anota el siguiente número del año en curso. Nunca reutiliza un número."""
    ahora = ahora or datetime.now()
    libro = Path(libro)
    patron = re.compile(rf"^{re.escape(prefijo)}-{ahora.year}-(\d+)$")
    ultimo = 0
    if libro.exists():
        with open(libro, newline="", encoding="utf-8-sig") as fh:
            for fila in csv.reader(fh, delimiter=";"):
                m = patron.match(fila[0].strip()) if fila else None
                if m:
                    ultimo = max(ultimo, int(m.group(1)))
    numero = f"{prefijo}-{ahora.year}-{ultimo + 1:04d}"
    libro.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not libro.exists() or libro.stat().st_size == 0
    with open(libro, "a", newline="", encoding="utf-8-sig" if nuevo else "utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        if nuevo:
            w.writerow(CABECERA)
        w.writerow([numero, ahora.strftime("%Y-%m-%d %H:%M"), cliente, lote])
    return numero

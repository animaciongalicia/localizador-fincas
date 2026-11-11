"""Caché en disco: datos (JSON) y PDFs ya descargados. Solo se guardan éxitos."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional


class Cache:
    def __init__(self, raiz: Path, activa: bool = True):
        self.raiz = Path(raiz)
        self.activa = activa

    def _ruta(self, ns: str, clave: str, ext: str) -> Path:
        return self.raiz / ns / f"{clave}{ext}"

    # --- datos
    def leer(self, ns: str, clave: str) -> Optional[dict]:
        p = self._ruta(ns, clave, ".json")
        if not self.activa or not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def guardar(self, ns: str, clave: str, datos: dict) -> None:
        p = self._ruta(ns, clave, ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- ficheros
    def fichero(self, ns: str, clave: str, ext: str = ".pdf") -> Optional[Path]:
        p = self._ruta(ns, clave, ext)
        return p if self.activa and p.exists() and p.stat().st_size > 0 else None

    def guardar_fichero(self, ns: str, clave: str, origen: Path, ext: str = ".pdf") -> None:
        p = self._ruta(ns, clave, ext)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origen, p)

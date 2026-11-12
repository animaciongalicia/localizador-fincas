"""Caché en disco: datos (JSON) y PDFs ya descargados. Solo se guardan éxitos."""
from __future__ import annotations

import json
import os
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
            datos = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return datos if isinstance(datos, dict) else None

    def fecha(self, ns: str, clave: str) -> str:
        """Cuándo se guardó ese dato (ISO, minutos): es la fecha real de la consulta. '' si no existe."""
        from datetime import datetime
        try:
            return datetime.fromtimestamp(self._ruta(ns, clave, ".json").stat().st_mtime).isoformat(timespec="minutes")
        except OSError:
            return ""

    def borrar(self, ns: str, clave: str) -> None:
        for ext in (".json", ".pdf"):
            try:
                self._ruta(ns, clave, ext).unlink()
            except OSError:
                pass

    def guardar(self, ns: str, clave: str, datos: dict) -> None:
        p = self._ruta(ns, clave, ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    # --- ficheros
    def fichero(self, ns: str, clave: str, ext: str = ".pdf") -> Optional[Path]:
        p = self._ruta(ns, clave, ext)
        if not (self.activa and p.exists() and p.stat().st_size > 0):
            return None
        if ext == ".pdf":
            try:
                with open(p, "rb") as fh:
                    if fh.read(5) != b"%PDF-":
                        return None
            except OSError:
                return None
        return p

    def guardar_fichero(self, ns: str, clave: str, origen: Path, ext: str = ".pdf") -> None:
        p = self._ruta(ns, clave, ext)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        shutil.copyfile(origen, tmp)
        os.replace(tmp, p)

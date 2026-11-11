"""registro.txt: errores y parcelas a revisar a mano."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple


class Registro:
    def __init__(self, eco: Optional[Callable[[str], None]] = lambda m: print(m, flush=True)):
        self._lineas: List[str] = []
        self._revisar: List[Tuple[str, str]] = []
        self._eco = eco

    def info(self, msg: str) -> None:
        self._linea("INFO", msg)

    def aviso(self, finca: str, msg: str) -> None:
        self._linea("AVISO", f"{finca}: {msg}")
        self._revisar.append((finca, msg))

    def error(self, finca: str, msg: str) -> None:
        self._linea("ERROR", f"{finca}: {msg}")
        self._revisar.append((finca, f"ERROR - {msg}"))

    def _linea(self, nivel: str, msg: str) -> None:
        t = datetime.now().strftime("%H:%M:%S")
        linea = f"[{t}] {nivel:5} {msg}"
        self._lineas.append(linea)
        if self._eco:
            self._eco(linea)

    def escribir(self, ruta: Path) -> Path:
        partes = ["REGISTRO DE EJECUCIÓN", "=" * 60, *self._lineas, ""]
        partes += ["PARCELAS A REVISAR A MANO", "=" * 60]
        partes += [f"- {f}: {m}" for f, m in self._revisar] or ["(ninguna)"]
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_name(ruta.name + ".tmp")
        tmp.write_text("\n".join(partes) + "\n", encoding="utf-8")
        os.replace(tmp, ruta)
        return ruta

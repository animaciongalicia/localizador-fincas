"""Orquestación: lee fincas -> resuelve códigos -> Catastro -> SIGPAC -> Excel -> ZIP.

No conoce URLs ni selectores: solo habla con las interfaces de catastro.py y sigpac.py.
Los servicios se inyectan, así se puede probar entero con dobles (sin red).
"""
from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .cache import Cache
from .catastro import CatastroError, ParcelaNoEncontrada, RequiereCaptcha
from .codigos import Ambiguo, NoEncontrado
from .excel import escribir_excel
from .modelos import Codigos, DatosCatastro, Finca, Resultado, ResultadoSigpac
from .registro import Registro
from .sigpac import coincide_refcat
from .superficie import control_superficie
from .utils import slug


@dataclass
class Opciones:
    nombre_trabajo: str
    salidas: Path = Path("salidas")
    prueba: Optional[int] = None
    usar_sigpac: bool = True
    usar_pdf_catastro: bool = True
    fecha: Optional[str] = None


@dataclass
class Servicios:
    resolutor: object   # .resolver(provincia, municipio, preguntar) -> Codigos
    catastro: object    # .consulta_parcela(), .descargar_pdf()
    sigpac: Optional[object]  # .consultar()
    cache: Cache


def carpeta_trabajo(op: Opciones) -> Path:
    return Path(op.salidas) / f"{op.fecha or date.today().isoformat()}_{slug(op.nombre_trabajo)}"


def ejecutar(fincas: Sequence[Finca], op: Opciones, srv: Servicios, reg: Registro,
             preguntar: Optional[Callable[[str, Sequence[str]], str]] = None) -> Path:
    if op.prueba:
        fincas = list(fincas)[: op.prueba]
        reg.info(f"Modo prueba: solo las primeras {len(fincas)} parcelas")
    raiz = carpeta_trabajo(op)
    d_cat, d_sig = raiz / "pdf_catastro", raiz / "pdf_sigpac"
    d_cat.mkdir(parents=True, exist_ok=True)
    d_sig.mkdir(parents=True, exist_ok=True)
    reg.info(f"Carpeta de trabajo: {raiz}  ({len(fincas)} parcelas)")

    # 0) códigos: se resuelven todos antes, para preguntar las ambigüedades al principio
    codigos: Dict[Tuple[str, str], Optional[Codigos]] = {}
    fallos_codigos: Dict[Tuple[str, str], str] = {}
    for f in fincas:
        k = (f.provincia, f.municipio)
        if k in codigos:
            continue
        try:
            codigos[k] = srv.resolutor.resolver(f.provincia, f.municipio, preguntar)
            reg.info(f"{f.municipio} ({f.provincia}) -> {codigos[k].nombre_municipio} "
                     f"[{codigos[k].cod_provincia}{codigos[k].cod_municipio_ine}]")
        except Ambiguo:
            raise  # se para: lo decide el usuario
        except NoEncontrado as e:
            codigos[k] = None
            fallos_codigos[k] = str(e)
        except Exception as e:  # noqa: BLE001  (red caída al pedir el callejero, etc.)
            codigos[k] = None
            fallos_codigos[k] = f"No se pudieron resolver los códigos: {e}"

    resultados: List[Resultado] = []
    try:
        for i, f in enumerate(fincas, start=1):
            reg.info(f"[{i}/{len(fincas)}] {f.etiqueta}")
            res = Resultado(finca=f, codigos=codigos[(f.provincia, f.municipio)])
            resultados.append(res)
            try:
                _procesar(res, op, srv, d_cat, d_sig, reg)
            except Exception as e:  # noqa: BLE001  una parcela que falla no para las demás
                res.errores.append(f"Error inesperado: {e}")
                reg.error(f.etiqueta, f"Error inesperado: {e!r}")
            if res.codigos is None:
                msg = fallos_codigos[(f.provincia, f.municipio)]
                res.errores.append(msg)
                reg.error(f.etiqueta, msg)
    finally:
        escribir_excel(resultados, raiz / "resultados.xlsx")
        reg.escribir(raiz / "registro.txt")

    zip_base = raiz.parent / raiz.name
    shutil.make_archive(str(zip_base), "zip", root_dir=raiz.parent, base_dir=raiz.name)
    reg.info(f"ZIP: {zip_base}.zip")
    return raiz


def _procesar(res: Resultado, op: Opciones, srv: Servicios, d_cat: Path, d_sig: Path,
              reg: Registro) -> None:
    f, cod = res.finca, res.codigos
    if cod is None:
        return
    clave = f"{cod.clave}_{f.poligono}_{f.parcela}"
    base = f.nombre_base

    if not f.cuota.valida:
        res.avisos.append(f"Cuota '{f.cuota.texto}' no interpretable")
        reg.aviso(f.etiqueta, f"cuota '{f.cuota.texto}' no interpretable")

    # ---- Catastro: datos
    datos = _catastro_datos(res, srv, cod, clave, reg)
    res.catastro = datos
    if datos:
        res.avisos.extend(datos.avisos)
        for a in datos.avisos:
            reg.aviso(f.etiqueta, a)

    # ---- Catastro: PDF
    if datos and op.usar_pdf_catastro:
        destino = d_cat / f"{base}_catastro.pdf"
        en_cache = srv.cache.fichero("pdf_catastro", clave)
        try:
            if en_cache:
                shutil.copyfile(en_cache, destino)
            else:
                srv.catastro.descargar_pdf(datos, cod, destino)
                srv.cache.guardar_fichero("pdf_catastro", clave, destino)
            res.pdf_catastro = destino.name
        except RequiereCaptcha as e:
            res.errores.append(str(e))
            reg.error(f.etiqueta, f"PDF Catastro: {e}")
        except Exception as e:  # noqa: BLE001
            res.errores.append(f"PDF Catastro: {e}")
            reg.error(f.etiqueta, f"PDF Catastro: {e}")

    # ---- control de superficie
    res.control_superficie = control_superficie(datos.superficie_m2 if datos else None,
                                                f.superficie_m2, f.cuota)
    if res.control_superficie == "REVISAR":
        sup = datos.superficie_m2 if datos else None
        reg.aviso(f.etiqueta, f"superficie catastral {sup} vs cliente {f.superficie_m2} "
                              f"(cuota {f.cuota.texto}) -> REVISAR")

    # ---- SIGPAC
    if op.usar_sigpac and srv.sigpac is not None:
        res.sigpac = _sigpac(res, srv, cod, clave, d_sig, base, reg)
        res.coincide = coincide_refcat(datos.refcat if datos else "", res.sigpac.refcat)
        if res.sigpac.estado == "NO ENCONTRADA":
            res.coincide = "NO ENCONTRADA"
            reg.aviso(f.etiqueta, "no aparece en SIGPAC (¿monte/forestal?)")
        elif res.coincide == "NO":
            reg.aviso(f.etiqueta, f"referencia SIGPAC {res.sigpac.refcat or '(vacía)'} "
                                  f"distinta de Catastro {datos.refcat if datos else '(sin dato)'}")


def _catastro_datos(res, srv, cod, clave, reg) -> Optional[DatosCatastro]:
    f = res.finca
    cacheado = srv.cache.leer("catastro", clave)
    if cacheado:
        return DatosCatastro(**cacheado)
    try:
        datos = srv.catastro.consulta_parcela(cod, f.poligono, f.parcela)
    except ParcelaNoEncontrada as e:
        res.errores.append(f"Catastro: parcela no encontrada ({e})")
        reg.error(f.etiqueta, f"Catastro: parcela no encontrada ({e})")
        return None
    except CatastroError as e:
        res.errores.append(f"Catastro: {e}")
        reg.error(f.etiqueta, f"Catastro: {e}")
        return None
    except Exception as e:  # noqa: BLE001  (red, timeouts, tras los reintentos)
        res.errores.append(f"Catastro: {e}")
        reg.error(f.etiqueta, f"Catastro: {e!r}")
        return None
    srv.cache.guardar("catastro", clave, asdict(datos))
    return datos


def _sigpac(res, srv, cod, clave, d_sig, base, reg) -> ResultadoSigpac:
    f = res.finca
    destino = d_sig / f"{base}_sigpac.pdf"
    cacheado = srv.cache.leer("sigpac", clave)
    en_cache_pdf = srv.cache.fichero("pdf_sigpac", clave)
    if cacheado and en_cache_pdf:
        shutil.copyfile(en_cache_pdf, destino)
        return ResultadoSigpac(**cacheado)
    try:
        r = srv.sigpac.consultar(f, cod, res.catastro.refcat if res.catastro else "", destino)
    except Exception as e:  # noqa: BLE001
        res.errores.append(f"SIGPAC: {e}")
        reg.error(f.etiqueta, f"SIGPAC: {e}")
        return ResultadoSigpac("ERROR", detalle=str(e))
    if r.estado == "ENCONTRADA":
        srv.cache.guardar("sigpac", clave, asdict(r))
        if destino.exists():
            srv.cache.guardar_fichero("pdf_sigpac", clave, destino)
    return r

"""Orquestación: lee fincas -> resuelve códigos -> Catastro -> SIGPAC -> Excel -> ZIP.

No conoce URLs ni selectores: solo habla con las interfaces de catastro.py y sigpac.py.
Los servicios se inyectan, así se puede probar entero con dobles (sin red).
"""
from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .cache import Cache
from .catastro import CatastroError, ParcelaNoEncontrada, RequiereCaptcha
from .codigos import Ambiguo, NoEncontrado
from .excel import escribir_excel
from .informe import escribir_informe
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
    mapa: bool = True      # mapa de conjunto de todas las fincas (solo Galicia)
    informe: bool = True   # informe.pdf único con resumen, índice y todas las fichas
    fecha: Optional[str] = None


@dataclass
class Servicios:
    resolutor: object   # .resolver(provincia, municipio, preguntar) -> Codigos
    catastro: object    # .consulta_parcela(), .descargar_pdf()
    sigpac: Optional[object]  # .consultar()
    cache: Cache
    mapa: Optional[object] = None  # .generar(resultados, raiz, titulo) -> Conjunto | None
    usados: Dict[str, str] = field(default_factory=dict)  # nombre de fichero -> clave (detecta colisiones)


def carpeta_trabajo(op: Opciones) -> Path:
    return Path(op.salidas) / f"{op.fecha or date.today().isoformat()}_{slug(op.nombre_trabajo)}"


def carpeta_libre(op: Opciones) -> Path:
    """Como carpeta_trabajo, pero si ya existe (con contenido) añade _2, _3... para no mezclar ni pisar lotes."""
    base = carpeta_trabajo(op)
    if not base.exists() or not any(base.iterdir()):
        return base
    n = 2
    while True:
        cand = base.with_name(f"{base.name}_{n}")
        if not cand.exists():
            return cand
        n += 1


def ejecutar(fincas: Sequence[Finca], op: Opciones, srv: Servicios, reg: Registro,
             preguntar: Optional[Callable[[str, Sequence[str]], str]] = None) -> Path:
    if op.prueba:
        fincas = list(fincas)[: op.prueba]
        reg.info(f"Modo prueba: solo las primeras {len(fincas)} parcelas")
    raiz = carpeta_libre(op)
    if raiz != carpeta_trabajo(op):
        reg.info(f"Ya existía {carpeta_trabajo(op).name}: este lote va a {raiz.name}")
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

    def _volcar() -> None:  # Excel + registro a disco; un fallo aquí no debe tumbar el lote
        for fn in (lambda: escribir_excel(resultados, raiz / "resultados.xlsx", op.nombre_trabajo),
                   lambda: reg.escribir(raiz / "registro.txt")):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                reg.info(f"No se pudo guardar un fichero de resultados ({e})")

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
            if i % 5 == 0:
                _volcar()  # copia de seguridad cada 5 parcelas (corte de luz, cierre de terminal...)
    finally:
        _volcar()

    conjunto = None
    if op.mapa and srv.mapa is not None:
        try:
            conjunto = srv.mapa.generar(resultados, raiz, op.nombre_trabajo)
            if conjunto and conjunto.pdf:
                reg.info("Mapa de conjunto: mapa_conjunto.pdf generado")
            if conjunto and len(conjunto.puntos) >= 2:
                escribir_excel(resultados, raiz / "resultados.xlsx", op.nombre_trabajo, conjunto)
        except Exception as e:  # noqa: BLE001  el mapa es un extra: no debe impedir el informe ni el ZIP
            reg.info(f"No se pudo generar el mapa de conjunto ({e})")

    if op.informe:
        try:
            extras = [("Mapa de conjunto", conjunto.pdf)] if conjunto and conjunto.pdf else []
            if escribir_informe(resultados, raiz, op.nombre_trabajo, log=reg.info, extras=extras):
                reg.info("Informe: informe.pdf generado")
        except Exception as e:  # noqa: BLE001  el informe es un extra: no debe impedir el ZIP
            reg.info(f"No se pudo generar informe.pdf ({e})")

    zip_base = raiz.parent / raiz.name
    shutil.make_archive(str(zip_base), "zip", root_dir=raiz.parent, base_dir=raiz.name)
    reg.info(f"ZIP: {zip_base}.zip")
    return raiz


def _procesar(res: Resultado, op: Opciones, srv: Servicios, d_cat: Path, d_sig: Path,
              reg: Registro) -> None:
    f, cod = res.finca, res.codigos
    if f.sin_datos:
        res.errores.append(f.sin_datos)
        reg.error(f.etiqueta, f.sin_datos)
        return
    if cod is None:
        return
    clave = f"{cod.clave}_{f.poligono}_{f.parcela}"
    base = f.nombre_base
    previo = srv.usados.setdefault(base, clave)
    if previo != clave:  # municipios homónimos de otra provincia: no pisar el PDF anterior
        base = f"{base}_{cod.cod_provincia}{cod.cod_municipio_ine}"

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
        if res.sigpac.estado == "ERROR":
            res.coincide = "ERROR"  # fallo técnico, no confundir con parcela inexistente
        elif res.sigpac.estado == "ENCONTRADA" and not res.sigpac.refcat:
            res.coincide = "SIN REF SIGPAC"
            reg.aviso(f.etiqueta, "SIGPAC la encontró pero no devolvió referencia catastral")
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
        try:
            return DatosCatastro(**cacheado)
        except (TypeError, ValueError):  # caché de otro esquema o corrupta: se descarta y se reconsulta
            srv.cache.borrar("catastro", clave)
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
    cacheado = srv.cache.leer("sigpac_v3", clave)
    en_cache_pdf = srv.cache.fichero("pdf_sigpac_v3", clave)
    if cacheado and en_cache_pdf:
        try:
            r = ResultadoSigpac(**cacheado)
            shutil.copyfile(en_cache_pdf, destino)
            return r
        except (TypeError, ValueError, OSError):
            srv.cache.borrar("sigpac_v3", clave)
            srv.cache.borrar("pdf_sigpac_v3", clave)
    try:
        r = srv.sigpac.consultar(f, cod, res.catastro.refcat if res.catastro else "", destino)
    except Exception as e:  # noqa: BLE001
        res.errores.append(f"SIGPAC: {e}")
        reg.error(f.etiqueta, f"SIGPAC: {e}")
        return ResultadoSigpac("ERROR", detalle=str(e))
    if r.estado == "ENCONTRADA" and r.refcat:  # sin referencia no es un éxito completo: se reintenta otro día
        srv.cache.guardar("sigpac_v3", clave, asdict(r))
        if destino.exists():
            srv.cache.guardar_fichero("pdf_sigpac_v3", clave, destino)
    return r

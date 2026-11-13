"""Orquestación: lee fincas -> resuelve códigos -> Catastro -> SIGPAC -> Excel -> ZIP.

No conoce URLs ni selectores: solo habla con las interfaces de catastro.py y sigpac.py.
Los servicios se inyectan, así se puede probar entero con dobles (sin red).
"""
from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .cache import Cache
from .catastro import CatastroError, ParcelaNoEncontrada, RequiereCaptcha
from .codigos import Ambiguo, NoEncontrado, _partes, clave_nombre
from .excel import escribir_excel
from .informe import escribir_informe
from .inventario import aviso_alteracion, contrastar
from .modelos import Codigos, DatosAprovechamiento, DatosCatastro, DatosGeologia, DatosInventario, Finca, Meta, Resultado, ResultadoSigpac
from .registro import Registro
from .sigpac import GALICIA, coincide_refcat
from .superficie import control_superficie
from .utils import slug


@dataclass
class Opciones:
    nombre_trabajo: str
    salidas: Path = Path("salidas")
    prueba: Optional[int] = None
    usar_sigpac: bool = True
    usar_pdf_catastro: bool = True
    inventario: bool = True  # cobertura actual según el Inventario Forestal Continuo de Galicia
    aprovechamiento: bool = True  # condicionantes de corta: afecciones y dificultad de saca (solo Galicia)
    geologia: bool = True  # contexto geológico y minero (IGME; derechos mineros solo Galicia)
    mapa: bool = True      # mapa de conjunto de todas las fincas (solo Galicia)
    cliente: str = ""      # nombre del cliente tal cual (para el dossier)
    expediente: str = ""   # nº de expediente del informe
    informe: bool = True   # informe.pdf único con resumen, índice y todas las fichas
    fecha: Optional[str] = None


@dataclass
class Servicios:
    resolutor: object   # .resolver(provincia, municipio, preguntar) -> Codigos
    catastro: object    # .consulta_parcela(), .descargar_pdf()
    sigpac: Optional[object]  # .consultar()
    cache: Cache
    inventario: Optional[object] = None  # .consultar(refcat) -> DatosInventario | None
    aprovechamiento: Optional[object] = None  # .consultar(refcat) -> DatosAprovechamiento | None
    geologia: Optional[object] = None  # .consultar(lon, lat, galicia) -> DatosGeologia
    mapa: Optional[object] = None  # .generar(resultados, raiz, titulo) -> Conjunto | None
    usados: Dict[str, str] = field(default_factory=dict)  # nombre de fichero -> clave (detecta colisiones)


def _ahora() -> str:
    return datetime.now().isoformat(timespec="minutes")


def meta_de(op: Opciones) -> Meta:
    return Meta(cliente=op.cliente or op.nombre_trabajo, expediente=op.expediente,
                fecha=date.today().strftime("%d/%m/%Y"))


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
    if op.expediente:
        reg.info(f"Expediente {op.expediente} · cliente: {op.cliente or op.nombre_trabajo}")

    # 0) códigos: se resuelven todos antes, para preguntar las ambigüedades al principio
    codigos: Dict[Tuple[str, str], Optional[Codigos]] = {}
    fallos_codigos: Dict[Tuple[str, str], str] = {}
    for f in fincas:
        k = (f.provincia, f.municipio)
        if k in codigos or f.sin_datos:  # las filas SIN DATOS no se buscan: tampoco se resuelven sus códigos
            continue
        try:
            codigos[k] = srv.resolutor.resolver(f.provincia, f.municipio, preguntar)
            reg.info(f"{f.municipio} ({f.provincia}) -> {codigos[k].nombre_municipio} "
                     f"[{codigos[k].cod_provincia}{codigos[k].cod_municipio_ine}]")
            if clave_nombre(f.municipio) not in _partes(codigos[k].nombre_municipio):
                # coincidencia aproximada (prefijo o errata): que quede a la vista, no en silencio
                reg.aviso(f"{f.municipio} ({f.provincia})",
                          f"municipio interpretado como {codigos[k].nombre_municipio}; comprobar que es el correcto")
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
        for fn in (lambda: escribir_excel(resultados, raiz / "resultados.xlsx", op.nombre_trabajo, meta=meta_de(op)),
                   lambda: reg.escribir(raiz / "registro.txt")):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                reg.info(f"No se pudo guardar un fichero de resultados ({e})")

    try:
        for i, f in enumerate(fincas, start=1):
            reg.info(f"[{i}/{len(fincas)}] {f.etiqueta}")
            res = Resultado(finca=f, codigos=codigos.get((f.provincia, f.municipio)))
            resultados.append(res)
            try:
                _procesar(res, op, srv, d_cat, d_sig, reg)
            except Exception as e:  # noqa: BLE001  una parcela que falla no para las demás
                res.errores.append(f"Error inesperado: {e}")
                reg.error(f.etiqueta, f"Error inesperado: {e!r}")
            if res.codigos is None and not f.sin_datos:
                msg = fallos_codigos.get((f.provincia, f.municipio), "No se pudieron resolver los códigos")
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
                escribir_excel(resultados, raiz / "resultados.xlsx", op.nombre_trabajo, conjunto, meta=meta_de(op))
        except Exception as e:  # noqa: BLE001  el mapa es un extra: no debe impedir el informe ni el ZIP
            reg.info(f"No se pudo generar el mapa de conjunto ({e})")

    if op.informe:
        try:
            extras = [("Mapa de conjunto", conjunto.pdf)] if conjunto and conjunto.pdf else []
            if escribir_informe(resultados, raiz, op.nombre_trabajo, log=reg.info, extras=extras, meta=meta_de(op)):
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
    clave = f"{cod.clave}_{f.clave_parcela}"
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
            motivo = (str(e).strip().splitlines() or ["error desconocido"])[0]   # sin el volcado técnico del navegador
            res.errores.append(f"Ficha del Catastro no obtenida: {motivo}")
            reg.error(f.etiqueta, f"PDF Catastro: {motivo}")

    # ---- control de superficie
    res.control_superficie = control_superficie(datos.superficie_m2 if datos else None,
                                                f.superficie_m2, f.cuota)
    if datos and datos.clase == "Urbano" and datos.superficie_m2 is None:
        res.control_superficie = "NO APLICA"   # el servicio no da la superficie de parcela de un urbano
    if res.control_superficie == "REVISAR":
        sup = datos.superficie_m2 if datos else None
        reg.aviso(f.etiqueta, f"superficie catastral {sup} vs cliente {f.superficie_m2} "
                              f"(cuota {f.cuota.texto}) -> REVISAR")

    # ---- Inventario forestal (solo Galicia): cobertura actual frente al cultivo del Catastro
    if datos and op.inventario and srv.inventario is not None and cod.cod_provincia in GALICIA:
        _inventario(res, srv, clave, reg)
    if datos and op.aprovechamiento and srv.aprovechamiento is not None and cod.cod_provincia in GALICIA:
        _aprovechamiento(res, srv, clave, reg)

    if datos and op.geologia and srv.geologia is not None:
        _geologia(res, srv, cod, clave, reg)

    # ---- SIGPAC
    if f.por_referencia:   # el visor busca por polígono y parcela: un inmueble urbano no tiene
        res.coincide = "NO APLICA"
        reg.info(f"{f.etiqueta}: inmueble urbano, no se consulta SIGPAC")
        if (op.usar_sigpac and srv.sigpac is not None and hasattr(srv.sigpac, "ficha_urbana")
                and cod.cod_provincia in GALICIA and datos):
            res.sigpac = _ficha_urbana(res, srv, cod, clave, d_sig, base, reg)
    elif op.usar_sigpac and srv.sigpac is not None:
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
            d = DatosCatastro(**cacheado)
            d.consultado = d.consultado or srv.cache.fecha("catastro", clave)
            return d
        except (TypeError, ValueError):  # caché de otro esquema o corrupta: se descarta y se reconsulta
            srv.cache.borrar("catastro", clave)
    try:
        datos = (srv.catastro.consulta_referencia(f.refcat) if f.por_referencia
                 else srv.catastro.consulta_parcela(cod, f.poligono, f.parcela))
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
    datos.consultado = _ahora()
    srv.cache.guardar("catastro", clave, asdict(datos))
    return datos


def _geologia(res: Resultado, srv: Servicios, cod: Codigos, clave: str, reg: Registro) -> None:
    """Contexto geológico y minero. Es un extra: si falla, se avisa y la finca sigue adelante."""
    f, datos = res.finca, res.catastro
    d: Optional[DatosGeologia] = None
    cacheado = srv.cache.leer("geologia_v1", clave)
    if cacheado:
        try:
            d = DatosGeologia(**cacheado)
        except (TypeError, ValueError):
            srv.cache.borrar("geologia_v1", clave)
    if d is None:
        try:
            if datos.lon is None or datos.lat is None:   # centro de la parcela: una consulta más al Catastro
                datos.lon, datos.lat = srv.catastro.centro(datos.refcat)
                srv.cache.guardar("catastro", clave, asdict(datos))
            d = srv.geologia.consultar(datos.lon, datos.lat, cod.cod_provincia in GALICIA)
        except Exception as e:  # noqa: BLE001
            reg.aviso(f.etiqueta, f"geología y minería: no se pudo consultar ({e})")
            return
        d.consultado = _ahora()
        if not d.sin_consultar:              # solo se cachea la consulta completa
            srv.cache.guardar("geologia_v1", clave, asdict(d))
    res.geologia = d
    reg.info(f"{f.etiqueta}: geología y minería: {d.veredicto}")
    if d.derechos_parcela:
        reg.aviso(f.etiqueta, "hay derechos mineros vigentes sobre la finca: "
                              + ", ".join(x["numero"] for x in d.derechos_parcela))
    if d.sin_consultar:
        reg.aviso(f.etiqueta, "geología y minería: no se pudo consultar " + ", ".join(d.sin_consultar))


def _aprovechamiento(res: Resultado, srv: Servicios, clave: str, reg: Registro) -> None:
    """Afecciones a una corta y dificultad de saca. Es un extra: si falla, se avisa y la finca sigue adelante."""
    f = res.finca
    d: Optional[DatosAprovechamiento] = None
    cacheado = srv.cache.leer("aprovechamiento_v3", clave)
    if cacheado:
        try:
            d = DatosAprovechamiento(**cacheado)
        except (TypeError, ValueError):
            srv.cache.borrar("aprovechamiento_v3", clave)
    if d is None:
        try:
            d = srv.aprovechamiento.consultar(res.catastro.refcat)
        except Exception as e:  # noqa: BLE001
            reg.aviso(f.etiqueta, f"condicionantes de corta: no se pudieron consultar ({e})")
            return
        if d is None:
            reg.info(f"{f.etiqueta}: sin geometría en el inventario, no se consultan condicionantes de corta")
            return
        d.consultado = _ahora()
        if not d.sin_consultar:              # solo se cachea la consulta completa
            srv.cache.guardar("aprovechamiento_v3", clave, asdict(d))
    res.aprovechamiento = d
    if d.afecciones:
        tipos = "; ".join(dict.fromkeys(a["tipo"].split(" (")[0] for a in d.afecciones))
        reg.info(f"{f.etiqueta}: afecciones a tener en cuenta en una corta: {tipos}")
    if d.sin_consultar:
        reg.aviso(f.etiqueta, "condicionantes de corta: no se pudo consultar " + ", ".join(d.sin_consultar))


def _inventario(res: Resultado, srv: Servicios, clave: str, reg: Registro) -> None:
    """Anota cobertura actual y contraste. Es un extra: si falla, se avisa y la finca sigue adelante."""
    f, datos = res.finca, res.catastro
    inv: Optional[DatosInventario] = None
    cacheado = srv.cache.leer("inventario", clave)
    if cacheado and "monte_vecinal" in cacheado:   # las entradas anteriores a ese dato se vuelven a consultar
        try:
            inv = DatosInventario(**cacheado)
        except (TypeError, ValueError):
            srv.cache.borrar("inventario", clave)
    if inv is None:
        try:
            inv = srv.inventario.consultar(datos.refcat)
        except Exception as e:  # noqa: BLE001
            res.avisos.append("Inventario forestal: no se pudo consultar")
            reg.aviso(f.etiqueta, f"inventario forestal: no se pudo consultar ({e})")
            return
        if inv is not None:
            inv.consultado = _ahora()
            srv.cache.guardar("inventario", clave, asdict(inv))
    res.inventario = inv
    res.contraste, res.contraste_detalle = contrastar(datos.uso, inv)
    if inv is None:
        reg.info(f"{f.etiqueta}: no figura en el Inventario Forestal de Galicia")
        return
    if inv.monte_vecinal:
        res.avisos.append("Figura como monte vecinal o público en el Inventario Forestal: conviene confirmarlo")
        reg.aviso(f.etiqueta, "figura como monte vecinal o público en el inventario forestal")
    if res.contraste == "REVISAR":
        reg.aviso(f.etiqueta, f"cultivo catastral y cobertura actual no cuadran -> REVISAR. {res.contraste_detalle}")
    else:
        alt = aviso_alteracion(inv)
        if alt:
            res.avisos.append(alt)
            reg.aviso(f.etiqueta, alt)


def _ficha_urbana(res, srv, cod, clave, d_sig, base, reg) -> ResultadoSigpac:
    """Plano del inmueble urbano (contorno del Catastro + ortofoto SIXPAC). Misma caché que las fichas SIXPAC."""
    f = res.finca
    destino = d_sig / f"{base}_sigpac.pdf"
    cacheado = srv.cache.leer("sigpac_v3", clave)
    en_cache_pdf = srv.cache.fichero("pdf_sigpac_v3", clave)
    if cacheado and en_cache_pdf:
        try:
            r = ResultadoSigpac(**cacheado)
            r.consultado = r.consultado or srv.cache.fecha("sigpac_v3", clave)
            shutil.copyfile(en_cache_pdf, destino)
            return r
        except (TypeError, ValueError, OSError):
            srv.cache.borrar("sigpac_v3", clave)
            srv.cache.borrar("pdf_sigpac_v3", clave)
    try:
        r = srv.sigpac.ficha_urbana(f, cod, res.catastro.refcat if res.catastro else f.refcat, destino)
    except Exception as e:  # noqa: BLE001
        r = ResultadoSigpac("ERROR", detalle=str(e))
    r.consultado = r.consultado or _ahora()
    if r.estado == "ENCONTRADA":
        srv.cache.guardar("sigpac_v3", clave, asdict(r))
        if destino.exists():
            srv.cache.guardar_fichero("pdf_sigpac_v3", clave, destino)
    else:
        reg.aviso(f.etiqueta, f"plano del inmueble urbano no disponible: {r.detalle}")
    return r


def _sigpac(res, srv, cod, clave, d_sig, base, reg) -> ResultadoSigpac:
    f = res.finca
    destino = d_sig / f"{base}_sigpac.pdf"
    cacheado = srv.cache.leer("sigpac_v3", clave)
    en_cache_pdf = srv.cache.fichero("pdf_sigpac_v3", clave)
    if cacheado and en_cache_pdf:
        try:
            r = ResultadoSigpac(**cacheado)
            r.consultado = r.consultado or srv.cache.fecha("sigpac_v3", clave)
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
    r.consultado = r.consultado or _ahora()
    if r.respaldo:
        res.avisos.append("Ficha SIXPAC/SIGPAC con captura de respaldo (parcela rellena): repetir para obtener el contorno")
        reg.aviso(f.etiqueta, "ficha con captura de respaldo (parcela rellena, tapa la ortofoto); no se guarda en caché")
    if r.estado == "ENCONTRADA" and r.refcat and not r.respaldo:  # éxito completo; si no, se reintenta otro día
        srv.cache.guardar("sigpac_v3", clave, asdict(r))
        if destino.exists():
            srv.cache.guardar_fichero("pdf_sigpac_v3", clave, destino)
    return r

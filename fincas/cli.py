"""Línea de comandos."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import config
from .entrada import ErrorEntrada, leer_fincas
from .pipeline import Opciones, Servicios, carpeta_trabajo, ejecutar
from .registro import Registro
from .utils import slug


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fincas",
        description="Localiza fincas rústicas (Catastro + SIGPAC) y genera una carpeta de resultados.")
    p.add_argument("entrada", help="CSV, XLSX o DOCX con la lista de fincas")
    p.add_argument("--nombre", help="nombre del trabajo (por defecto, el del fichero de entrada)")
    p.add_argument("--municipio", default="", help="municipio por defecto si el fichero no lo trae")
    p.add_argument("--provincia", default="", help="provincia por defecto si el fichero no la trae")
    p.add_argument("--prueba", type=int, metavar="N", help="procesar solo las N primeras parcelas")
    p.add_argument("--cliente", help="nombre del cliente: las salidas van a "
                   "<carpeta de clientes>/<cliente>/salidas/ (ver config.CARPETA_CLIENTES)")
    p.add_argument("--salidas", default=None, help="carpeta base de salidas (def.: salidas, o la del cliente)")
    p.add_argument("--simular", action="store_true",
                   help="solo lee y valida la entrada y muestra el plan. NO hace ninguna consulta.")
    p.add_argument("--sin-sigpac", action="store_true", help="saltar el paso SIGPAC")
    p.add_argument("--sin-pdf-catastro", action="store_true", help="no descargar el PDF del Catastro")
    p.add_argument("--sin-mapa", action="store_true", help="no generar el mapa de conjunto de las fincas")
    p.add_argument("--sin-informe", action="store_true", help="no generar informe.pdf (resumen + todas las fichas)")
    p.add_argument("--sin-cache", action="store_true", help="ignorar la caché (vuelve a consultar)")
    p.add_argument("--visible", action="store_true", help="mostrar el navegador (calibrar / captcha manual)")
    p.add_argument("--pausa-min", type=float, default=config.PAUSA_MIN_S)
    p.add_argument("--pausa-max", type=float, default=config.PAUSA_MAX_S)
    return p


def _preguntar(que: str, opciones: Sequence[str]) -> str:
    if not sys.stdin.isatty():
        raise SystemExit(f"Ambigüedad en {que}: {', '.join(opciones)}. Ejecuta en una terminal interactiva "
                         "o escribe el nombre oficial en el fichero.")
    print(f"\nAmbigüedad en {que}. ¿Cuál es?")
    for i, o in enumerate(opciones, 1):
        print(f"  {i}) {o}")
    while True:
        r = input("Número (o Enter para abortar): ").strip()
        if not r:
            raise SystemExit("Abortado por el usuario.")
        if r.isdigit() and 1 <= int(r) <= len(opciones):
            return opciones[int(r) - 1]


def _plan(fincas, problemas, op: Opciones) -> None:
    print(f"\nPLAN (simulación, sin consultas): {len(fincas)} parcelas -> {carpeta_trabajo(op)}/")
    municipios = sorted({(f.provincia, f.municipio) for f in fincas})
    print(f"Municipios distintos: {len(municipios)}")
    for prov, mun in municipios:
        n = sum(1 for f in fincas if (f.provincia, f.municipio) == (prov, mun))
        print(f"  - {mun} ({prov}): {n} parcelas")
    parciales = [f for f in fincas if not f.cuota.completa]
    print(f"Parcelas con cuota distinta de completa: {len(parciales)}")
    for f in fincas[:10]:
        print(f"    {f.nombre_base}  cuota={f.cuota.texto}  sup_cliente={f.superficie_m2}")
    if len(fincas) > 10:
        print(f"    ... y {len(fincas) - 10} más")
    if problemas:
        print(f"\nProblemas de lectura ({len(problemas)}):")
        for pr in problemas:
            print("  !", pr)
    t_min = len(fincas) * (config.PAUSA_MIN_S * 3) / 60  # ~3 consultas por parcela
    print(f"\nTiempo orientativo solo en pausas: >= {t_min:.0f} min")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        fincas, problemas = leer_fincas(args.entrada, args.municipio, args.provincia)
    except ErrorEntrada as e:
        print(f"ERROR de entrada: {e}", file=sys.stderr)
        return 2
    if args.salidas:
        salidas = Path(args.salidas)
    elif args.cliente:
        salidas = config.CARPETA_CLIENTES / slug(args.cliente) / "salidas"
    else:
        salidas = Path("salidas")
    op = Opciones(nombre_trabajo=args.nombre or Path(args.entrada).stem, salidas=salidas,
                  prueba=args.prueba, usar_sigpac=not args.sin_sigpac,
                  usar_pdf_catastro=not args.sin_pdf_catastro,
                  informe=not args.sin_informe, mapa=not args.sin_mapa)
    if args.simular:
        _plan(fincas[: args.prueba] if args.prueba else fincas, problemas, op)
        return 0

    # --- a partir de aquí SÍ hay consultas reales ---
    from .cache import Cache
    from .catastro import ServicioCatastro
    from .codigos import Ambiguo, ResolutorCodigos
    from .navegador import Navegador
    from .red import ClienteHttp, Limitador
    from .sigpac import ServicioSigpac

    import signal

    def _term(*_):  # `kill`/cierre de terminal: sale por el finally y guarda Excel y registro
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)
    reg = Registro()
    for pr in problemas:
        reg.aviso("entrada", pr)
    limitador = Limitador(args.pausa_min, args.pausa_max)
    cache = Cache(Path(config.CARPETA_CACHE), activa=not args.sin_cache)
    nav = Navegador(visible=args.visible)
    try:
        catastro = ServicioCatastro(ClienteHttp(limitador, log=reg.info), limitador, nav, log=reg.info)
        sigpac = None if args.sin_sigpac else ServicioSigpac(nav, limitador, log=reg.info)
        from .mapa import ServicioMapa
        mapa = None if (args.sin_mapa or args.sin_sigpac) else ServicioMapa(nav, cache, log=reg.info)
        srv = Servicios(ResolutorCodigos(catastro, cache), catastro, sigpac, cache, mapa=mapa)
        try:
            carpeta = ejecutar(fincas, op, srv, reg, preguntar=_preguntar)
        except Ambiguo as e:
            print(f"Ambigüedad: {e}", file=sys.stderr)
            return 3
        print(f"\nListo: {carpeta}", flush=True)
        return 1 if getattr(reg, "_revisar", None) and any("ERROR" in m for _, m in reg._revisar) else 0
    finally:
        nav.cerrar()

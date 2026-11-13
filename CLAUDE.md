# CLAUDE.md — Búsqueda de fincas (rústicas y urbanas)

## Objetivo
CLI reutilizable para cualquier cliente. Entrada: lista de fincas de España de cualquier tipo: rústicas
(municipio, provincia, polígono, parcela, o referencia catastral) y urbanas (referencia catastral). Salida: carpeta `salidas/AAAA-MM-DD_nombre-trabajo/` con
`resultados.xlsx`, `pdf_catastro/`, `pdf_sigpac/`, `registro.txt` y un ZIP.
**Nada del código puede estar atado a un municipio, cliente o lista concreta.**

## Organización de carpetas
- **Herramienta (este proyecto, "BUSQUEDA FINCAS CLAUDE")**: código, tests, `.venv`, `.cache` (caché única).
- **Clientes**: `~/Desktop/BUSQUEDAS FERRADOS-CLAUDE/<cliente-slug>/` con `entrada/` (listas del cliente) y
  `salidas/` (resultados). Un hilo por cliente, titulado con su nombre.
- Lanzar un lote de un cliente (desde esta carpeta):
  `.venv/bin/python -m fincas "<ruta>/entrada/lista.csv" --cliente "Nombre Cliente" --nombre lote-x`
  Se lanza directamente, siempre en segundo plano (sin ventana), para que el usuario pueda seguir usando el ordenador.
  `--cliente` guarda en `<carpeta clientes>/<slug>/salidas/` (`FINCAS_CLIENTES` cambia la raíz).
- Clientes hechos: ver la carpeta de clientes (los datos de clientes no se documentan en este repo).

## Reglas del encargo (no negociables)
- **La herramienta ya está validada: no hace falta simular ni probar antes de un lote.** Se lanza el lote
  completo cuando el usuario da la lista. (`--simular` y `--prueba N` siguen existiendo, solo opcionales.)
- No obtener titular ni valor catastral (datos protegidos).
- No saltarse captchas: si la Sede pide uno, se registra y se resuelve a mano (`--visible`).
- Pausa 3–5 s entre consultas reales, 2 reintentos, caché para relanzar sin repetir.
- Si una parcela falla, se continúa con las demás. Nunca se descartan parcelas (la cuota y el control de
  superficie solo marcan/avisan).
- `--prueba N` ejecuta solo las N primeras parcelas.

## Arquitectura (un módulo por fuente; si una web cambia, solo se toca su módulo)
| Módulo | Responsabilidad |
|---|---|
| `entrada.py` | Lee CSV/XLSX/DOCX, cabeceras flexibles, cuota, relleno hacia abajo de municipio |
| `codigos.py` | Nombre → códigos (provincia/municipio), tildes, gallego/castellano, `Ambiguo` |
| `catastro.py` | Servicios XML (provincias, municipios, `Consulta_DNPPP`) + PDF de la Sede. Parsers puros |
| `sigpac.py` | Visor SIGPAC/SIXPAC con Playwright (búsqueda, ref. catastral, ficha PDF) |
| `plano.py` | Plano de la ficha SIXPAC: geometría + ortofoto de los servicios del visor, dibujado en SVG (funciones puras testeables) |
| `excel.py` | `resultados.xlsx`: hoja Resumen + hoja Fincas (enlaces a PDF y a la Sede, detalle del control) |
| `resumen.py` | Cifras del lote (totales, por municipio/uso, controles). Puro, sin red |
| `mapa.py` | Mapa de conjunto de todas las fincas (solo Galicia): 1 petición `parcelabox` por finca (caché `bbox_sixpac`) + ortofoto SIXPAC; distancias, grupos cercanos (≤2,5 km encadenadas, máx. 8 mapas). Geometría pura + `ServicioMapa` con red inyectable |
| `informe.py` | `informe.pdf`: portada+resumen, índice y todas las fichas con marcadores. Une PDF ya descargados (pypdf + reportlab) |
| `inventario.py` | Inventario Forestal Continuo de Galicia (Xunta): cobertura ACTUAL por referencia catastral y contraste con el cultivo del Catastro. Parsers puros + `ServicioInventario` con HTTP inyectable |
| `aprovechamiento.py` | Condicionantes de una corta (solo Galicia): afecciones sectoriales que toca la parcela (capas VISAF de la Xunta) con su organismo, y dificultad de saca (restricciones técnicas INVEGA). Parsers puros + `ServicioAprovechamiento` con HTTP inyectable |
| `geologia.py` | Contexto geológico y minero (toda España): hoja y litología MAGNA, indicios y explotaciones BDMIN a ≤5 km y Atlas Geoquímico (IGME-CSIC); derechos mineros vigentes de la Xunta (solo Galicia). Parsers puros + `ServicioGeologia` con HTTP inyectable |
| `precios.py` | Precio medio comarcal de la tierra rústica (Xunta, Observatorio de Mobilidade de Terras). Sin red: tablas en `fincas/datos/` |
| `colindantes.py` | Parcelas colindantes y acceso a dominio público según SIXPAC (solo Galicia). Parser puro + `ServicioColindantes` con HTTP inyectable |
| `superficie.py` | Control de superficie (margen 10 %, cuota) |
| `pipeline.py` | Orquestación; servicios inyectados (testeable sin red) |
| `red.py` / `navegador.py` / `cache.py` / `registro.py` | pausas+reintentos+HTTP / Chromium compartido / caché / registro.txt |
| `config.py` | URLs, constantes (sobreescribibles por variable de entorno) |

## Decisiones técnicas
- **Producción de eucalipto, planeamiento y precios caducados (2026-11-13).** (a) `madera_galicia.json` lleva `produccion`: m³/ha a
  turno por calidad del terreno, para glóbulus de rebrote, glóbulus plantado (15 años) y nitens plantado (13), del «Caderno técnico:
  montes de eucalipto» de la Asociación Forestal de Galicia (2020). OJO: según esa fuente el rebrote BIEN LLEVADO (selección a ~1.000
  brotes/ha) da lo mismo que la plantación; no afirmar que "el rebrote da menos" sin más. Va en m³ (la fuente no da toneladas; no se
  inventa la conversión). Sigue sin estimarse la madera de ninguna finca. Pino: `producciones` lleva también pinaster (regeneración natural y plantado, 35 años) y radiata (25 años), del «Caderno técnico: montes de coníferas» de la misma asociación; ahí la cifra es la producción de TODO el turno, claras incluidas. Frondosas: sin tabla publicada (la página de precios de la AFG es solo para socios). (b) Planeamiento:
  `planeamiento_galicia.json` = instrumento, fecha y situación de los 313 concellos (tabla de SIOTUGA, consultada 2026-11-13;
  actualizar a mano). Es dato MUNICIPAL: la clasificación del suelo de cada finca NO se consulta porque el propio SIOTUGA avisa de que
  su cartografía está en revisión y no tiene validez legal; el informe remite al ayuntamiento. (c) La CLI avisa en el registro si
  `revisado_iso` de la tabla de madera tiene más de 6 meses. Anexos: fuentes, metodología (compacta) y aviso legal van en UNO.
- **Orden definitivo en CUATRO PARTES (2026-11-13, decisión del usuario: primero se enseña el trabajo, luego se interpreta).**
  1 Lo esencial (resumen ejecutivo, mapa) · 2 Las fincas, una a una · 3 Para decidir (resumen para la compraventa, qué puede valer y
  qué cuesta vender, afecciones + cotejo forestal en UN apartado) · 4 Anexos. Las partes 2-4 abren con una página separadora
  (`_separador`, textos en `PARTES`). El Contenido y el índice de fincas son ENLACES a su página: `_ancla` anota la posición de cada
  fila al dibujar el frontal y `escribir_informe` añade después las anotaciones con pypdf (`Link`). La portada solo lleva las 4 cifras
  y cómo está organizado el informe; las tablas por municipio y por uso pasaron al resumen ejecutivo. Esto sustituye al orden del
  punto siguiente.
- **Composición del informe (2026-11-13, rediseño pedido por el usuario).** Orden: portada con 4 cifras en recuadros (`_tarjetas`) ·
  Contenido (índice general con páginas + índice de fincas) · Resumen ejecutivo (una tabla con marcas verde/ámbar/rojo; rojo solo
  para monte vecinal) · mapa · Resumen para la compraventa · Precio de referencia de la zona · Afecciones · Cotejo forestal ·
  fincas · anexos (construcciones, geología, fuentes y fechas, metodología y aviso legal: la metodología va AL FINAL).
  `escribir_informe` monta piezas ("propia"|"externa", título, contenido) y genera el frontal al final con las páginas reales.
- **"Qué puede valer y qué cuesta vender" (2026-11-13, decisión del usuario).** Sustituye al apartado de precio medio comarcal, que
  el usuario descartó: el cliente lo leería como el precio de su finca y en Galicia se vende por lo que pague el comprador. El
  apartado da (a) precios de la madera EN PIE como horquilla con fuente y fecha (`fincas/datos/madera_galicia.json`, de Campo Galego
  y un maderista; caducan: revisar 2-3 veces al año y cambiar `revisado`); (b) a quién suele interesar cada finca y sus colindantes;
  (c) la tasa de movilidad de la tierra de la comarca (`movilidad_total` en `precios_tierra_galicia.json`, mismo informe de la Xunta).
  NUNCA calcula toneladas ni dinero de una finca: no se conoce edad ni densidad. Sin tabla de toneladas por edad: solo el crecimiento
  publicado (10-40 m³/ha·año, media 20, USC); una tabla por edades y por rebrote/plantado necesita validarla un ingeniero forestal.
  ferrados.com también hace ofertas de compra: el aviso legal lo dice, y las cifras van como horquillas con fuente, no sesgadas a la baja.
- **Precio de referencia (2026-11-13; ya NO se muestra en el informe, ver arriba).** `precios.py`, SIN red: tablas en `fincas/datos/` (públicas). `precios_tierra_galicia.json` =
  precio medio de compraventa 2021-2023 por comarca, monte y SAU, del informe anual 2024 del Observatorio Galego de Mobilidade de
  Terras (AGADER, con datos de ATRIGA); 44 comarcas, 9 sin dato. `comarcas_galicia.json` = comarca de cada concello por código INE
  (capa `PBA/Mapa_Base_PBA_2020/MapServer/144`, campo `Comarca`). Usar el municipio INE, NO el del Catastro. Tipo de terreno = lo que
  hay hoy (inventario) y, si no, el Catastro. DECISIÓN: el precio NUNCA se multiplica por la superficie de la finca (se leería como
  tasación); solo €/m² y €/ha de la comarca. No aplica a urbanos. Actualizar las tablas a mano cuando salga un informe nuevo.
- **Colindantes (2026-11-13).** `colindantes.py`: `ParcelasCatastrais/SIXPAC_<año>/MapServer/1` (se elige el año más reciente, 1
  petición por lote). `query` por PROVINCIA/MUNICIPIO(Catastro)/POLIGONO/PARCELA con geometría y luego `query` espacial POST con
  `esriSpatialRelIntersects` + `distance=1` (con `Touches` devuelve 0). Parcelas ≥ 9000 = dominio público (camino, vía o cauce):
  se cuentan como "acceso", no como vecinas. Solo referencias, nunca titulares. Solo rústicas de Galicia. Caché `colindantes_v1`,
  `--sin-colindantes`. La capa trae además `DN_SURFACE` (superficie SIXPAC), aún sin usar.
- **Urbanismo (clasificación del suelo): NO verificado.** No se encontró servicio público consultable (SIOTUGA); no añadir sin probarlo.
- **Estructura del informe (2026-11-13, decisión del usuario).** La HOJA de cada finca no lleva geología (la alargaba y "rompía" el
  informe). Orden: portada+resumen, índice (con los anexos y su página), cotejo forestal, página "Afecciones y limitaciones detectadas"
  (resumen del lote con lo que ya consta en cada hoja; el monte vecinal/utilidad pública va aparte y primero porque afecta a la
  titularidad), metodología, mapa, fincas, y AL FINAL los anexos: "Construcciones e inmuebles urbanos" (solo si hay; incluye
  `DatosCatastro.otros_inmuebles` = construcciones dadas de alta en una parcela rústica, que salen de la respuesta-lista de DNPPP sin
  consulta extra) e "Informe geológico y minero" (indicios en UNA tabla para todo el lote, con la finca más cercana). El Catastro no
  dice si algo está en ruina: no se aventura, solo se avisa de que hay que verlo en visita.
  La trazabilidad (fuente y fecha de cada consulta, enlace a la Sede) ya NO va en cada hoja: es el último anexo, "Fuentes y fechas
  de consulta", una fila por finca. Así la hoja de cada finca cabe en una página.
  Primer anexo: "Resumen para la compraventa" (`puntos_a_comprobar`): por finca, qué es, qué hay hoy y qué conviene aclarar. SOLO
  hechos que ya constan en el resto del informe; no valora ni recomienda. Sin consultas nuevas.
  `_tabla` convierte toda cadena suelta en párrafo: ningún texto se sale de su celda (antes los rótulos largos de la 1ª columna se
  salían). Si una cabecera se parte a mitad de palabra, ensanchar la columna. Para revisar el PDF a ojo sin poppler: separar páginas
  con pypdf y `sips -s format png` (las fichas SIXPAC salen con fondo negro en esa conversión: es del conversor, no del PDF).
- **Geología y minería (2026-11-13).** `geologia.py`, por el centro de la parcela (lon/lat de `Consulta_CPMRC`, que se pide y se
  guarda en la caché de Catastro si falta). Servicios verificados en `https://mapas.igme.es/gis/rest/services`:
  `Cartografia_Geologica/IGME_MAGNA_50/MapServer/0` (hoja) y `/11` (litología, campo `DLO`), `BasesDatos/IGME_BDMIN_Indicios/MapServer/1`
  y `IGME_BDMIN_Explotaciones/MapServer/1` (`query` con `distance=5000`; las columnas X/Y vienen mezcladas de SRS: usar la geometría),
  `AtlasGeoquimico/IGME_MapaIsovalores2012_<El>/MapServer` (`identify` capa 0 = sedimentos: valor y clase, que EMPIEZA EN 0; `legend`
  da los tramos; unidades sin confirmar: se muestra solo "tramo n de N"). Derechos mineros: `PBA/Afeccions_outras_ConcesionsMineiras/MapServer/0`
  de la Xunta (solo vigentes; trae TITULAR, que NO se guarda). `IGME_Geode_50` no admite `query`. Valoración en una línea:
  «Anomalía detectada» (algún elemento Au/Ag/Sn/W/Ce en el tramo más alto del atlas), «Indicios en la comarca» (indicios BDMIN a ≤5 km),
  «Sin indicios», o «Sin verificar» si falló una fuente (nunca se da por limpia). El atlas es regional (interpolado): el informe lo dice.
  ~13 peticiones por finca (~50 s), caché `geologia_v1` (solo consultas completas), `--sin-geologia` lo omite. Sin usar: muestras
  geoquímicas y geofísica (`IGME_MuestrasGeoquimica`, `IGME_SIGEOF`: solo dicen dónde hay datos, no valores).
- **Inmuebles urbanos (2026-11-13).** Si la referencia catastral de la lista no es rústica (p. ej. una casa en diseminado), la
  finca ya no queda SIN DATOS: se busca por la referencia (`Finca.refcat`, `por_referencia`). `ServicioCatastro.consulta_referencia`
  usa `Consulta_DNPRC` (uso, superficie CONSTRUIDA en `sfc`, año `ant`, destinos en `lcons`) y `OVCCoordenadas.asmx/Consulta_CPMRC`
  (centro de la parcela, para el mapa). El servicio NO da la superficie de la parcela de un urbano (solo la ficha PDF): control de
  superficie `NO APLICA`. Ficha de la Sede: misma URL con `UrbRus=U` (verificado). SIGPAC no se consulta (`coincide = NO APLICA`).
  Inventario y condicionantes sí: las capas de la Xunta tienen esas referencias. Clave de caché `<prov+mun>_rc_<14>`.
  La Sede falla a ratos (error de datos o descarga que no llega): los fallos no se cachean, basta relanzar.
- **Inventario forestal (2026-11-12).** El cultivo del Catastro suele estar desfasado (en 12 parcelas reales de A Coruña, 7 no
  cuadraban). Se cruza con el Inventario Forestal Continuo de Galicia (INVEGA), NO con el IFN (el IFN4 de Galicia es de 2011 y no
  baja a parcela). Servicio público verificado: `https://ideg.xunta.gal/servizos/rest/services/INVEGA/InvegaRecintos/MapServer/0/query?where=REFCAT='<14>'&outFields=*&returnGeometry=false&f=json`.
  Un registro por recinto y edición (ADM_FECINI = 1 de enero de 2022/2023/2024); se usa la última y se guarda el histórico.
  Columnas (textos del visor, `appdata/locales/es.json`): S01 eucaliptos, S02 coníferas, S03 frondosas, S04+S41-43 cortas,
  S05+S51-53 regeneración, S06 prado o cultivo, S07 matorral, S08 suelo desnudo, S09 antrópico, S10 agua, S11+S111-113 incendios,
  S12+S121-123 área quemada; la suma = SUTIL. `Vm3`/`Vm3_ha` (volumen) vienen vacías y el visor las llama "provisionales": se
  muestran solo si traen dato y rotuladas como provisionales. Contraste: COHERENTE si lo que declara el Catastro ocupa ≥50 %;
  REVISAR si no, o si hay cortas/incendio ≥20 % con cultivo arbolado; NO COMPARABLE; SIN DATO. Solo avisa. Solo Galicia.
  1 consulta más por finca (~4 s con la pausa), caché `inventario`. `--sin-inventario` lo omite. Si falla, aviso y se sigue.
  El mismo servidor tiene más capas sin usar todavía: especies (`InvegaUsosSueloEspecies`), densidad, restricciones legales y
  técnicas, y `ParcelasCatastrais/SIXPAC_2022/MapServer/1/query` (búsqueda de parcela por prov/mun/pol/parc sin visor).
- **Cotejo destacado y monte vecinal (2026-11-12).** `informe.pdf` lleva tras el índice la página "Cotejo con el Inventario
  Forestal Continuo de Galicia": tabla de fincas con diferencias (Catastro frente a inventario), coherentes, no comparables y las
  que figuran como monte vecinal o público. `DatosInventario.monte_vecinal` sale de `MVMC_MP` (1 = sí, 0 = no; sale de la misma
  consulta). Las entradas de caché sin ese campo se vuelven a consultar solas.
- **Bloque de corta CONSTRUIDO (2026-11-12)** con (a), (b) sin la capa legal y (c) de la lista siguiente: `aprovechamiento.py`,
  16 peticiones por finca (~70 s), caché `aprovechamiento_v3` (solo consultas completas), `--sin-corta` lo omite. La hoja de la finca
  puede ocupar 2 páginas (`_Entrada.hoja` cuenta las reales para el índice). El informe NUNCA dice si se puede cortar: solo qué
  capa toca y a qué organismo corresponde. Probado con 7 parcelas reales (aguas, patrimonio, Camino de Santiago; resultados distintos).
  AÑADIDAS después y verificadas con una entidad real de cada capa (`CARPETAS` da la carpeta del servidor): `PBA/Afeccions_Forestal` (registro de
  montes vecinales con nombre, utilidad pública, montes ordenados, masas consolidadas de frondosas autóctonas),
  `PBA/Afeccions_outras_Forestal_Faixas` (faja de protección 50 m), `PBA/Afeccions_Enerxia` (líneas eléctricas, gasoducto),
  `PBA/Afeccions_ConcentracionsParcelarias`, `SIRFOG/CAMPANHAS_INCENDIOS` (incendios 2020-2021 con fecha) y
  `PBA/Afeccions_outras_AreasIncendiadas` (2010-2020, año en `CODIGO[:4]`; el mismo incendio en las dos se deja una vez).
  identify devuelve ALIAS de campo (p. ej. `confederac`, `Concello`): comparar sin mayúsculas (`_atr`). La capa de fajas no
  devuelve geometría en `query` (demasiado grande) pero `identify` sí funciona. Sin añadir: `PBA/Afeccions_outras_ConcesionsMineiras`. Descartada `MUAGA/Actual_Galicia` (contradice al inventario 2024: más antigua).
- **Investigado y VERIFICADO con parcela real (2026-11-12)**:
  (a) Geometría de la parcela: la misma consulta de `InvegaRecintos` con `returnGeometry=true` (EPSG:25829).
  (b) Restricciones a la corta (raster 10 m): `INVEGA/InvegaRestriccionesLegais|Tecnicas|TecLeg/ImageServer/computeHistograms`
  (POST, `geometry`=polígono, `geometryType=esriGeometryPolygon`) devuelve el recuento de píxeles por clase dentro de la parcela.
  TecLeg: 1 favorable, 2 intermedio, 3 desfavorable. Técnicas: 1 favorable … 4 muy desfavorable. Legales: 0–6, significado SIN
  confirmar (falta leer el informe explicativo del centro de descargas de INVEGA): no rotular hasta saberlo.
  (c) Afecciones sectoriales (las del visor de aprovechamientos): `VISAF/{Augas,Estradas,Costas_DPMT,PatrimonioCultural,
  PatrimonioNatural_autorizacion_declaracion,CaminosSantiago,ZonasDemarcadas}/MapServer/identify` (POST, polígono, `layers=all`,
  `tolerance=0`, `mapExtent`, `imageDisplay`) → capas que toca la parcela. 7 peticiones por finca.
  (d) Especies: `InvegaUsosSueloEspecies` es piloto y solo distingue pinos (21 silvestre, 26 pinaster, 28 radiata); el campo
  `ESPECIE` de recintos viene a 0. `Invega_DB`/`Invega_MOE` son densidad básica y elasticidad de la MADERA, no pies/ha.
  (e) Altura LiDAR: el IGN NO la sirve por WCS/API (solo MDT). Hay que descargar del CNIG el MDSnV 2,5 m (1ª/2ª cobertura) o
  restar MDS−MDT 50 cm de la 3ª cobertura (Galicia ya publicada), por hojas, y leer GeoTIFF. Sin resolver la descarga automática.
  (e2) Altura por servicio, VERIFICADO (lo usa Iberpix): `https://wms-mapa-lidar.idee.es/lidar` WMS 1.3.0 GetFeatureInfo,
  `layers=query_layers=EL.GridCoverage`, `crs=EPSG:25829`, `info_format=text/plain` → capa `gfi-vegetacion-h30`, `value_0` =
  altura de la vegetación en metros en ese punto. Un punto por petición (GetMap en tiff devuelve imagen pintada, no valores).
  OJO: es la 1ª cobertura PNOA-LiDAR (malla 2,5 m, vuelo antiguo, fecha por zona sin confirmar): en eucalipto puede no valer.
  (f) Régimen de cortas (declaración responsable MR604R / autorización, Decreto 73/2020, Ley 7/2012): NO redactar reglas en el
  informe sin validarlas; solo afecciones detectadas + organismo + remisión a Medio Rural.
- **Dossier con marca (2026-11-12).** `informe.pdf` = portada (marca `ferrados.com` en verde oscuro, cliente, expediente, fecha) +
  resumen + índice + metodología y aviso legal + mapa + por finca una HOJA propia (identificación, comprobaciones, incidencias y
  TRAZABILIDAD: fuente, fecha y hora de cada consulta, enlace a la Sede) y sus fichas. Cabecera y pie en TODAS las páginas (las fichas
  externas se reducen ~8 % para que no las pisen). Marca, color y pie en `config.py` (`FINCAS_MARCA`, `FINCAS_PIE`...).
- **Expediente** `FER-AAAA-NNNN` correlativo por año, generado al lanzar el lote (`--expediente` lo fuerza). Libro de expedientes en
  `<carpeta clientes>/expedientes.csv` (fuera del repo): no borrarlo, de él sale la numeración. Cada relanzamiento gasta un número.
- **Fecha de consulta**: `consultado` en `DatosCatastro` y `ResultadoSigpac` (se guarda en la caché; en entradas antiguas se usa la
  fecha del fichero de caché). El dossier y el Excel muestran la fecha REAL de la consulta, no la de emisión.
- **Revisión de código 2026-11-12** (PR "revisión entrada y robustez"). Entrada: separador CSV por recuento (el Sniffer fallaba
  con una línea de título), cabeceras por palabra clave ('Polígono nº', 'Superficie parcela', '% propiedad'), superficie en ha/áreas
  convertida a m² con aviso, ferrados/fanegas SIN conversión (varía por concello: no se inventa), filas sin municipio/provincia se
  conservan como SIN DATOS (antes se perdían), parcelas repetidas y columnas no reconocidas se avisan, cuotas 'mitad'/'0,5'/'50' bajo
  cabecera '%'; '3/2' no es cuota. Visor: la etiqueta exige también provincia y municipio. Ficha de respaldo (captura con relleno) en
  Galicia: aviso en registro y NO se cachea. Municipio resuelto por aproximación: aviso. Catastro por https (verificado).
- Riesgos conocidos sin resolver: ver "PENDIENTE" abajo.
- **Teselas de contornos de SIXPAC: `parcela@3857/{zoom=15}.{x}.{y}.geojson`. El primer número es el ZOOM, no la provincia.**
  Con la provincia solo funcionaba A Coruña (15) y el resto de Galicia caía a la captura del visor (parcela rellena de azul, no vale:
  tapa la ortofoto). Corregido 2026-11-12; caché de fichas subida a `sigpac_v3` para regenerar las fichas azules.
  La ficha buena es SIEMPRE contorno amarillo sin relleno; si en el registro sale "plano propio no disponible", investigar.
- `mapa_conjunto.pdf` y hoja `Distancias` del Excel (`--sin-mapa` lo omite). Distancias en línea recta entre centros de la caja de
  cada parcela (orientativas). Fincas sin geometría o fuera de Galicia se omiten con aviso. Verificado con 31 fincas reales
  (31 peticiones + ~20 teselas, ~1 min). Si hay números solapados en zonas densas, es esperable (posición = centro de la caja).
- `informe.pdf` se genera al final de cada lote (`--sin-informe` lo omite). Pesa lo que suman los PDF (~60 MB con 31 fincas).
  reportlab fijado a <5 (la 5.x exige Python 3.10; el proyecto es 3.9).
- Python 3.9 compatible (`from __future__ import annotations`). Entorno: `.venv`.
- Servicios inyectados en `pipeline.ejecutar` → los tests usan dobles; los tests no tocan red.
- Caché global en `.cache/` (clave = prov+muni INE + polígono + parcela). Solo se cachean éxitos; los fallos
  se reintentan en la siguiente ejecución. `--sin-cache` la ignora. Los PDF se copian de la caché a la carpeta.
- Los códigos se resuelven **todos antes** de consultar parcelas, para preguntar ambigüedades al inicio.
  Ambigüedad → `Ambiguo` → la CLI pregunta (o aborta si no hay terminal). Sin coincidencia → esas filas
  quedan con error y se sigue.
- SIGPAC: provincia INE (2) + municipio con el código del CATASTRO (`cmc`), NO el INE. Coinciden en
  Vilanova (61) pero no en Ortigueira (INE 61, Catastro 62). `Codigos` guarda ambos.
- Referencia de la parcela = 14 primeros caracteres; la de 20 (subparcelas) se guarda aparte.
- Superficie catastral = suma de subparcelas (`ssp`) si existen, si no `sfc`.
- Control de superficie: `OK` si coincide ±10 % con el valor del cliente o con cliente × denominador de la
  cuota; `REVISAR` si no. Añadido `SIN DATO CLIENTE` cuando el cliente no dio superficie (extensión menor).
- Coincidencia Catastro/SIGPAC: `SI` / `NO` / `NO ENCONTRADA`.
- Nombres de PDF: `municipio-slug_poligonoXX_parcelaXXX_{catastro|sigpac}.pdf`.

## VERIFICADO con consultas reales (2026-11-11)
- Host `http://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx`: `ConsultaProvincia`,
  `ConsultaMunicipio`, `Consulta_DNPPP`, `Consulta_DNPRC` responden; esquema XML confirmado (fixtures en
  `tests/fixtures/*_real.xml`). Nombres oficiales: "A CORUÑA", "ALACANT" (no "CORUÑA, A").
- `Consulta_DNPPP` devuelve una lista SIN superficie ni cultivos; hay que llamar a `Consulta_DNPRC` con la
  referencia de 20 car. de la parte rústica (la de `locs/lors`). Cada parcela = 2 consultas.
- Una parcela puede mezclar inmuebles rústico + urbano (sufijo 0001...): solo se usa el rústico.
- Superficie: `sfc` del rústico suele ser 0; la fiable es la suma de `ssp` (subparcelas). La página de la
  Sede muestra la superficie TOTAL de la parcela (856 m² en la de prueba vs 393 m² rústicos): ojo en el
  control de superficie (puede dar REVISAR por incluir parte urbana).
- Para buscar parcelas de prueba reales: `OVCCoordenadas.asmx/Consulta_RCCOOR` (SRS, Coordenada_X/Y).
- `SECImprimirCroquisYDatos.aspx` NO es público (pide certificado/Cl@ve). La consulta pública
  `OVCListaBienes.aspx?rc1=&rc2=&del=&mun=` sí carga sin login, con botones postback (`btnPDFFXCC`...).

- **PDF de la Sede VERIFICADO de extremo a extremo** (parcela 36061A02800236): se abre
  `OVCConCiud.aspx?del=&mun=&UrbRus=R&RefC=<20 car.>...` y el botón `#BImpCroquisYDatos` descarga el PDF
  (~400 KB) sin captcha ni login. Se rechazan las cookies. Playwright + Chromium instalados en `.venv`.
- Visor SIGPAC (`https://sigpac.mapa.gob.es/fega/visor/`, "Visor SigPac V 4.20") carga con Playwright.
  Campos de búsqueda por localización (ocultos hasta abrir el panel): `#dirProv #dirMun #dirAgr #dirZona
  #dirPol #dirPar #dirRec`; por referencia catastral: `#dirCpProv #dirCpMun #dirCpAgr #dirCpZona #dirCpPol
  #dirCpParRem #dirCpSub` (esos ids se leyeron, aún no se han usado).

- **Visor verificado de extremo a extremo** (FEGA v4.20; en Galicia el SIGPAC del FEGA daba error, se usa SIXPAC
  de la Xunta). Flujo documentado en la cabecera de `sigpac.py`: búsqueda Directa, impresión
  nativa del visor (PDF ortofoto+parcelario, ~1,5 MB), y referencia catastral vía Consultas > Catastro (U)
  (abre la Sede con `RefC=`). Se controla con coordenadas de pantalla (viewport 1400x900).
- Prueba real completa (1 parcela, ~1 min): Excel + 2 PDF + registro + ZIP, `coincide = SI`.

- **SIXPAC (Xunta) = mismo visor** (v4.18, FOGGA) que el SIGPAC del FEGA, mismos datos (misma ref. catastral y
  517,8 m² para Ortigueira 34/175). **En Galicia (provincias 15/27/32/36) usar SIEMPRE SIXPAC** (`sigpac.url_visor`): el SIGPAC del FEGA daba error ahí y SIXPAC no falla.
- El PDF impreso por el visor es un recorte 1:500 SIN la parcela marcada (inútil en parcelas pequeñas).
  Ahora la ficha se genera en `sigpac._ficha_pdf`: captura del mapa con la parcela resaltada y rotulada
  (herramienta Consultas>Parcela) + tabla de datos. El panel de búsqueda se oculta alternando los prismáticos.

- **Lote de prueba real (31 parcelas, un solo municipio de A Coruña) completado y verificado de extremo a extremo** con SIXPAC (provincia 15):
  31/31 con `coincide = SI`. Parcelas grandes: el visor da la superficie en hectáreas (`2.0601 ha`); `sigpac.etiqueta_coincide`
  ya convierte ha -> m². (Antes se daban por NO ENCONTRADA: no era monte, era el parser.)

- **Ficha SIXPAC rehecha (2026-11-12)**: la captura de pantalla salía cortada y con relleno azul. Ahora `plano.py` usa los
  servicios del visor (`vispac.xunta.gal`: `servis/query/parcelabox/...` bbox lon/lat; `sdg-vector/vector/parcela@3857/{prov}.{x}.{y}.geojson`
  teselas z15 con y TMS; `sdg-raster/wmts?layer=ortofotos` XYZ hasta z19) → encuadre automático, contorno sin relleno, vecinos,
  escala y norte, mapa de situación. Si falla, se usa la captura antigua. Caché de SIGPAC versionada (`sigpac_v2`).
- Entrada: columna de referencia catastral rústica (20 car.) sustituye a polígono+parcela; filas sin datos se conservan en el Excel
  con `SIN DATOS` (no se buscan). Alias `nombre finca` -> paraje.
- Ejecutar SIEMPRE sin `--visible` (headless, no molesta; el usuario sigue trabajando en el ordenador).
  `--visible` solo si hay un captcha o hay que depurar un cambio del visor, y solo a petición del usuario.

## PENDIENTE DE VERIFICAR
0. De la revisión 2026-11-12: (a) la caché no caduca (la fecha de consulta ya se guarda y se muestra, pero un dato antiguo no se
   refresca solo: usar --sin-cache si hace falta); (b) el ZIP duplica peso (informe.pdf + PDF sueltos, ~120 MB con 31
   fincas); (c) fuera de Galicia la ficha SIGPAC es siempre la captura con relleno (no hay plano de contorno); (d) no se compara la
   superficie de SIXPAC con la del Catastro; (e) 'por uso' agrupa por la cadena de cultivos, no por subparcela; (f) la superficie
   total no aplica la cuota del cliente; (g) esperas fijas en el visor (~1 min/parcela); (h) '1,234' se lee como 1,234 m² (se avisa).
1. Parcela inexistente en SIGPAC (monte/forestal): se supone que la etiqueta pol:parc nunca aparece y se marca
   NO ENCONTRADA tras ~25 clics (lento); comprobar con un caso real.
2. Polígonos/parcelas con letras (no numéricos) se marcan NO ENCONTRADA en SIGPAC sin consultar.
3. Cuota/parcelas en lote: tiempo real ~1 min por parcela (pausas + visor).

Procedimiento de verificación: `--visible --prueba 1` con una parcela conocida, guardar respuestas reales
como fixtures en `tests/`, y ajustar solo el módulo afectado.

## Comandos
```bash
.venv/bin/python -m pytest -q                       # tests (sin red)
.venv/bin/python -m fincas tests/ejemplo.csv --simular   # plan sin consultas
```

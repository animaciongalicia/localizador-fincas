# CLAUDE.md — Búsqueda de fincas rústicas

## Objetivo
CLI reutilizable para cualquier cliente. Entrada: lista de fincas rústicas de España
(municipio, provincia, polígono, parcela). Salida: carpeta `salidas/AAAA-MM-DD_nombre-trabajo/` con
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
| `informe.py` | `informe.pdf`: portada+resumen, índice y todas las fichas con marcadores. Une PDF ya descargados (pypdf + reportlab) |
| `superficie.py` | Control de superficie (margen 10 %, cuota) |
| `pipeline.py` | Orquestación; servicios inyectados (testeable sin red) |
| `red.py` / `navegador.py` / `cache.py` / `registro.py` | pausas+reintentos+HTTP / Chromium compartido / caché / registro.txt |
| `config.py` | URLs, constantes (sobreescribibles por variable de entorno) |

## Decisiones técnicas
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

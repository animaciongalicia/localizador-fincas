# Búsqueda de fincas e inmuebles (Catastro + SIGPAC + Xunta + IGME)

Dada una lista de propiedades de cualquier tipo genera un informe con los datos y la ficha del Catastro de cada
una, un Excel, un mapa de conjunto y un dossier en PDF.

- **Fincas rústicas** (montes, prados, labradíos): por municipio, provincia, polígono y parcela, o por su
  referencia catastral. Además de Catastro llevan la ficha SIGPAC/SIXPAC con el contorno sobre la ortofoto.
- **Inmuebles urbanos** (casas, solares, naves, lo que tengan encima): por su referencia catastral. Llevan uso,
  superficie construida, año y destinos de la construcción.
- **En Galicia**, para todas: cobertura forestal actual (Inventario Forestal Continuo), condicionantes de una corta
  o una venta (afecciones de la Xunta) y derechos mineros vigentes.
- **En toda España**: contexto geológico y minero del IGME-CSIC (litología, indicios mineros a menos de 5 km,
  Atlas Geoquímico). Orientativo.

Nunca se obtienen titulares ni valor catastral (datos protegidos). Las fuentes están verificadas con consultas
reales (ver `CLAUDE.md`).

## Instalación
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

## Entrada
CSV, XLSX o DOCX (tabla de Word). Columnas en cualquier orden y con nombres flexibles
(`poligono/polígono`, `municipio/concello`, ...):

| obligatorias | opcionales |
|---|---|
| municipio*, provincia*, poligono, parcela | paraje, superficie_m2, cuota (`1/5`, `1/2`, `20%`, `completa`), notas |

\* pueden darse por parámetro (`--municipio`, `--provincia`). Si el municipio solo está en la primera fila, se
rellena hacia abajo. Un mismo fichero puede mezclar municipios. Ejemplo: `tests/ejemplo.csv`.

## Uso
```bash
# Ejecución completa (siempre en segundo plano, sin ventana)
.venv/bin/python -m fincas lista.xlsx --nombre cliente-perez

# Opcional: ver el plan sin hacer ninguna consulta
.venv/bin/python -m fincas lista.xlsx --simular

# Opcional: solo las N primeras parcelas
.venv/bin/python -m fincas lista.xlsx --nombre cliente-perez --prueba 2

# Fichero sin municipio/provincia en columnas
.venv/bin/python -m fincas lista.docx --municipio "Vilanova de Arousa" --provincia Pontevedra

# Solo Catastro, sin ignorar la caché
.venv/bin/python -m fincas lista.csv --sin-sigpac --sin-cache
```

Opciones: `--cliente` (salidas en `~/Desktop/BUSQUEDAS FERRADOS-CLAUDE/<cliente>/salidas/`), `--nombre`, `--municipio`, `--provincia`, `--prueba N`, `--salidas DIR`, `--simular`, `--sin-sigpac`,
`--sin-pdf-catastro`, `--sin-cache`, `--expediente`, `--sin-mapa`, `--sin-inventario`, `--sin-corta`, `--sin-geologia`, `--sin-informe`, `--visible`, `--pausa-min`, `--pausa-max`.

## Salida
```
salidas/2026-11-11_cliente-perez/
├── resultados.xlsx     # una fila por parcela; filas con cuota ≠ completa en amarillo
├── pdf_catastro/       # municipio_poligono05_parcela123_catastro.pdf
├── pdf_sigpac/         # municipio_poligono05_parcela123_sigpac.pdf
└── registro.txt        # errores y parcelas a revisar a mano
salidas/2026-11-11_cliente-perez.zip
```

## Comportamiento
- Pausa de 3–5 s entre consultas, 2 reintentos, caché en `.cache/` (relanzar no repite lo ya descargado).
- Una parcela que falla no detiene las demás. Nada se descarta: la cuota y el control de superficie solo marcan.
- Ambigüedad en provincia/municipio: se detiene y pregunta.
- Si la Sede pide captcha no se salta: queda en el registro para hacerlo a mano.

## Tests
```bash
.venv/bin/python -m pytest -q
```

## Salida
`resultados.xlsx` (hoja **Resumen** con totales y hoja **Fincas** con enlaces a cada PDF y a la Sede del Catastro), `pdf_catastro/`, `pdf_sigpac/`, `registro.txt`, **`mapa_conjunto.pdf`** (todas las fincas en un mapa sobre la ortofoto de SIXPAC, con números, leyenda y vecina más cercana; solo Galicia; la hoja *Distancias* del Excel trae la matriz en km), **`informe.pdf`** (portada con resumen, índice y todas las fichas, con marcadores) y el ZIP.

El `informe.pdf` es un dossier con marca: portada con cliente, nº de expediente (`FER-AAAA-NNNN`, automático) y fecha; cabecera y pie en todas las páginas; una hoja por finca con la trazabilidad de cada consulta; metodología y aviso legal. La marca y el pie se cambian en `fincas/config.py`.

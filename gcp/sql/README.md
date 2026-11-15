# BigQuery ArquiFusion

Orden de ejecucion:
1. `gcp/sql/crear_tablas.ps1`: crea el dataset `arquifusion` y las tablas.
2. `gcp/sql/cargar_historico.ps1`: carga los CSV de `simulador/salida/`.
3. `gcp/sql/verificar.sql`: ejecuta las consultas de control.

Los scripts leen el project ID con `gcloud config get-value project`; no esta fijo.
Dataset: `arquifusion` | Ubicacion: `US`.

`cargar_historico.ps1` usa `--replace`: reemplaza la tabla con el historico.
La ingesta en vivo debe hacer append y deduplicar por `maquina_id + ts`.
Para otra carpeta de CSV: `.\cargar_historico.ps1 -Carpeta "C:\ruta\csv"`.

El contexto del contrato esta aplanado en `contexto_clima`, `contexto_terreno` y `contexto_lote_combustible`.
`telemetria.ingest_ts` se llena con `CURRENT_TIMESTAMP()` y no viene en el CSV.
`scores` guarda `tipo`, `generado_ts`, `version_modelo` y `registro` JSON.

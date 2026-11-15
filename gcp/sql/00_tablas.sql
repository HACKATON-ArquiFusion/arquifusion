-- Dataset: arquifusion | Ubicacion: US
-- Todas las columnas son NULLABLE por defecto; no se usa NOT NULL.

-- Telemetria: lecturas historicas y en vivo de cada maquina.
-- Incluye el contexto aplanado y las columnas de entrenamiento del simulador.
CREATE TABLE IF NOT EXISTS `arquifusion.telemetria` (
  maquina_id STRING,
  ts TIMESTAMP,
  modelo STRING,
  zona STRING,
  lat FLOAT64,
  lon FLOAT64,
  horas_op FLOAT64,
  temp FLOAT64,
  vib FLOAT64,
  presion FLOAT64,
  combustible_lph FLOAT64,
  carga_t FLOAT64,
  velocidad FLOAT64,
  rpm INT64,
  estado STRING,
  contexto_clima STRING,
  contexto_terreno STRING,
  contexto_lote_combustible STRING,
  etiqueta_falla_7d INT64,
  dias_a_falla FLOAT64,
  componente_falla STRING,
  escenario STRING,
  grupo_id STRING,
  falla_id STRING,
  ingest_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(ts)
CLUSTER BY maquina_id, zona
OPTIONS(description = "Telemetria historica y en vivo; contexto aplanado y etiquetas de entrenamiento.");

-- Fallas: eventos de falla inyectados por el simulador y su ventana de deriva.
CREATE TABLE IF NOT EXISTS `arquifusion.fallas` (
  falla_id STRING,
  maquina_id STRING,
  zona STRING,
  componente STRING,
  escenario STRING,
  ts_inicio_deriva TIMESTAMP,
  ts_falla TIMESTAMP,
  ts_reparacion TIMESTAMP,
  grupo_id STRING,
  causa_verdadera STRING
)
PARTITION BY DATE(ts_falla)
CLUSTER BY maquina_id
OPTIONS(description = "Fallas reales simuladas; particion por dia de falla.");

-- Incidentes: registro operativo asociado a una falla o intervencion.
CREATE TABLE IF NOT EXISTS `arquifusion.incidentes` (
  incidente_id STRING,
  maquina_id STRING,
  ts TIMESTAMP,
  severidad STRING,
  estado STRING,
  descripcion STRING
)
PARTITION BY DATE(ts)
CLUSTER BY maquina_id
OPTIONS(description = "Incidentes abiertos y cerrados por maquina.");

-- Mantenimientos: preventivos y correctivos realizados por maquina.
CREATE TABLE IF NOT EXISTS `arquifusion.mantenimientos` (
  mantenimiento_id STRING,
  maquina_id STRING,
  ts TIMESTAMP,
  tipo_mantenimiento STRING,
  estado STRING,
  componentes STRING
)
PARTITION BY DATE(ts)
CLUSTER BY maquina_id
OPTIONS(description = "Mantenimientos; componentes es una lista separada por punto y coma.");

-- Consumo_repuestos: piezas usadas en cada mantenimiento.
CREATE TABLE IF NOT EXISTS `arquifusion.consumo_repuestos` (
  consumo_id STRING,
  maquina_id STRING,
  ts TIMESTAMP,
  componente STRING,
  cantidad INT64,
  unidad STRING
)
PARTITION BY DATE(ts)
CLUSTER BY componente
OPTIONS(description = "Consumo de repuestos por componente; el CSV no trae zona.");

-- Scores: registros que B publica hacia A a traves del puente /scores.
CREATE TABLE IF NOT EXISTS `arquifusion.scores` (
  tipo STRING,
  generado_ts TIMESTAMP,
  version_modelo STRING,
  registro JSON
)
PARTITION BY DATE(generado_ts)
CLUSTER BY tipo
OPTIONS(description = "Scores publicados por B; registro conserva el JSON original.");

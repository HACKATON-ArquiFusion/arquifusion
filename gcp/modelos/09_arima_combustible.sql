-- 09_arima_combustible.sql
-- Tendencia y anomalias de consumo de combustible por maquina (RF-06) con ARIMA_PLUS.
-- Serie diaria: promedio de combustible_lph solo en lecturas con la maquina operando.
-- Los dias sin lecturas operando quedan ausentes de la serie.
-- Version _eval: entrenada con dias < 2026-11-01; version final con todos los datos.

CREATE OR REPLACE VIEW `arquifusion.v_combustible_dia` AS
SELECT
  maquina_id,
  DATE(ts) AS dia,
  AVG(combustible_lph) AS consumo
FROM `arquifusion.telemetria`
WHERE maquina_id IS NOT NULL
  AND ts IS NOT NULL
  AND IFNULL(estado, '') <> 'detenida'
  AND combustible_lph > 0
GROUP BY maquina_id, DATE(ts);

CREATE OR REPLACE MODEL `arquifusion.modelo_arima_combustible_eval`
OPTIONS(
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'dia',
  time_series_data_col = 'consumo',
  time_series_id_col = 'maquina_id',
  data_frequency = 'DAILY',
  auto_arima = TRUE
) AS
SELECT
  maquina_id,
  dia,
  consumo
FROM `arquifusion.v_combustible_dia`
WHERE dia < DATE '2026-11-01';

CREATE OR REPLACE MODEL `arquifusion.modelo_arima_combustible`
OPTIONS(
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'dia',
  time_series_data_col = 'consumo',
  time_series_id_col = 'maquina_id',
  data_frequency = 'DAILY',
  auto_arima = TRUE
) AS
SELECT
  maquina_id,
  dia,
  consumo
FROM `arquifusion.v_combustible_dia`;

CREATE TABLE IF NOT EXISTS `arquifusion.metricas_arima` (
  modelo STRING,
  n_series INT64,
  horizonte_dias INT64,
  mae FLOAT64,
  wape FLOAT64,
  wape_baseline FLOAT64,
  mejora_pct FLOAT64,
  creado_ts TIMESTAMP
);

DELETE FROM `arquifusion.metricas_arima`
WHERE modelo = 'arima_combustible';

INSERT INTO `arquifusion.metricas_arima`
  (modelo, n_series, horizonte_dias, mae, wape, wape_baseline, mejora_pct, creado_ts)
WITH fc AS (
  SELECT
    maquina_id,
    DATE(forecast_timestamp) AS dia,
    GREATEST(0.0, forecast_value) AS pron
  FROM ML.FORECAST(
    MODEL `arquifusion.modelo_arima_combustible_eval`,
    STRUCT(14 AS horizon, 0.9 AS confidence_level)
  )
),
base AS (
  SELECT
    maquina_id,
    AVG(consumo) AS media14
  FROM `arquifusion.v_combustible_dia`
  WHERE dia >= DATE '2026-10-18'
    AND dia < DATE '2026-11-01'
  GROUP BY maquina_id
),
cmp AS (
  SELECT
    r.maquina_id,
    r.consumo AS real_v,
    f.pron,
    b.media14
  FROM `arquifusion.v_combustible_dia` r
  JOIN fc f
    ON f.maquina_id = r.maquina_id
   AND f.dia = r.dia
  JOIN base b
    ON b.maquina_id = r.maquina_id
  WHERE r.dia >= DATE '2026-11-01'
    AND r.dia <= DATE '2026-11-14'
),
agg AS (
  SELECT
    COUNT(DISTINCT maquina_id) AS n_series,
    AVG(ABS(real_v - pron)) AS mae,
    SAFE_DIVIDE(SUM(ABS(real_v - pron)), SUM(real_v)) AS wape,
    SAFE_DIVIDE(SUM(ABS(real_v - media14)), SUM(real_v)) AS wape_baseline
  FROM cmp
)
SELECT
  'arima_combustible',
  n_series,
  14,
  mae,
  wape,
  wape_baseline,
  SAFE_DIVIDE(wape_baseline - wape, wape_baseline) * 100.0,
  CURRENT_TIMESTAMP()
FROM agg;

SELECT
  modelo,
  n_series,
  horizonte_dias,
  ROUND(mae, 4) AS mae,
  ROUND(wape, 4) AS wape,
  ROUND(wape_baseline, 4) AS wape_baseline,
  ROUND(mejora_pct, 1) AS mejora_pct
FROM `arquifusion.metricas_arima`
WHERE modelo = 'arima_combustible';

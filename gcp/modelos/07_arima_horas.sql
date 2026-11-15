-- 07_arima_horas.sql
-- Pronostico de horas de uso por maquina (RF-02) con ARIMA_PLUS.
-- Serie diaria: horas operativas = lecturas horarias con estado distinto de detenida.
-- Version _eval: entrenada con dias < 2026-11-01 para evaluar contra 2026-11-01..2026-11-14.
-- Version final: entrenada con todos los datos (la usa 10_pronosticos.sql).

CREATE OR REPLACE VIEW `arquifusion.v_horas_dia` AS
SELECT
  maquina_id,
  DATE(ts) AS dia,
  CAST(COUNTIF(IFNULL(estado, '') <> 'detenida') AS FLOAT64) AS horas
FROM `arquifusion.telemetria`
WHERE maquina_id IS NOT NULL
  AND ts IS NOT NULL
GROUP BY maquina_id, DATE(ts);

CREATE OR REPLACE MODEL `arquifusion.modelo_arima_horas_eval`
OPTIONS(
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'dia',
  time_series_data_col = 'horas',
  time_series_id_col = 'maquina_id',
  data_frequency = 'DAILY',
  auto_arima = TRUE
) AS
SELECT
  maquina_id,
  dia,
  horas
FROM `arquifusion.v_horas_dia`
WHERE dia < DATE '2026-11-01';

CREATE OR REPLACE MODEL `arquifusion.modelo_arima_horas`
OPTIONS(
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'dia',
  time_series_data_col = 'horas',
  time_series_id_col = 'maquina_id',
  data_frequency = 'DAILY',
  auto_arima = TRUE
) AS
SELECT
  maquina_id,
  dia,
  horas
FROM `arquifusion.v_horas_dia`;

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
WHERE modelo = 'arima_horas';

INSERT INTO `arquifusion.metricas_arima`
  (modelo, n_series, horizonte_dias, mae, wape, wape_baseline, mejora_pct, creado_ts)
WITH fc AS (
  SELECT
    maquina_id,
    DATE(forecast_timestamp) AS dia,
    GREATEST(0.0, forecast_value) AS pron
  FROM ML.FORECAST(
    MODEL `arquifusion.modelo_arima_horas_eval`,
    STRUCT(14 AS horizon, 0.9 AS confidence_level)
  )
),
base AS (
  SELECT
    maquina_id,
    AVG(horas) AS media14
  FROM `arquifusion.v_horas_dia`
  WHERE dia >= DATE '2026-10-18'
    AND dia < DATE '2026-11-01'
  GROUP BY maquina_id
),
cmp AS (
  SELECT
    r.maquina_id,
    r.horas AS real_v,
    f.pron,
    b.media14
  FROM `arquifusion.v_horas_dia` r
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
  'arima_horas',
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
WHERE modelo = 'arima_horas';

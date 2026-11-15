-- 08_arima_repuestos.sql
-- Pronostico de demanda de repuestos por componente (RF-03) con ARIMA_PLUS.
-- Serie diaria por componente: suma de cantidad, con 0 en los dias sin consumo.
-- La zona se reparte despues (10_pronosticos.sql) segun la proporcion de consumo de los ultimos 30 dias.
-- Version _eval: entrenada con dias < 2026-11-01; version final con todos los datos.

CREATE OR REPLACE VIEW `arquifusion.v_repuestos_dia` AS
WITH limites AS (
  SELECT
    MIN(DATE(ts)) AS d0,
    MAX(DATE(ts)) AS d1
  FROM `arquifusion.telemetria`
),
comps AS (
  SELECT DISTINCT componente
  FROM `arquifusion.consumo_repuestos`
  WHERE componente IS NOT NULL
),
dias AS (
  SELECT dia
  FROM limites l, UNNEST(GENERATE_DATE_ARRAY(l.d0, l.d1)) AS dia
),
consumo AS (
  SELECT
    componente,
    DATE(ts) AS dia,
    SUM(cantidad) AS demanda
  FROM `arquifusion.consumo_repuestos`
  WHERE componente IS NOT NULL
  GROUP BY componente, DATE(ts)
)
SELECT
  c.componente,
  d.dia,
  CAST(IFNULL(k.demanda, 0) AS FLOAT64) AS demanda
FROM comps c
CROSS JOIN dias d
LEFT JOIN consumo k
  ON k.componente = c.componente
 AND k.dia = d.dia;

CREATE OR REPLACE MODEL `arquifusion.modelo_arima_repuestos_eval`
OPTIONS(
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'dia',
  time_series_data_col = 'demanda',
  time_series_id_col = 'componente',
  data_frequency = 'DAILY',
  auto_arima = TRUE
) AS
SELECT
  componente,
  dia,
  demanda
FROM `arquifusion.v_repuestos_dia`
WHERE dia < DATE '2026-11-01';

CREATE OR REPLACE MODEL `arquifusion.modelo_arima_repuestos`
OPTIONS(
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'dia',
  time_series_data_col = 'demanda',
  time_series_id_col = 'componente',
  data_frequency = 'DAILY',
  auto_arima = TRUE
) AS
SELECT
  componente,
  dia,
  demanda
FROM `arquifusion.v_repuestos_dia`;

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
WHERE modelo = 'arima_repuestos';

INSERT INTO `arquifusion.metricas_arima`
  (modelo, n_series, horizonte_dias, mae, wape, wape_baseline, mejora_pct, creado_ts)
WITH fc AS (
  SELECT
    componente,
    DATE(forecast_timestamp) AS dia,
    GREATEST(0.0, forecast_value) AS pron
  FROM ML.FORECAST(
    MODEL `arquifusion.modelo_arima_repuestos_eval`,
    STRUCT(14 AS horizon, 0.9 AS confidence_level)
  )
),
base AS (
  SELECT
    componente,
    AVG(demanda) AS media14
  FROM `arquifusion.v_repuestos_dia`
  WHERE dia >= DATE '2026-10-18'
    AND dia < DATE '2026-11-01'
  GROUP BY componente
),
cmp AS (
  SELECT
    r.componente,
    r.demanda AS real_v,
    f.pron,
    b.media14
  FROM `arquifusion.v_repuestos_dia` r
  JOIN fc f
    ON f.componente = r.componente
   AND f.dia = r.dia
  JOIN base b
    ON b.componente = r.componente
  WHERE r.dia >= DATE '2026-11-01'
    AND r.dia <= DATE '2026-11-14'
),
agg AS (
  SELECT
    COUNT(DISTINCT componente) AS n_series,
    AVG(ABS(real_v - pron)) AS mae,
    SAFE_DIVIDE(SUM(ABS(real_v - pron)), SUM(real_v)) AS wape,
    SAFE_DIVIDE(SUM(ABS(real_v - media14)), SUM(real_v)) AS wape_baseline
  FROM cmp
)
SELECT
  'arima_repuestos',
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
WHERE modelo = 'arima_repuestos';

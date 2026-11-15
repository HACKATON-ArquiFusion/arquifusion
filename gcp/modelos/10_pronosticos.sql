-- 10_pronosticos.sql
-- Genera las tablas que se publicaran a A por /scores. Horizonte de 14 dias, intervalo de confianza 0.9.
-- Usa los modelos finales de los archivos 07, 08 y 09 y los modelos de falla del 02 y 03.

-- 1) Horas de uso previstas por maquina (RF-02)
CREATE OR REPLACE TABLE `arquifusion.pronostico_horas_uso` AS
SELECT
  maquina_id,
  DATE(forecast_timestamp) AS fecha,
  GREATEST(0.0, LEAST(24.0, forecast_value)) AS horas_prevista,
  GREATEST(0.0, prediction_interval_lower_bound) AS limite_inf,
  LEAST(24.0, prediction_interval_upper_bound) AS limite_sup
FROM ML.FORECAST(
  MODEL `arquifusion.modelo_arima_horas`,
  STRUCT(14 AS horizon, 0.9 AS confidence_level)
);

-- 2) Demanda de repuestos prevista por componente y zona (RF-03)
-- La zona de cada maquina sale de telemetria; el consumo se reparte por la proporcion de los ultimos 30 dias.
CREATE OR REPLACE TABLE `arquifusion.pronostico_repuestos` AS
WITH zona_maq AS (
  SELECT
    maquina_id,
    MAX(zona) AS zona
  FROM `arquifusion.telemetria`
  WHERE maquina_id IS NOT NULL
  GROUP BY maquina_id
),
fin AS (
  SELECT MAX(DATE(ts)) AS dmax
  FROM `arquifusion.telemetria`
),
consumo30 AS (
  SELECT
    c.componente,
    z.zona,
    SUM(c.cantidad) AS cant
  FROM `arquifusion.consumo_repuestos` c
  JOIN zona_maq z
    ON z.maquina_id = c.maquina_id
  CROSS JOIN fin
  WHERE DATE(c.ts) > DATE_SUB(fin.dmax, INTERVAL 30 DAY)
  GROUP BY c.componente, z.zona
),
reparto AS (
  SELECT
    componente,
    zona,
    SAFE_DIVIDE(cant, SUM(cant) OVER (PARTITION BY componente)) AS proporcion
  FROM consumo30
),
fc AS (
  SELECT
    componente,
    DATE(forecast_timestamp) AS fecha,
    GREATEST(0.0, forecast_value) AS demanda,
    GREATEST(0.0, prediction_interval_lower_bound) AS lim_inf,
    GREATEST(0.0, prediction_interval_upper_bound) AS lim_sup
  FROM ML.FORECAST(
    MODEL `arquifusion.modelo_arima_repuestos`,
    STRUCT(14 AS horizon, 0.9 AS confidence_level)
  )
)
SELECT
  f.componente,
  r.zona,
  f.fecha,
  f.demanda * r.proporcion AS demanda_prevista,
  f.lim_inf * r.proporcion AS limite_inf,
  f.lim_sup * r.proporcion AS limite_sup
FROM fc f
JOIN reparto r
  ON r.componente = f.componente;

-- 3) Consumo de combustible esperado por maquina (RF-06)
CREATE OR REPLACE TABLE `arquifusion.pronostico_combustible` AS
SELECT
  maquina_id,
  DATE(forecast_timestamp) AS fecha,
  GREATEST(0.0, forecast_value) AS consumo_esperado_lph,
  GREATEST(0.0, prediction_interval_lower_bound) AS limite_inf,
  prediction_interval_upper_bound AS limite_sup
FROM ML.FORECAST(
  MODEL `arquifusion.modelo_arima_combustible`,
  STRUCT(14 AS horizon, 0.9 AS confidence_level)
);

-- 4) Anomalias de combustible de los ultimos 30 dias (RF-06)
-- consumo_esperado_lph es el centro del intervalo de prediccion del modelo.
CREATE OR REPLACE TABLE `arquifusion.anomalias_combustible` AS
WITH fin AS (
  SELECT MAX(dia) AS dmax
  FROM `arquifusion.v_combustible_dia`
),
real14 AS (
  SELECT
    c.maquina_id,
    AVG(c.consumo) AS media_real14
  FROM `arquifusion.v_combustible_dia` c
  CROSS JOIN fin
  WHERE c.dia > DATE_SUB(fin.dmax, INTERVAL 14 DAY)
  GROUP BY c.maquina_id
),
pron14 AS (
  SELECT
    maquina_id,
    AVG(consumo_esperado_lph) AS media_pron14
  FROM `arquifusion.pronostico_combustible`
  GROUP BY maquina_id
),
tend AS (
  SELECT
    r.maquina_id,
    CASE
      WHEN SAFE_DIVIDE(p.media_pron14, r.media_real14) > 1.05 THEN 'creciente'
      WHEN SAFE_DIVIDE(p.media_pron14, r.media_real14) < 0.95 THEN 'decreciente'
      ELSE 'estable'
    END AS tendencia
  FROM real14 r
  JOIN pron14 p
    ON p.maquina_id = r.maquina_id
),
an AS (
  SELECT
    maquina_id,
    dia,
    consumo,
    lower_bound,
    upper_bound,
    is_anomaly
  FROM ML.DETECT_ANOMALIES(
    MODEL `arquifusion.modelo_arima_combustible`,
    STRUCT(0.99 AS anomaly_prob_threshold)
  )
)
SELECT
  a.maquina_id,
  DATE(a.dia) AS fecha,
  a.consumo AS consumo_real_lph,
  (a.lower_bound + a.upper_bound) / 2.0 AS consumo_esperado_lph,
  SAFE_DIVIDE(
    a.consumo - (a.lower_bound + a.upper_bound) / 2.0,
    (a.lower_bound + a.upper_bound) / 2.0
  ) * 100.0 AS desviacion_pct,
  IFNULL(t.tendencia, 'estable') AS tendencia,
  IFNULL(a.is_anomaly, FALSE) AS anomalia
FROM an a
CROSS JOIN fin
LEFT JOIN tend t
  ON t.maquina_id = a.maquina_id
WHERE DATE(a.dia) > DATE_SUB(fin.dmax, INTERVAL 29 DAY);

-- 5) Mantenimiento preventivo propuesto (RF-02): cada 500 horas de operacion
CREATE OR REPLACE TABLE `arquifusion.preventivo_propuesto` AS
WITH ult AS (
  SELECT
    maquina_id,
    horas_op
  FROM (
    SELECT
      maquina_id,
      horas_op,
      ROW_NUMBER() OVER (PARTITION BY maquina_id ORDER BY ts DESC) AS rn
    FROM `arquifusion.telemetria`
    WHERE maquina_id IS NOT NULL
      AND horas_op IS NOT NULL
  )
  WHERE rn = 1
),
falt AS (
  SELECT
    maquina_id,
    horas_op,
    500.0 - (horas_op - 500.0 * FLOOR(horas_op / 500.0)) AS horas_faltantes
  FROM ult
),
acum AS (
  SELECT
    maquina_id,
    fecha,
    SUM(horas_prevista) OVER (PARTITION BY maquina_id ORDER BY fecha) AS acum_h
  FROM `arquifusion.pronostico_horas_uso`
),
resumen AS (
  SELECT
    maquina_id,
    MAX(fecha) AS fecha_fin,
    MAX(acum_h) AS total14,
    SAFE_DIVIDE(MAX(acum_h), COUNT(*)) AS prom_diario
  FROM acum
  GROUP BY maquina_id
),
hit AS (
  SELECT
    f.maquina_id,
    MIN(a.fecha) AS fecha_hit
  FROM falt f
  JOIN acum a
    ON a.maquina_id = f.maquina_id
   AND a.acum_h >= f.horas_faltantes
  GROUP BY f.maquina_id
)
SELECT
  f.maquina_id,
  COALESCE(
    h.fecha_hit,
    IF(
      r.prom_diario > 0,
      DATE_ADD(
        r.fecha_fin,
        INTERVAL CAST(CEIL((f.horas_faltantes - r.total14) / r.prom_diario) AS INT64) DAY
      ),
      NULL
    )
  ) AS fecha_recomendada,
  f.horas_op + f.horas_faltantes AS horas_proyectadas,
  CONCAT('Preventivo cada 500 h de operacion; faltan ', CAST(ROUND(f.horas_faltantes, 1) AS STRING), ' h') AS motivo
FROM falt f
LEFT JOIN resumen r
  ON r.maquina_id = f.maquina_id
LEFT JOIN hit h
  ON h.maquina_id = f.maquina_id;

-- 6) Probabilidad de falla actual por maquina (ultima lectura, ambos modelos)
CREATE OR REPLACE TABLE `arquifusion.predicciones_falla_actual` AS
WITH ult AS (
  SELECT
    maquina_id,
    ts,
    temp_m24_z,
    vib_m24_z,
    comb_m24_z,
    temp_d72_z,
    vib_d72_z,
    comb_d72_z,
    temp_d14_z,
    vib_d14_z,
    comb_d14_z
  FROM (
    SELECT
      maquina_id,
      ts,
      temp_m24_z,
      vib_m24_z,
      comb_m24_z,
      temp_d72_z,
      vib_d72_z,
      comb_d72_z,
      temp_d14_z,
      vib_d14_z,
      comb_d14_z,
      ROW_NUMBER() OVER (PARTITION BY maquina_id ORDER BY ts DESC) AS rn
    FROM `arquifusion.v_features_z`
  )
  WHERE rn = 1
),
logit AS (
  SELECT
    maquina_id,
    (SELECT p.prob FROM UNNEST(predicted_etiqueta_probs) AS p WHERE p.label = 1) AS prob_logit
  FROM ML.PREDICT(
    MODEL `arquifusion.modelo_falla_logit`,
    (
      SELECT
        maquina_id,
        temp_m24_z,
        vib_m24_z,
        comb_m24_z,
        temp_d72_z,
        vib_d72_z,
        comb_d72_z,
        temp_d14_z,
        vib_d14_z,
        comb_d14_z
      FROM ult
    )
  )
),
boosted AS (
  SELECT
    maquina_id,
    (SELECT p.prob FROM UNNEST(predicted_etiqueta_probs) AS p WHERE p.label = 1) AS prob_boosted
  FROM ML.PREDICT(
    MODEL `arquifusion.modelo_falla_boosted`,
    (
      SELECT
        maquina_id,
        temp_m24_z,
        vib_m24_z,
        comb_m24_z,
        temp_d72_z,
        vib_d72_z,
        comb_d72_z,
        temp_d14_z,
        vib_d14_z,
        comb_d14_z
      FROM ult
    )
  )
)
SELECT
  u.maquina_id,
  u.ts,
  l.prob_logit,
  b.prob_boosted
FROM ult u
JOIN logit l
  ON l.maquina_id = u.maquina_id
JOIN boosted b
  ON b.maquina_id = u.maquina_id;

-- Control: conteos
SELECT 'pronostico_horas_uso' AS tabla, COUNT(*) AS filas FROM `arquifusion.pronostico_horas_uso`
UNION ALL
SELECT 'pronostico_repuestos', COUNT(*) FROM `arquifusion.pronostico_repuestos`
UNION ALL
SELECT 'pronostico_combustible', COUNT(*) FROM `arquifusion.pronostico_combustible`
UNION ALL
SELECT 'anomalias_combustible', COUNT(*) FROM `arquifusion.anomalias_combustible`
UNION ALL
SELECT 'preventivo_propuesto', COUNT(*) FROM `arquifusion.preventivo_propuesto`
UNION ALL
SELECT 'predicciones_falla_actual', COUNT(*) FROM `arquifusion.predicciones_falla_actual`
ORDER BY tabla;

-- Control: ejemplos
SELECT
  maquina_id,
  fecha,
  ROUND(horas_prevista, 2) AS horas_prevista,
  ROUND(limite_inf, 2) AS limite_inf,
  ROUND(limite_sup, 2) AS limite_sup
FROM `arquifusion.pronostico_horas_uso`
ORDER BY maquina_id, fecha
LIMIT 5;

SELECT
  componente,
  zona,
  fecha,
  ROUND(demanda_prevista, 3) AS demanda_prevista
FROM `arquifusion.pronostico_repuestos`
ORDER BY componente, zona, fecha
LIMIT 5;

SELECT
  maquina_id,
  fecha,
  ROUND(consumo_real_lph, 2) AS consumo_real_lph,
  ROUND(consumo_esperado_lph, 2) AS consumo_esperado_lph,
  ROUND(desviacion_pct, 1) AS desviacion_pct,
  tendencia,
  anomalia
FROM `arquifusion.anomalias_combustible`
WHERE anomalia = TRUE
ORDER BY fecha DESC
LIMIT 5;

SELECT
  maquina_id,
  fecha_recomendada,
  ROUND(horas_proyectadas, 1) AS horas_proyectadas,
  motivo
FROM `arquifusion.preventivo_propuesto`
ORDER BY fecha_recomendada
LIMIT 5;

SELECT
  maquina_id,
  ts,
  ROUND(prob_logit, 4) AS prob_logit,
  ROUND(prob_boosted, 4) AS prob_boosted
FROM `arquifusion.predicciones_falla_actual`
ORDER BY prob_boosted DESC
LIMIT 5;

-- Validacion anti-fuga y de utilidad operativa sobre la particion test.
-- Para la evaluacion se utilizan los umbrales seleccionados en la particion val.
CREATE OR REPLACE TEMP TABLE umbrales_modelo AS
SELECT
  modelo,
  MAX(IF(particion = 'val', umbral, NULL)) AS umbral
FROM `arquifusion.metricas_modelos`
WHERE modelo IN ('logit', 'boosted')
GROUP BY modelo;

CREATE OR REPLACE TEMP TABLE pred_eval_logit AS
SELECT
  maquina_id,
  ts,
  etiqueta,
  particion,
  (SELECT p.prob FROM UNNEST(predicted_etiqueta_probs) AS p WHERE p.label = 1) AS prob
FROM ML.PREDICT(
  MODEL `arquifusion.modelo_falla_logit`,
  (
    SELECT
      maquina_id,
      ts,
      etiqueta,
      particion,
      temp_m24_z,
      vib_m24_z,
      comb_m24_z,
      temp_d72_z,
      vib_d72_z,
      comb_d72_z,
      temp_d14_z,
      vib_d14_z,
      comb_d14_z
    FROM `arquifusion.v_features_z`
    WHERE particion IN ('gap', 'test')
  )
);

CREATE OR REPLACE TEMP TABLE pred_eval_boosted AS
SELECT
  maquina_id,
  ts,
  etiqueta,
  particion,
  (SELECT p.prob FROM UNNEST(predicted_etiqueta_probs) AS p WHERE p.label = 1) AS prob
FROM ML.PREDICT(
  MODEL `arquifusion.modelo_falla_boosted`,
  (
    SELECT
      maquina_id,
      ts,
      etiqueta,
      particion,
      temp_m24_z,
      vib_m24_z,
      comb_m24_z,
      temp_d72_z,
      vib_d72_z,
      comb_d72_z,
      temp_d14_z,
      vib_d14_z,
      comb_d14_z
    FROM `arquifusion.v_features_z`
    WHERE particion IN ('gap', 'test')
  )
);

CREATE OR REPLACE TEMP TABLE pred_eval_todos AS
SELECT 'logit' AS modelo, maquina_id, ts, etiqueta, particion, prob
FROM pred_eval_logit
UNION ALL
SELECT 'boosted', maquina_id, ts, etiqueta, particion, prob
FROM pred_eval_boosted;

CREATE OR REPLACE TEMP TABLE muestra_logit AS
SELECT
  maquina_id,
  ts,
  etiqueta,
  temp_m24_z,
  vib_m24_z,
  comb_m24_z,
  temp_d72_z,
  vib_d72_z,
  comb_d72_z,
  temp_d14_z,
  vib_d14_z,
  comb_d14_z
FROM `arquifusion.v_features_z`
WHERE particion = 'test'
ORDER BY ts
LIMIT 20;

CREATE OR REPLACE TEMP TABLE pred_muestra_logit AS
SELECT
  maquina_id,
  ts,
  etiqueta,
  temp_m24_z,
  vib_m24_z,
  comb_m24_z,
  temp_d72_z,
  vib_d72_z,
  comb_d72_z,
  temp_d14_z,
  vib_d14_z,
  comb_d14_z,
  (SELECT p.prob FROM UNNEST(predicted_etiqueta_probs) AS p WHERE p.label = 1) AS prob_ml
FROM ML.PREDICT(
  MODEL `arquifusion.modelo_falla_logit`,
  (
    SELECT
      maquina_id,
      ts,
      etiqueta,
      temp_m24_z,
      vib_m24_z,
      comb_m24_z,
      temp_d72_z,
      vib_d72_z,
      comb_d72_z,
      temp_d14_z,
      vib_d14_z,
      comb_d14_z
    FROM `muestra_logit`
  )
);

-- 1) Sigmoide manual con ML.WEIGHTS contra la probabilidad de ML.PREDICT (9 variables).
WITH pesos AS (
  SELECT
    MAX(IF(processed_input = '__INTERCEPT__', weight, NULL)) AS intercepto,
    MAX(IF(processed_input = 'temp_m24_z', weight, NULL)) AS w_temp_m24,
    MAX(IF(processed_input = 'vib_m24_z', weight, NULL)) AS w_vib_m24,
    MAX(IF(processed_input = 'comb_m24_z', weight, NULL)) AS w_comb_m24,
    MAX(IF(processed_input = 'temp_d72_z', weight, NULL)) AS w_temp_d72,
    MAX(IF(processed_input = 'vib_d72_z', weight, NULL)) AS w_vib_d72,
    MAX(IF(processed_input = 'comb_d72_z', weight, NULL)) AS w_comb_d72,
    MAX(IF(processed_input = 'temp_d14_z', weight, NULL)) AS w_temp_d14,
    MAX(IF(processed_input = 'vib_d14_z', weight, NULL)) AS w_vib_d14,
    MAX(IF(processed_input = 'comb_d14_z', weight, NULL)) AS w_comb_d14
  FROM ML.WEIGHTS(MODEL `arquifusion.modelo_falla_logit`)
)
SELECT
  COUNT(*) AS filas,
  MAX(
    ABS(
      m.prob_ml - SAFE_DIVIDE(
        1.0,
        1.0 + EXP(-(
          p.intercepto
          + p.w_temp_m24 * m.temp_m24_z
          + p.w_vib_m24 * m.vib_m24_z
          + p.w_comb_m24 * m.comb_m24_z
          + p.w_temp_d72 * m.temp_d72_z
          + p.w_vib_d72 * m.vib_d72_z
          + p.w_comb_d72 * m.comb_d72_z
          + p.w_temp_d14 * m.temp_d14_z
          + p.w_vib_d14 * m.vib_d14_z
          + p.w_comb_d14 * m.comb_d14_z
        ))
      )
    )
  ) AS max_diferencia
FROM pred_muestra_logit m
CROSS JOIN pesos p;

-- 2) Sensibilidad por falla: una falla cuenta si hay alerta en los 7 dias previos.
WITH fallas_test AS (
  SELECT
    falla_id,
    maquina_id,
    ts_falla
  FROM `arquifusion.fallas`
  WHERE ts_falla >= (SELECT MIN(ts) FROM `arquifusion.v_features_z` WHERE particion = 'test')
),
detectadas AS (
  SELECT DISTINCT
    p.modelo,
    f.falla_id
  FROM fallas_test f
  JOIN pred_eval_todos p
    ON p.maquina_id = f.maquina_id
   AND p.ts >= TIMESTAMP_SUB(f.ts_falla, INTERVAL 7 DAY)
   AND p.ts < f.ts_falla
  JOIN umbrales_modelo u
    ON u.modelo = p.modelo
  WHERE p.prob >= u.umbral
)
SELECT
  u.modelo,
  COUNTIF(d.falla_id IS NOT NULL) AS fallas_detectadas,
  COUNT(f.falla_id) AS fallas_totales
FROM fallas_test f
CROSS JOIN umbrales_modelo u
LEFT JOIN detectadas d
  ON d.modelo = u.modelo
 AND d.falla_id = f.falla_id
GROUP BY u.modelo
ORDER BY u.modelo;

-- 3) Evaluacion por episodios de alertas confirmadas en la particion test.
WITH pred_test AS (
  SELECT
    p.modelo,
    p.maquina_id,
    p.ts,
    p.prob,
    u.umbral,
    COUNTIF(p.prob >= u.umbral) OVER (
      PARTITION BY p.modelo, p.maquina_id
      ORDER BY p.ts
      ROWS BETWEEN 5 PRECEDING AND CURRENT ROW
    ) >= 3 AS es_alerta_confirmada
  FROM pred_eval_todos p
  JOIN umbrales_modelo u ON u.modelo = p.modelo
  WHERE p.particion = 'test'
),
alertas_confirmadas AS (
  SELECT
    modelo,
    maquina_id,
    ts,
    IF(
      LAG(ts) OVER (PARTITION BY modelo, maquina_id ORDER BY ts) IS NULL
      OR TIMESTAMP_DIFF(ts, LAG(ts) OVER (PARTITION BY modelo, maquina_id ORDER BY ts), HOUR) >= 24,
      1, 0
    ) AS es_inicio_episodio
  FROM pred_test
  WHERE es_alerta_confirmada = TRUE
),
episodios_identificados AS (
  SELECT
    modelo,
    maquina_id,
    ts,
    SUM(es_inicio_episodio) OVER (PARTITION BY modelo, maquina_id ORDER BY ts) AS episodio_id
  FROM alertas_confirmadas
),
episodios AS (
  SELECT
    modelo,
    maquina_id,
    episodio_id,
    MIN(ts) AS ts_inicio
  FROM episodios_identificados
  GROUP BY modelo, maquina_id, episodio_id
),
fallas_test_ep AS (
  SELECT
    falla_id,
    maquina_id,
    ts_falla
  FROM `arquifusion.fallas`
  WHERE ts_falla >= (SELECT MIN(ts) FROM `arquifusion.v_features_z` WHERE particion = 'test')
),
episodios_evaluados AS (
  SELECT
    e.modelo,
    e.maquina_id,
    e.episodio_id,
    e.ts_inicio,
    LOGICAL_OR(
      f.falla_id IS NOT NULL
      AND f.ts_falla >= e.ts_inicio
      AND f.ts_falla <= TIMESTAMP_ADD(e.ts_inicio, INTERVAL 7 DAY)
    ) AS es_verdadero
  FROM episodios e
  LEFT JOIN fallas_test_ep f
    ON f.maquina_id = e.maquina_id
   AND f.ts_falla >= e.ts_inicio
   AND f.ts_falla <= TIMESTAMP_ADD(e.ts_inicio, INTERVAL 7 DAY)
  GROUP BY e.modelo, e.maquina_id, e.episodio_id, e.ts_inicio
),
fallas_con_anticipacion AS (
  SELECT
    ee.modelo,
    ft.falla_id,
    TIMESTAMP_DIFF(ft.ts_falla, MIN(ee.ts_inicio), HOUR) AS anticipacion_horas
  FROM fallas_test_ep ft
  JOIN episodios_evaluados ee
    ON ee.maquina_id = ft.maquina_id
   AND ee.es_verdadero = TRUE
   AND ft.ts_falla >= ee.ts_inicio
   AND ft.ts_falla <= TIMESTAMP_ADD(ee.ts_inicio, INTERVAL 7 DAY)
  GROUP BY ee.modelo, ft.falla_id, ft.ts_falla
),
stats_test_info AS (
  SELECT
    COUNT(DISTINCT maquina_id) AS n_maquinas,
    SAFE_DIVIDE(DATE_DIFF(MAX(DATE(ts)), MIN(DATE(ts)), DAY), 7.0) AS semanas_test
  FROM `arquifusion.v_features_z`
  WHERE particion = 'test'
),
resumen_modelos AS (
  SELECT
    u.modelo,
    COUNT(DISTINCT fca.falla_id) AS fallas_detectadas,
    (SELECT COUNT(*) FROM fallas_test_ep) AS fallas_totales,
    APPROX_QUANTILES(fca.anticipacion_horas, 100)[OFFSET(50)] AS anticipacion_mediana_horas,
    COUNTIF(ee.es_verdadero = FALSE) AS episodios_falsos,
    COUNTIF(ee.es_verdadero = TRUE) AS episodios_verdaderos,
    COUNT(ee.episodio_id) AS episodios_totales
  FROM umbrales_modelo u
  LEFT JOIN episodios_evaluados ee ON ee.modelo = u.modelo
  LEFT JOIN fallas_con_anticipacion fca ON fca.modelo = u.modelo
  GROUP BY u.modelo
)
SELECT
  rm.modelo,
  rm.fallas_detectadas,
  rm.fallas_totales,
  ROUND(SAFE_DIVIDE(rm.fallas_detectadas, rm.fallas_totales), 4) AS sensibilidad_fallas,
  rm.anticipacion_mediana_horas,
  ROUND(SAFE_DIVIDE(rm.episodios_falsos, sti.n_maquinas * sti.semanas_test), 4) AS episodios_falsos_por_maquina_semana,
  ROUND(SAFE_DIVIDE(rm.episodios_verdaderos, rm.episodios_totales), 4) AS precision_por_episodio
FROM resumen_modelos rm
CROSS JOIN stats_test_info sti
ORDER BY rm.modelo;

-- Validacion anti-fuga y de utilidad operativa sobre la particion test.
-- Para la sensibilidad se usan predicciones de gap y test en la ventana de 7 dias.
CREATE OR REPLACE TEMP TABLE umbrales_modelo AS
SELECT
  modelo,
  MAX(IF(particion = 'train', umbral, NULL)) AS umbral
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
      comb_d72_z
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
      comb_d72_z
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
  comb_d72_z
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
      comb_d72_z
    FROM `muestra_logit`
  )
);

-- 1) Sigmoide manual con ML.WEIGHTS contra la probabilidad de ML.PREDICT.
WITH pesos AS (
  SELECT
    MAX(IF(processed_input = '__INTERCEPT__', weight, NULL)) AS intercepto,
    MAX(IF(processed_input = 'temp_m24_z', weight, NULL)) AS w_temp_m24,
    MAX(IF(processed_input = 'vib_m24_z', weight, NULL)) AS w_vib_m24,
    MAX(IF(processed_input = 'comb_m24_z', weight, NULL)) AS w_comb_m24,
    MAX(IF(processed_input = 'temp_d72_z', weight, NULL)) AS w_temp_d72,
    MAX(IF(processed_input = 'vib_d72_z', weight, NULL)) AS w_vib_d72,
    MAX(IF(processed_input = 'comb_d72_z', weight, NULL)) AS w_comb_d72
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
  WHERE ts_falla >= TIMESTAMP '2026-10-17 00:00:00'
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

-- 3) Alertas falsas por maquina y semana ISO en test.
SELECT
  p.modelo,
  p.maquina_id,
  FORMAT_DATE('%G-W%V', DATE(p.ts)) AS semana,
  COUNTIF(p.prob >= u.umbral AND p.etiqueta = 0) AS alertas_falsas
FROM pred_eval_todos p
JOIN umbrales_modelo u
  ON u.modelo = p.modelo
WHERE p.particion = 'test'
GROUP BY
  p.modelo,
  p.maquina_id,
  FORMAT_DATE('%G-W%V', DATE(p.ts))
HAVING COUNTIF(p.prob >= u.umbral AND p.etiqueta = 0) > 0
ORDER BY p.modelo, p.maquina_id, semana;

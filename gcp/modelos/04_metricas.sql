-- Metricas de train, val y test para logit y boosted.
-- El umbral de cada modelo se elige en val maximizando F1 y se reutiliza en train, val y test.
CREATE TABLE IF NOT EXISTS `arquifusion.metricas_modelos` (
  modelo STRING,
  particion STRING,
  umbral FLOAT64,
  precision FLOAT64,
  sensibilidad FLOAT64,
  f1 FLOAT64,
  roc_auc FLOAT64,
  n_positivos INT64,
  n_total INT64,
  creado_ts TIMESTAMP
);

DELETE FROM `arquifusion.metricas_modelos`
WHERE modelo IN ('logit', 'boosted');

CREATE OR REPLACE TEMP TABLE pred_logit AS
SELECT
  particion,
  etiqueta,
  (SELECT p.prob FROM UNNEST(predicted_etiqueta_probs) AS p WHERE p.label = 1) AS prob
FROM ML.PREDICT(
  MODEL `arquifusion.modelo_falla_logit`,
  (
    SELECT
      temp_m24_z,
      vib_m24_z,
      comb_m24_z,
      temp_d72_z,
      vib_d72_z,
      comb_d72_z,
      temp_d14_z,
      vib_d14_z,
      comb_d14_z,
      etiqueta,
      particion
    FROM `arquifusion.v_features_z`
    WHERE particion IN ('train', 'val', 'test')
  )
);

CREATE OR REPLACE TEMP TABLE pred_boosted AS
SELECT
  particion,
  etiqueta,
  (SELECT p.prob FROM UNNEST(predicted_etiqueta_probs) AS p WHERE p.label = 1) AS prob
FROM ML.PREDICT(
  MODEL `arquifusion.modelo_falla_boosted`,
  (
    SELECT
      temp_m24_z,
      vib_m24_z,
      comb_m24_z,
      temp_d72_z,
      vib_d72_z,
      comb_d72_z,
      temp_d14_z,
      vib_d14_z,
      comb_d14_z,
      etiqueta,
      particion
    FROM `arquifusion.v_features_z`
    WHERE particion IN ('train', 'val', 'test')
  )
);

CREATE OR REPLACE TEMP TABLE metricas_val_todas AS
WITH grid AS (
  SELECT nivel / 20.0 AS umbral
  FROM UNNEST(GENERATE_ARRAY(1, 19)) AS nivel
),
logit AS (
  SELECT
    'logit' AS modelo,
    g.umbral,
    COUNTIF(p.prob >= g.umbral AND p.etiqueta = 1) AS tp,
    COUNTIF(p.prob >= g.umbral AND p.etiqueta = 0) AS fp,
    COUNTIF(p.prob < g.umbral AND p.etiqueta = 1) AS fn
  FROM pred_logit p
  CROSS JOIN grid g
  WHERE p.particion = 'val'
  GROUP BY g.umbral
),
boosted AS (
  SELECT
    'boosted' AS modelo,
    g.umbral,
    COUNTIF(p.prob >= g.umbral AND p.etiqueta = 1) AS tp,
    COUNTIF(p.prob >= g.umbral AND p.etiqueta = 0) AS fp,
    COUNTIF(p.prob < g.umbral AND p.etiqueta = 1) AS fn
  FROM pred_boosted p
  CROSS JOIN grid g
  WHERE p.particion = 'val'
  GROUP BY g.umbral
)
SELECT
  modelo,
  umbral,
  SAFE_DIVIDE(tp, tp + fp) AS precision,
  SAFE_DIVIDE(tp, tp + fn) AS sensibilidad,
  SAFE_DIVIDE(2 * tp, 2 * tp + fp + fn) AS f1
FROM logit
UNION ALL
SELECT
  modelo,
  umbral,
  SAFE_DIVIDE(tp, tp + fp) AS precision,
  SAFE_DIVIDE(tp, tp + fn) AS sensibilidad,
  SAFE_DIVIDE(2 * tp, 2 * tp + fp + fn) AS f1
FROM boosted;

CREATE OR REPLACE TEMP TABLE mejores_umbrales AS
SELECT
  modelo,
  umbral,
  f1
FROM (
  SELECT
    modelo,
    umbral,
    f1,
    ROW_NUMBER() OVER (
      PARTITION BY modelo
      ORDER BY IFNULL(f1, -1.0) DESC, umbral ASC
    ) AS fila
  FROM metricas_val_todas
)
WHERE fila = 1;

CREATE OR REPLACE TEMP TABLE predicciones AS
SELECT 'logit' AS modelo, particion, etiqueta, prob
FROM pred_logit
UNION ALL
SELECT 'boosted' AS modelo, particion, etiqueta, prob
FROM pred_boosted;

CREATE OR REPLACE TEMP TABLE metricas_calculadas AS
SELECT
  p.modelo,
  p.particion,
  u.umbral,
  SAFE_DIVIDE(
    COUNTIF(p.prob >= u.umbral AND p.etiqueta = 1),
    COUNTIF(p.prob >= u.umbral)
  ) AS precision,
  SAFE_DIVIDE(
    COUNTIF(p.prob >= u.umbral AND p.etiqueta = 1),
    COUNTIF(p.etiqueta = 1)
  ) AS sensibilidad,
  SAFE_DIVIDE(
    2 * COUNTIF(p.prob >= u.umbral AND p.etiqueta = 1),
    2 * COUNTIF(p.prob >= u.umbral AND p.etiqueta = 1)
      + COUNTIF(p.prob >= u.umbral AND p.etiqueta = 0)
      + COUNTIF(p.prob < u.umbral AND p.etiqueta = 1)
  ) AS f1,
  COUNTIF(p.etiqueta = 1) AS n_positivos,
  COUNT(*) AS n_total
FROM predicciones p
JOIN mejores_umbrales u USING (modelo)
GROUP BY p.modelo, p.particion, u.umbral;

CREATE OR REPLACE TEMP TABLE roc_auc_modelos AS
SELECT 'logit' AS modelo, 'train' AS particion, roc_auc
FROM ML.EVALUATE(
  MODEL `arquifusion.modelo_falla_logit`,
  (
    SELECT temp_m24_z, vib_m24_z, comb_m24_z, temp_d72_z, vib_d72_z, comb_d72_z, temp_d14_z, vib_d14_z, comb_d14_z, etiqueta
    FROM `arquifusion.v_features_z` WHERE particion = 'train'
  )
)
UNION ALL
SELECT 'logit', 'val', roc_auc
FROM ML.EVALUATE(
  MODEL `arquifusion.modelo_falla_logit`,
  (
    SELECT temp_m24_z, vib_m24_z, comb_m24_z, temp_d72_z, vib_d72_z, comb_d72_z, temp_d14_z, vib_d14_z, comb_d14_z, etiqueta
    FROM `arquifusion.v_features_z` WHERE particion = 'val'
  )
)
UNION ALL
SELECT 'logit', 'test', roc_auc
FROM ML.EVALUATE(
  MODEL `arquifusion.modelo_falla_logit`,
  (
    SELECT temp_m24_z, vib_m24_z, comb_m24_z, temp_d72_z, vib_d72_z, comb_d72_z, temp_d14_z, vib_d14_z, comb_d14_z, etiqueta
    FROM `arquifusion.v_features_z` WHERE particion = 'test'
  )
)
UNION ALL
SELECT 'boosted', 'train', roc_auc
FROM ML.EVALUATE(
  MODEL `arquifusion.modelo_falla_boosted`,
  (
    SELECT temp_m24_z, vib_m24_z, comb_m24_z, temp_d72_z, vib_d72_z, comb_d72_z, temp_d14_z, vib_d14_z, comb_d14_z, etiqueta
    FROM `arquifusion.v_features_z` WHERE particion = 'train'
  )
)
UNION ALL
SELECT 'boosted', 'val', roc_auc
FROM ML.EVALUATE(
  MODEL `arquifusion.modelo_falla_boosted`,
  (
    SELECT temp_m24_z, vib_m24_z, comb_m24_z, temp_d72_z, vib_d72_z, comb_d72_z, temp_d14_z, vib_d14_z, comb_d14_z, etiqueta
    FROM `arquifusion.v_features_z` WHERE particion = 'val'
  )
)
UNION ALL
SELECT 'boosted', 'test', roc_auc
FROM ML.EVALUATE(
  MODEL `arquifusion.modelo_falla_boosted`,
  (
    SELECT temp_m24_z, vib_m24_z, comb_m24_z, temp_d72_z, vib_d72_z, comb_d72_z, temp_d14_z, vib_d14_z, comb_d14_z, etiqueta
    FROM `arquifusion.v_features_z` WHERE particion = 'test'
  )
);

INSERT INTO `arquifusion.metricas_modelos`
SELECT
  m.modelo,
  m.particion,
  m.umbral,
  m.precision,
  m.sensibilidad,
  m.f1,
  r.roc_auc,
  m.n_positivos,
  m.n_total,
  CURRENT_TIMESTAMP()
FROM metricas_calculadas m
LEFT JOIN roc_auc_modelos r
  ON r.modelo = m.modelo
 AND r.particion = m.particion;

SELECT
  modelo,
  particion,
  umbral,
  ROUND(precision, 4) AS precision,
  ROUND(sensibilidad, 4) AS sensibilidad,
  ROUND(f1, 4) AS f1,
  ROUND(roc_auc, 4) AS roc_auc,
  n_positivos,
  n_total
FROM `arquifusion.metricas_modelos`
ORDER BY
  modelo,
  CASE particion
    WHEN 'train' THEN 1
    WHEN 'val' THEN 2
    WHEN 'test' THEN 3
  END;

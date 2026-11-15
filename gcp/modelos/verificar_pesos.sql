-- Compara la sigmoide con los pesos exportados (medias 24/96/336) contra ML.PREDICT.
-- max_diferencia debe ser ~1e-9. Si es > 0.0001, no enviar a A.
WITH base AS (
  SELECT maquina_id, ts, temp, vib, combustible_lph,
         ROW_NUMBER() OVER (PARTITION BY maquina_id ORDER BY ts) AS fila_num
  FROM `arquifusion.telemetria`
  WHERE maquina_id IS NOT NULL AND ts IS NOT NULL
    AND temp IS NOT NULL AND vib IS NOT NULL
    AND combustible_lph IS NOT NULL AND etiqueta_falla_7d IS NOT NULL
),
v AS (
  SELECT maquina_id, ts,
    AVG(temp) OVER w24 AS t24, AVG(temp) OVER w96 AS t96, AVG(temp) OVER w336 AS t336,
    AVG(vib) OVER w24 AS v24, AVG(vib) OVER w96 AS v96, AVG(vib) OVER w336 AS v336,
    AVG(combustible_lph) OVER w24 AS c24,
    AVG(combustible_lph) OVER w96 AS c96,
    AVG(combustible_lph) OVER w336 AS c336
  FROM base
  WINDOW
    w24 AS (PARTITION BY maquina_id ORDER BY ts ROWS BETWEEN 23 PRECEDING AND CURRENT ROW),
    w96 AS (PARTITION BY maquina_id ORDER BY ts ROWS BETWEEN 95 PRECEDING AND CURRENT ROW),
    w336 AS (PARTITION BY maquina_id ORDER BY ts ROWS BETWEEN 335 PRECEDING AND CURRENT ROW)
),
pw AS (
  SELECT intercepto,
    FLOAT64(pesos.temp_media_24h) AS wt24,
    FLOAT64(pesos.temp_media_96h) AS wt96,
    FLOAT64(pesos.temp_media_336h) AS wt336,
    FLOAT64(pesos.vib_media_24h) AS wv24,
    FLOAT64(pesos.vib_media_96h) AS wv96,
    FLOAT64(pesos.vib_media_336h) AS wv336,
    FLOAT64(pesos.combustible_lph_media_24h) AS wc24,
    FLOAT64(pesos.combustible_lph_media_96h) AS wc96,
    FLOAT64(pesos.combustible_lph_media_336h) AS wc336
  FROM `arquifusion.pesos_modelo_falla`
),
pred AS (
  SELECT maquina_id, ts,
    (SELECT p.prob FROM UNNEST(predicted_etiqueta_probs) p WHERE p.label = 1) AS prob_ml
  FROM ML.PREDICT(MODEL `arquifusion.modelo_falla_logit`, (
    SELECT maquina_id, ts, temp_m24_z, vib_m24_z, comb_m24_z, temp_d72_z, vib_d72_z,
           comb_d72_z, temp_d14_z, vib_d14_z, comb_d14_z
    FROM `arquifusion.v_features_z`
    WHERE particion = 'test'
    ORDER BY ts
    LIMIT 500))
)
SELECT
  COUNT(*) AS filas,
  MAX(ABS(prob_ml - 1 / (1 + EXP(-(
    pw.intercepto
    + wt24 * t24 + wt96 * t96 + wt336 * t336
    + wv24 * v24 + wv96 * v96 + wv336 * v336
    + wc24 * c24 + wc96 * c96 + wc336 * c336))))) AS max_diferencia
FROM pred
JOIN v USING (maquina_id, ts)
CROSS JOIN pw;

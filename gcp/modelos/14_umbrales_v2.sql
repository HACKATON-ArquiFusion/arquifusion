WITH pred AS (
  SELECT
    particion,
    etiqueta,
    (SELECT p.prob FROM UNNEST(predicted_etiqueta_probs) p WHERE p.label = 1) AS prob
  FROM ML.PREDICT(
    MODEL `arquifusion.modelo_falla_boosted_v2`,
    (SELECT * EXCEPT(maquina_id, ts) FROM `arquifusion.v_features_v2`
     WHERE particion IN ('val', 'test'))
  )
),
umbrales AS (
  SELECT umbral FROM UNNEST(GENERATE_ARRAY(0.05, 0.95, 0.05)) AS umbral
),
rejilla AS (
  SELECT
    p.particion,
    u.umbral,
    COUNTIF(p.prob >= u.umbral AND p.etiqueta = 1) AS tp,
    COUNTIF(p.prob >= u.umbral AND p.etiqueta = 0) AS fp,
    COUNTIF(p.prob < u.umbral AND p.etiqueta = 1) AS fn
  FROM pred p CROSS JOIN umbrales u
  GROUP BY p.particion, u.umbral
),
m AS (
  SELECT
    particion, umbral, tp, fp, fn,
    SAFE_DIVIDE(tp, tp + fp) AS precision,
    SAFE_DIVIDE(tp, tp + fn) AS sensibilidad
  FROM rejilla
)
SELECT
  particion,
  ROUND(umbral, 2) AS umbral,
  tp AS aciertos,
  fp AS falsas_alarmas,
  ROUND(precision, 3) AS precision,
  ROUND(sensibilidad, 3) AS sensibilidad,
  ROUND(SAFE_DIVIDE(2 * precision * sensibilidad, precision + sensibilidad), 3) AS f1
FROM m
WHERE tp + fp > 0
ORDER BY particion, umbral

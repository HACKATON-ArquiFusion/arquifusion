DELETE FROM `arquifusion.metricas_modelos` WHERE modelo = 'boosted_v2';

INSERT INTO `arquifusion.metricas_modelos`
  (modelo, particion, umbral, precision, sensibilidad, f1, roc_auc, n_positivos, n_total, creado_ts)
WITH pred AS (
  SELECT particion, etiqueta,
    (SELECT p.prob FROM UNNEST(predicted_etiqueta_probs) p WHERE p.label = 1) AS prob
  FROM ML.PREDICT(MODEL `arquifusion.modelo_falla_boosted_v2`,
    (SELECT * EXCEPT(maquina_id, ts) FROM `arquifusion.v_features_v2`
     WHERE particion IN ('train','val','test')))
),
auc AS (
  SELECT particion,
    SAFE_DIVIDE(
      SUM(IF(etiqueta = 1, r, 0)) - SAFE_DIVIDE(COUNTIF(etiqueta=1)*(COUNTIF(etiqueta=1)+1), 2),
      COUNTIF(etiqueta=1) * COUNTIF(etiqueta=0)) AS roc_auc
  FROM (SELECT particion, etiqueta, RANK() OVER (PARTITION BY particion ORDER BY prob) r FROM pred)
  GROUP BY particion
),
m AS (
  SELECT p.particion, 0.6 AS umbral,
    COUNTIF(p.prob >= 0.6 AND p.etiqueta = 1) tp,
    COUNTIF(p.prob >= 0.6 AND p.etiqueta = 0) fp,
    COUNTIF(p.prob <  0.6 AND p.etiqueta = 1) fn,
    COUNTIF(p.etiqueta = 1) pos, COUNT(*) tot
  FROM pred p GROUP BY p.particion
)
SELECT 'boosted_v2', m.particion, m.umbral,
  SAFE_DIVIDE(tp, tp+fp), SAFE_DIVIDE(tp, tp+fn),
  SAFE_DIVIDE(2*SAFE_DIVIDE(tp,tp+fp)*SAFE_DIVIDE(tp,tp+fn),
              SAFE_DIVIDE(tp,tp+fp)+SAFE_DIVIDE(tp,tp+fn)),
  a.roc_auc, m.pos, m.tot, CURRENT_TIMESTAMP()
FROM m JOIN auc a USING (particion);

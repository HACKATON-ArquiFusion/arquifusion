CREATE OR REPLACE TABLE `arquifusion.predicciones_falla_v2` AS
WITH ult AS (
  SELECT * EXCEPT(rn) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY maquina_id ORDER BY ts DESC) AS rn
    FROM `arquifusion.v_features_v2`
  ) WHERE rn = 1
)
SELECT
  maquina_id,
  ts,
  (SELECT p.prob FROM UNNEST(predicted_etiqueta_probs) p WHERE p.label = 1) AS prob_v2
FROM ML.PREDICT(MODEL `arquifusion.modelo_falla_boosted_v2`,
  (SELECT * EXCEPT(particion) FROM ult));

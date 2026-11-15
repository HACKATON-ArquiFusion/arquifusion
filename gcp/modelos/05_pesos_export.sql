-- Exporta una fila con el contrato pesos_modelo para el logit.
-- Los pesos corresponden a variables ya estandarizadas con medias y desviaciones de train.
CREATE OR REPLACE TABLE `arquifusion.pesos_modelo_falla` AS
WITH pesos AS (
  SELECT
    MAX(IF(processed_input = '__INTERCEPT__', weight, NULL)) AS intercepto,
    MAX(IF(processed_input = 'temp_m24_z', weight, NULL)) AS temp_m24,
    MAX(IF(processed_input = 'vib_m24_z', weight, NULL)) AS vib_m24,
    MAX(IF(processed_input = 'comb_m24_z', weight, NULL)) AS comb_m24,
    MAX(IF(processed_input = 'temp_d72_z', weight, NULL)) AS temp_d72,
    MAX(IF(processed_input = 'vib_d72_z', weight, NULL)) AS vib_d72,
    MAX(IF(processed_input = 'comb_d72_z', weight, NULL)) AS comb_d72
  FROM ML.WEIGHTS(MODEL `arquifusion.modelo_falla_logit`)
),
stats AS (
  SELECT
    MAX(IF(variable = 'temp_m24', media, NULL)) AS temp_m24_media,
    MAX(IF(variable = 'vib_m24', media, NULL)) AS vib_m24_media,
    MAX(IF(variable = 'comb_m24', media, NULL)) AS comb_m24_media,
    MAX(IF(variable = 'temp_d72', media, NULL)) AS temp_d72_media,
    MAX(IF(variable = 'vib_d72', media, NULL)) AS vib_d72_media,
    MAX(IF(variable = 'comb_d72', media, NULL)) AS comb_d72_media,
    MAX(IF(variable = 'temp_m24', desviacion, NULL)) AS temp_m24_desv,
    MAX(IF(variable = 'vib_m24', desviacion, NULL)) AS vib_m24_desv,
    MAX(IF(variable = 'comb_m24', desviacion, NULL)) AS comb_m24_desv,
    MAX(IF(variable = 'temp_d72', desviacion, NULL)) AS temp_d72_desv,
    MAX(IF(variable = 'vib_d72', desviacion, NULL)) AS vib_d72_desv,
    MAX(IF(variable = 'comb_d72', desviacion, NULL)) AS comb_d72_desv
  FROM `arquifusion.entrenamiento_stats`
)
SELECT
  'logit-v1' AS modelo,
  FORMAT_DATE('%Y-%m-%d', CURRENT_DATE()) AS version,
  7 AS horizonte_dias,
  ['temp_m24', 'vib_m24', 'comb_m24', 'temp_d72', 'vib_d72', 'comb_d72'] AS variables,
  p.intercepto,
  TO_JSON(STRUCT(
    p.temp_m24 AS temp_m24,
    p.vib_m24 AS vib_m24,
    p.comb_m24 AS comb_m24,
    p.temp_d72 AS temp_d72,
    p.vib_d72 AS vib_d72,
    p.comb_d72 AS comb_d72
  )) AS pesos,
  TO_JSON(STRUCT(
    s.temp_m24_media AS temp_m24,
    s.vib_m24_media AS vib_m24,
    s.comb_m24_media AS comb_m24,
    s.temp_d72_media AS temp_d72,
    s.vib_d72_media AS vib_d72,
    s.comb_d72_media AS comb_d72
  )) AS medias,
  TO_JSON(STRUCT(
    s.temp_m24_desv AS temp_m24,
    s.vib_m24_desv AS vib_m24,
    s.comb_m24_desv AS comb_m24,
    s.temp_d72_desv AS temp_d72,
    s.vib_d72_desv AS vib_d72,
    s.comb_d72_desv AS comb_d72
  )) AS desviaciones,
  (
    SELECT umbral
    FROM `arquifusion.metricas_modelos`
    WHERE modelo = 'logit'
      AND particion = 'train'
    LIMIT 1
  ) AS umbral_sugerido,
  (
    SELECT TO_JSON(STRUCT(precision, sensibilidad))
    FROM `arquifusion.metricas_modelos`
    WHERE modelo = 'logit'
      AND particion = 'test'
    LIMIT 1
  ) AS metricas
FROM pesos p
CROSS JOIN stats s;

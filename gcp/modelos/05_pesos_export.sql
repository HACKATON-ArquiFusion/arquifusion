-- Exporta una fila con el contrato pesos_modelo para el logit, en la gramática de A:
--   <base>_<estadistica>_<N>h  (p. ej. temp_media_24h, combustible_lph_media_336h)
--
-- El modelo se entrena con z-scores de m24, d72 y d14. Como las ventanas se anidan:
--   d72 = (4/3)  * (m24 - m96)
--   d14 = (14/13) * (m24 - m336)
-- (m96 = media de las ultimas 96 lecturas, m336 = media de las ultimas 336, incluida la actual.)
-- El logit es lineal, asi que se reexpresa EXACTAMENTE sobre m24, m96 y m336 con medias = 0 y
-- desviaciones = 1 (los pesos ya absorben la estandarizacion). Mismas probabilidades que ML.PREDICT.
--
-- logit = b + sum_v w_v * (x_v - mu_v) / s_v
--   a24  = w_m/s_m + (4/3) w_72/s_72 + (14/13) w_14/s_14
--   a96  = -(4/3) w_72/s_72
--   a336 = -(14/13) w_14/s_14
--   c    = -sum_v w_v * mu_v / s_v        intercepto_nuevo = b + sum_bases c
CREATE OR REPLACE TABLE `arquifusion.pesos_modelo_falla` AS
WITH w AS (
  SELECT
    REGEXP_EXTRACT(processed_input, r'^(temp|vib|comb)_') AS b,
    REGEXP_EXTRACT(processed_input, r'_(m24|d72|d14)_z$') AS k,
    weight
  FROM ML.WEIGHTS(MODEL `arquifusion.modelo_falla_logit`)
  WHERE processed_input != '__INTERCEPT__'
),
b0 AS (
  SELECT weight AS intercepto
  FROM ML.WEIGHTS(MODEL `arquifusion.modelo_falla_logit`)
  WHERE processed_input = '__INTERCEPT__'
),
s AS (
  SELECT
    REGEXP_EXTRACT(variable, r'^(temp|vib|comb)_') AS b,
    REGEXP_EXTRACT(variable, r'_(m24|d72|d14)$') AS k,
    media,
    desviacion
  FROM `arquifusion.entrenamiento_stats`
),
j AS (
  SELECT w.b, w.k, SAFE_DIVIDE(w.weight, s.desviacion) AS g, s.media
  FROM w JOIN s USING (b, k)
),
coef AS (
  SELECT
    b,
    SUM(CASE k WHEN 'm24' THEN g WHEN 'd72' THEN 4.0 / 3.0 * g WHEN 'd14' THEN 14.0 / 13.0 * g END) AS a24,
    -SUM(IF(k = 'd72', 4.0 / 3.0 * g, 0.0)) AS a96,
    -SUM(IF(k = 'd14', 14.0 / 13.0 * g, 0.0)) AS a336,
    -SUM(g * media) AS c
  FROM j
  GROUP BY b
),
p AS (
  SELECT
    MAX(IF(b = 'temp', a24, NULL)) AS temp_24,
    MAX(IF(b = 'temp', a96, NULL)) AS temp_96,
    MAX(IF(b = 'temp', a336, NULL)) AS temp_336,
    MAX(IF(b = 'vib', a24, NULL)) AS vib_24,
    MAX(IF(b = 'vib', a96, NULL)) AS vib_96,
    MAX(IF(b = 'vib', a336, NULL)) AS vib_336,
    MAX(IF(b = 'comb', a24, NULL)) AS comb_24,
    MAX(IF(b = 'comb', a96, NULL)) AS comb_96,
    MAX(IF(b = 'comb', a336, NULL)) AS comb_336,
    SUM(c) AS c_total
  FROM coef
)
SELECT
  'logit-v1' AS modelo,
  FORMAT_DATE('%Y-%m-%d', CURRENT_DATE()) AS version,
  7 AS horizonte_dias,
  [
    'temp_media_24h', 'temp_media_96h', 'temp_media_336h',
    'vib_media_24h', 'vib_media_96h', 'vib_media_336h',
    'combustible_lph_media_24h', 'combustible_lph_media_96h', 'combustible_lph_media_336h'
  ] AS variables,
  b0.intercepto + p.c_total AS intercepto,
  TO_JSON(STRUCT(
    p.temp_24 AS temp_media_24h,
    p.temp_96 AS temp_media_96h,
    p.temp_336 AS temp_media_336h,
    p.vib_24 AS vib_media_24h,
    p.vib_96 AS vib_media_96h,
    p.vib_336 AS vib_media_336h,
    p.comb_24 AS combustible_lph_media_24h,
    p.comb_96 AS combustible_lph_media_96h,
    p.comb_336 AS combustible_lph_media_336h
  )) AS pesos,
  TO_JSON(STRUCT(
    0.0 AS temp_media_24h, 0.0 AS temp_media_96h, 0.0 AS temp_media_336h,
    0.0 AS vib_media_24h, 0.0 AS vib_media_96h, 0.0 AS vib_media_336h,
    0.0 AS combustible_lph_media_24h, 0.0 AS combustible_lph_media_96h, 0.0 AS combustible_lph_media_336h
  )) AS medias,
  TO_JSON(STRUCT(
    1.0 AS temp_media_24h, 1.0 AS temp_media_96h, 1.0 AS temp_media_336h,
    1.0 AS vib_media_24h, 1.0 AS vib_media_96h, 1.0 AS vib_media_336h,
    1.0 AS combustible_lph_media_24h, 1.0 AS combustible_lph_media_96h, 1.0 AS combustible_lph_media_336h
  )) AS desviaciones,
  (
    SELECT umbral
    FROM `arquifusion.metricas_modelos`
    WHERE modelo = 'logit'
      AND particion = 'val'
    LIMIT 1
  ) AS umbral_sugerido,
  (
    SELECT TO_JSON(STRUCT(precision, sensibilidad))
    FROM `arquifusion.metricas_modelos`
    WHERE modelo = 'logit'
      AND particion = 'test'
    LIMIT 1
  ) AS metricas
FROM p
CROSS JOIN b0;

SELECT modelo, version, variables, intercepto, pesos, umbral_sugerido
FROM `arquifusion.pesos_modelo_falla`;

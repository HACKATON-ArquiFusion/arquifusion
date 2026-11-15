-- Vista base: variables crudas, etiqueta y particion temporal.
-- Las ventanas usan filas ordenadas por ts dentro de cada maquina.
CREATE OR REPLACE VIEW `arquifusion.v_base_falla` AS
WITH base AS (
  SELECT
    maquina_id,
    ts,
    temp,
    vib,
    combustible_lph,
    etiqueta_falla_7d,
    ROW_NUMBER() OVER (PARTITION BY maquina_id ORDER BY ts) AS fila_num
  FROM `arquifusion.telemetria`
  WHERE maquina_id IS NOT NULL
    AND ts IS NOT NULL
    AND temp IS NOT NULL
    AND vib IS NOT NULL
    AND combustible_lph IS NOT NULL
    AND etiqueta_falla_7d IS NOT NULL
),
ventanas AS (
  SELECT
    maquina_id,
    ts,
    etiqueta_falla_7d,
    fila_num,
    AVG(temp) OVER w24 AS temp_m24,
    AVG(vib) OVER w24 AS vib_m24,
    AVG(combustible_lph) OVER w24 AS comb_m24,
    AVG(temp) OVER w72 AS temp_prev72,
    AVG(vib) OVER w72 AS vib_prev72,
    AVG(combustible_lph) OVER w72 AS comb_prev72,
    AVG(temp) OVER w312 AS temp_prev312,
    AVG(vib) OVER w312 AS vib_prev312,
    AVG(combustible_lph) OVER w312 AS comb_prev312
  FROM base
  WINDOW
    w24 AS (
      PARTITION BY maquina_id
      ORDER BY ts
      ROWS BETWEEN 23 PRECEDING AND CURRENT ROW
    ),
    w72 AS (
      PARTITION BY maquina_id
      ORDER BY ts
      ROWS BETWEEN 95 PRECEDING AND 24 PRECEDING
    ),
    w312 AS (
      PARTITION BY maquina_id
      ORDER BY ts
      ROWS BETWEEN 335 PRECEDING AND 24 PRECEDING
    )
)
SELECT
  maquina_id,
  ts,
  temp_m24,
  vib_m24,
  comb_m24,
  temp_m24 - temp_prev72 AS temp_d72,
  vib_m24 - vib_prev72 AS vib_d72,
  comb_m24 - comb_prev72 AS comb_d72,
  temp_m24 - temp_prev312 AS temp_d14,
  vib_m24 - vib_prev312 AS vib_d14,
  comb_m24 - comb_prev312 AS comb_d14,
  CAST(etiqueta_falla_7d AS INT64) AS etiqueta,
  CASE
    WHEN ts < TIMESTAMP '2026-09-26 00:00:00' THEN 'train'
    WHEN ts < TIMESTAMP '2026-10-03 00:00:00' THEN 'gap'
    WHEN ts < TIMESTAMP '2026-10-21 00:00:00' THEN 'val'
    WHEN ts < TIMESTAMP '2026-10-28 00:00:00' THEN 'gap'
    ELSE 'test'
  END AS particion
FROM ventanas
WHERE fila_num > 336;

-- Media y desviacion poblacional calculadas solo con la particion train.
CREATE OR REPLACE TABLE `arquifusion.entrenamiento_stats` AS
SELECT 'temp_m24' AS variable, AVG(temp_m24) AS media, STDDEV_POP(temp_m24) AS desviacion
FROM `arquifusion.v_base_falla`
WHERE particion = 'train'
UNION ALL
SELECT 'vib_m24', AVG(vib_m24), STDDEV_POP(vib_m24)
FROM `arquifusion.v_base_falla`
WHERE particion = 'train'
UNION ALL
SELECT 'comb_m24', AVG(comb_m24), STDDEV_POP(comb_m24)
FROM `arquifusion.v_base_falla`
WHERE particion = 'train'
UNION ALL
SELECT 'temp_d72', AVG(temp_d72), STDDEV_POP(temp_d72)
FROM `arquifusion.v_base_falla`
WHERE particion = 'train'
UNION ALL
SELECT 'vib_d72', AVG(vib_d72), STDDEV_POP(vib_d72)
FROM `arquifusion.v_base_falla`
WHERE particion = 'train'
UNION ALL
SELECT 'comb_d72', AVG(comb_d72), STDDEV_POP(comb_d72)
FROM `arquifusion.v_base_falla`
WHERE particion = 'train'
UNION ALL
SELECT 'temp_d14', AVG(temp_d14), STDDEV_POP(temp_d14)
FROM `arquifusion.v_base_falla`
WHERE particion = 'train'
UNION ALL
SELECT 'vib_d14', AVG(vib_d14), STDDEV_POP(vib_d14)
FROM `arquifusion.v_base_falla`
WHERE particion = 'train'
UNION ALL
SELECT 'comb_d14', AVG(comb_d14), STDDEV_POP(comb_d14)
FROM `arquifusion.v_base_falla`
WHERE particion = 'train';

-- Variables estandarizadas con las estadisticas de train.
CREATE OR REPLACE VIEW `arquifusion.v_features_z` AS
SELECT
  f.maquina_id,
  f.ts,
  f.particion,
  f.etiqueta,
  SAFE_DIVIDE(f.temp_m24 - s.media_temp_m24, s.desv_temp_m24) AS temp_m24_z,
  SAFE_DIVIDE(f.vib_m24 - s.media_vib_m24, s.desv_vib_m24) AS vib_m24_z,
  SAFE_DIVIDE(f.comb_m24 - s.media_comb_m24, s.desv_comb_m24) AS comb_m24_z,
  SAFE_DIVIDE(f.temp_d72 - s.media_temp_d72, s.desv_temp_d72) AS temp_d72_z,
  SAFE_DIVIDE(f.vib_d72 - s.media_vib_d72, s.desv_vib_d72) AS vib_d72_z,
  SAFE_DIVIDE(f.comb_d72 - s.media_comb_d72, s.desv_comb_d72) AS comb_d72_z,
  SAFE_DIVIDE(f.temp_d14 - s.media_temp_d14, s.desv_temp_d14) AS temp_d14_z,
  SAFE_DIVIDE(f.vib_d14 - s.media_vib_d14, s.desv_vib_d14) AS vib_d14_z,
  SAFE_DIVIDE(f.comb_d14 - s.media_comb_d14, s.desv_comb_d14) AS comb_d14_z
FROM `arquifusion.v_base_falla` f
CROSS JOIN (
  SELECT
    MAX(IF(variable = 'temp_m24', media, NULL)) AS media_temp_m24,
    MAX(IF(variable = 'temp_m24', desviacion, NULL)) AS desv_temp_m24,
    MAX(IF(variable = 'vib_m24', media, NULL)) AS media_vib_m24,
    MAX(IF(variable = 'vib_m24', desviacion, NULL)) AS desv_vib_m24,
    MAX(IF(variable = 'comb_m24', media, NULL)) AS media_comb_m24,
    MAX(IF(variable = 'comb_m24', desviacion, NULL)) AS desv_comb_m24,
    MAX(IF(variable = 'temp_d72', media, NULL)) AS media_temp_d72,
    MAX(IF(variable = 'temp_d72', desviacion, NULL)) AS desv_temp_d72,
    MAX(IF(variable = 'vib_d72', media, NULL)) AS media_vib_d72,
    MAX(IF(variable = 'vib_d72', desviacion, NULL)) AS desv_vib_d72,
    MAX(IF(variable = 'comb_d72', media, NULL)) AS media_comb_d72,
    MAX(IF(variable = 'comb_d72', desviacion, NULL)) AS desv_comb_d72,
    MAX(IF(variable = 'temp_d14', media, NULL)) AS media_temp_d14,
    MAX(IF(variable = 'temp_d14', desviacion, NULL)) AS desv_temp_d14,
    MAX(IF(variable = 'vib_d14', media, NULL)) AS media_vib_d14,
    MAX(IF(variable = 'vib_d14', desviacion, NULL)) AS desv_vib_d14,
    MAX(IF(variable = 'comb_d14', media, NULL)) AS media_comb_d14,
    MAX(IF(variable = 'comb_d14', desviacion, NULL)) AS desv_comb_d14
  FROM `arquifusion.entrenamiento_stats`
) s;

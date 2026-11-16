CREATE OR REPLACE VIEW `arquifusion.v_features_v2` AS
WITH base AS (
  SELECT
    maquina_id, ts, modelo, zona,
    contexto_clima, contexto_terreno, contexto_lote_combustible,
    horas_op, temp, vib, presion, combustible_lph, carga_t, velocidad, CAST(rpm AS FLOAT64) AS rpm,
    CAST(etiqueta_falla_7d AS INT64) AS etiqueta,
    ROW_NUMBER() OVER (PARTITION BY maquina_id ORDER BY ts) AS fila_num
  FROM `arquifusion.telemetria`
  WHERE maquina_id IS NOT NULL AND ts IS NOT NULL
    AND temp IS NOT NULL AND vib IS NOT NULL AND combustible_lph IS NOT NULL
    AND etiqueta_falla_7d IS NOT NULL
),
v AS (
  SELECT
    maquina_id, ts, modelo, zona, contexto_clima, contexto_terreno,
    etiqueta, fila_num, horas_op,

    AVG(temp) OVER w24 AS temp_m24,
    AVG(vib) OVER w24 AS vib_m24,
    AVG(presion) OVER w24 AS pres_m24,
    AVG(combustible_lph) OVER w24 AS comb_m24,
    AVG(carga_t) OVER w24 AS carga_m24,
    AVG(velocidad) OVER w24 AS vel_m24,
    AVG(rpm) OVER w24 AS rpm_m24,

    STDDEV_POP(temp) OVER w24 AS temp_sd24,
    STDDEV_POP(vib) OVER w24 AS vib_sd24,
    STDDEV_POP(presion) OVER w24 AS pres_sd24,
    STDDEV_POP(combustible_lph) OVER w24 AS comb_sd24,

    MAX(temp) OVER w24 AS temp_max24,
    MAX(vib) OVER w24 AS vib_max24,

    AVG(temp) OVER w72 AS temp_p72,
    AVG(vib) OVER w72 AS vib_p72,
    AVG(presion) OVER w72 AS pres_p72,
    AVG(combustible_lph) OVER w72 AS comb_p72,
    AVG(carga_t) OVER w72 AS carga_p72,
    AVG(rpm) OVER w72 AS rpm_p72,

    AVG(temp) OVER w312 AS temp_p312,
    AVG(vib) OVER w312 AS vib_p312,
    AVG(presion) OVER w312 AS pres_p312,
    AVG(combustible_lph) OVER w312 AS comb_p312,
    AVG(carga_t) OVER w312 AS carga_p312,
    AVG(rpm) OVER w312 AS rpm_p312,
    STDDEV_POP(temp) OVER w312 AS temp_sd312,
    STDDEV_POP(vib) OVER w312 AS vib_sd312
  FROM base
  WINDOW
    w24  AS (PARTITION BY maquina_id ORDER BY ts ROWS BETWEEN 23 PRECEDING AND CURRENT ROW),
    w72  AS (PARTITION BY maquina_id ORDER BY ts ROWS BETWEEN 95 PRECEDING AND 24 PRECEDING),
    w312 AS (PARTITION BY maquina_id ORDER BY ts ROWS BETWEEN 335 PRECEDING AND 24 PRECEDING)
)
SELECT
  maquina_id, ts, etiqueta,
  modelo, zona, contexto_clima, contexto_terreno,
  horas_op,

  temp_m24, vib_m24, pres_m24, comb_m24, carga_m24, vel_m24, rpm_m24,
  temp_sd24, vib_sd24, pres_sd24, comb_sd24,
  temp_max24, vib_max24,

  temp_m24 - temp_p72 AS temp_d72,
  vib_m24 - vib_p72 AS vib_d72,
  pres_m24 - pres_p72 AS pres_d72,
  comb_m24 - comb_p72 AS comb_d72,
  carga_m24 - carga_p72 AS carga_d72,
  rpm_m24 - rpm_p72 AS rpm_d72,

  temp_m24 - temp_p312 AS temp_d14,
  vib_m24 - vib_p312 AS vib_d14,
  pres_m24 - pres_p312 AS pres_d14,
  comb_m24 - comb_p312 AS comb_d14,
  carga_m24 - carga_p312 AS carga_d14,
  rpm_m24 - rpm_p312 AS rpm_d14,

  SAFE_DIVIDE(temp_m24 - temp_p312, NULLIF(temp_sd312, 0)) AS temp_z_propio,
  SAFE_DIVIDE(vib_m24 - vib_p312, NULLIF(vib_sd312, 0)) AS vib_z_propio,
  SAFE_DIVIDE(temp_sd24, NULLIF(temp_sd312, 0)) AS temp_vol_rel,
  SAFE_DIVIDE(vib_sd24, NULLIF(vib_sd312, 0)) AS vib_vol_rel,

  SAFE_DIVIDE(comb_m24, NULLIF(carga_m24, 0)) AS comb_por_carga,
  SAFE_DIVIDE(temp_m24, NULLIF(rpm_m24, 0)) * 1000 AS temp_por_rpm,

  CASE
    WHEN ts < TIMESTAMP '2026-09-26 00:00:00' THEN 'train'
    WHEN ts < TIMESTAMP '2026-10-03 00:00:00' THEN 'gap'
    WHEN ts < TIMESTAMP '2026-10-21 00:00:00' THEN 'val'
    WHEN ts < TIMESTAMP '2026-10-28 00:00:00' THEN 'gap'
    ELSE 'test'
  END AS particion
FROM v
WHERE fila_num > 336;

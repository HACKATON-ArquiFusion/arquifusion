-- 13_pares.sql
-- RF-08: comportamiento inusual de cada maquina frente a su propio historial y frente a maquinas similares.
-- Ventana reciente: ultimos dias_rec dias. Historial propio: dias_hist dias anteriores a esa ventana.
-- Similares: mismo modelo, mismos dias_rec dias (mediana e IQR robusto).
-- Se excluye la presion: su promedio diario depende de la mezcla de horas activas, no de una deriva
-- (mismo criterio que RF-11). Piso relativo del 1 % de la media para que una desviacion minima no dispare el z.
-- score_desvio = mayor |z| entre las 3 variables; comparado_con indica contra que se obtuvo.

DECLARE dias_rec INT64 DEFAULT 7;
DECLARE dias_hist INT64 DEFAULT 60;
DECLARE umbral FLOAT64 DEFAULT 3.0;

CREATE OR REPLACE TABLE `arquifusion.pares_desvio` AS
WITH fin AS (
  SELECT MAX(DATE(ts)) AS dmax
  FROM `arquifusion.telemetria`
),
diario AS (
  SELECT
    maquina_id,
    MAX(modelo) AS modelo,
    DATE(ts) AS dia,
    AVG(temp) AS temp,
    AVG(vib) AS vib,
    AVG(combustible_lph) AS combustible_lph
  FROM `arquifusion.telemetria`
  WHERE maquina_id IS NOT NULL
    AND IFNULL(estado, '') <> 'detenida'
  GROUP BY maquina_id, dia
),
largo AS (
  SELECT d.maquina_id, d.modelo, d.dia, v.variable, v.valor
  FROM diario d,
  UNNEST([
    STRUCT('temp' AS variable, d.temp AS valor),
    STRUCT('vib', d.vib),
    STRUCT('combustible_lph', d.combustible_lph)
  ]) v
  WHERE v.valor IS NOT NULL
),
rec AS (
  SELECT l.maquina_id, ANY_VALUE(l.modelo) AS modelo, l.variable, AVG(l.valor) AS media_rec
  FROM largo l CROSS JOIN fin
  WHERE l.dia > DATE_SUB(fin.dmax, INTERVAL dias_rec DAY)
  GROUP BY l.maquina_id, l.variable
),
hist AS (
  SELECT l.maquina_id, l.variable, AVG(l.valor) AS media_h, STDDEV_SAMP(l.valor) AS desv_h
  FROM largo l CROSS JOIN fin
  WHERE l.dia <= DATE_SUB(fin.dmax, INTERVAL dias_rec DAY)
    AND l.dia > DATE_SUB(fin.dmax, INTERVAL dias_rec + dias_hist DAY)
  GROUP BY l.maquina_id, l.variable
),
base AS (
  SELECT
    r.maquina_id, r.modelo, r.variable, r.media_rec, h.media_h, h.desv_h,
    PERCENTILE_CONT(r.media_rec, 0.5) OVER (PARTITION BY r.modelo, r.variable) AS med_m,
    PERCENTILE_CONT(r.media_rec, 0.75) OVER (PARTITION BY r.modelo, r.variable)
      - PERCENTILE_CONT(r.media_rec, 0.25) OVER (PARTITION BY r.modelo, r.variable) AS iqr_m
  FROM rec r
  JOIN hist h ON h.maquina_id = r.maquina_id AND h.variable = r.variable
),
puntaje AS (
  SELECT
    *,
    SAFE_DIVIDE(media_rec - media_h, GREATEST(desv_h, 0.01 * ABS(media_h))) AS z_hist,
    IF(variable = 'temp', NULL, SAFE_DIVIDE(media_rec - med_m, GREATEST(iqr_m / 1.349, 0.01 * ABS(med_m)))) AS z_sim
  FROM base
),
calif AS (
  SELECT
    *,
    GREATEST(ABS(z_hist), IFNULL(ABS(z_sim), 0.0)) AS score
  FROM puntaje
  WHERE z_hist IS NOT NULL
),
mejor AS (
  SELECT
    maquina_id,
    modelo,
    ARRAY_AGG(
      STRUCT(variable, score, z_hist, z_sim,
             IF(ABS(z_hist) >= IFNULL(ABS(z_sim), 0.0), 'historial', 'similares') AS comparado)
      ORDER BY score DESC LIMIT 1
    )[OFFSET(0)] AS t
  FROM calif
  GROUP BY maquina_id, modelo
)
SELECT
  maquina_id,
  modelo,
  ROUND(t.score, 2) AS score_desvio,
  t.variable AS variable_principal,
  modelo AS comparado_con,
  t.comparado AS tipo_comparacion,
  ROUND(t.z_hist, 2) AS z_historial,
  ROUND(t.z_sim, 2) AS z_similares,
  t.score >= umbral AS alerta
FROM mejor;

SELECT
  alerta,
  COUNT(*) AS maquinas,
  ROUND(MIN(score_desvio), 1) AS score_min,
  ROUND(MAX(score_desvio), 1) AS score_max
FROM `arquifusion.pares_desvio`
GROUP BY alerta
ORDER BY alerta DESC;
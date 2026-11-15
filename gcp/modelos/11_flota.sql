-- 11_flota.sql
-- Productividad de la flota por maquina (RF-07): genera el tipo `flota` del contrato puente-b-a.
-- Ventana: ultimos 7 dias de telemetria. periodo = fecha de inicio de esa ventana (YYYY-MM-DD).
-- utilizacion_pct = % de lecturas horarias con estado distinto de detenida.
-- horas_op = lecturas horarias con estado distinto de detenida dentro de la ventana.
-- costo_op = litros consumidos x precio por litro (solo combustible; supuesto editable abajo).
-- Categorias, en este orden de prioridad:
--   sobreuso             : utilizacion > u_sobre
--   baja_prod_alto_costo : utilizacion < u_prod y costo por hora operativa > f_costo x mediana de su modelo
--   baja_utilizacion     : utilizacion < u_baja
--   normal               : el resto

DECLARE precio_litro FLOAT64 DEFAULT 4.5;
DECLARE u_baja FLOAT64 DEFAULT 35.0;
DECLARE u_sobre FLOAT64 DEFAULT 80.0;
DECLARE u_prod FLOAT64 DEFAULT 60.0;
DECLARE f_costo FLOAT64 DEFAULT 1.2;

CREATE OR REPLACE TABLE `arquifusion.flota_semanal` AS
WITH fin AS (
  SELECT MAX(DATE(ts)) AS dmax
  FROM `arquifusion.telemetria`
),
ventana AS (
  SELECT
    t.maquina_id,
    t.modelo,
    t.estado,
    t.combustible_lph,
    fin.dmax
  FROM `arquifusion.telemetria` t
  CROSS JOIN fin
  WHERE t.maquina_id IS NOT NULL
    AND DATE(t.ts) > DATE_SUB(fin.dmax, INTERVAL 7 DAY)
),
agg AS (
  SELECT
    maquina_id,
    MAX(modelo) AS modelo,
    MAX(dmax) AS dmax,
    COUNT(*) AS lecturas,
    COUNTIF(IFNULL(estado, '') <> 'detenida') AS horas_op,
    SUM(IFNULL(combustible_lph, 0.0)) AS litros
  FROM ventana
  GROUP BY maquina_id
),
metricas AS (
  SELECT
    maquina_id,
    modelo,
    dmax,
    horas_op,
    SAFE_DIVIDE(horas_op, lecturas) * 100.0 AS utilizacion_pct,
    litros * precio_litro AS costo_op,
    SAFE_DIVIDE(litros * precio_litro, horas_op) AS costo_hora
  FROM agg
),
con_mediana AS (
  SELECT
    *,
    PERCENTILE_CONT(costo_hora, 0.5) OVER (PARTITION BY modelo) AS costo_hora_mediana
  FROM metricas
)
SELECT
  maquina_id,
  FORMAT_DATE('%Y-%m-%d', DATE_SUB(dmax, INTERVAL 6 DAY)) AS periodo,
  ROUND(utilizacion_pct, 2) AS utilizacion_pct,
  CAST(horas_op AS FLOAT64) AS horas_op,
  ROUND(costo_op, 2) AS costo_op,
  CASE
    WHEN utilizacion_pct > u_sobre THEN 'sobreuso'
    WHEN utilizacion_pct < u_prod AND costo_hora > f_costo * costo_hora_mediana THEN 'baja_prod_alto_costo'
    WHEN utilizacion_pct < u_baja THEN 'baja_utilizacion'
    ELSE 'normal'
  END AS categoria
FROM con_mediana;

-- Control: maquinas por categoria
SELECT
  categoria,
  COUNT(*) AS maquinas,
  ROUND(AVG(utilizacion_pct), 1) AS util_media,
  ROUND(AVG(costo_op), 0) AS costo_medio
FROM `arquifusion.flota_semanal`
GROUP BY categoria
ORDER BY maquinas DESC;

-- Control: distribucion de utilizacion y costo (sirve para calibrar los umbrales)
SELECT
  COUNT(*) AS filas,
  ROUND(MIN(utilizacion_pct), 1) AS util_min,
  ROUND(APPROX_QUANTILES(utilizacion_pct, 4)[OFFSET(2)], 1) AS util_mediana,
  ROUND(MAX(utilizacion_pct), 1) AS util_max,
  ROUND(MIN(costo_op), 0) AS costo_min,
  ROUND(MAX(costo_op), 0) AS costo_max
FROM `arquifusion.flota_semanal`;


-- 14_plan_semanal.sql
-- RF-12: plan semanal de intervenciones por maquina (tipo `plan_semanal` del contrato puente-b-a).
-- Combina riesgo de falla (logit), preventivo por horas, pares (RF-08) y causa comun (RF-11),
-- y usa el historial de fallas para decir con cuantos dias de aviso suelen aparecer los sintomas.
-- Semana = los 7 dias siguientes al ultimo dato de telemetria.
-- Prioridad: 1 inspeccion_inmediata, 2 preventivo, 3 investigar. Maximo una accion por maquina.
-- Requiere haber corrido los pasos 4 (metricas), 10 (pronosticos), 12 (causa comun) y 13 (pares).

DECLARE umbral_riesgo FLOAT64 DEFAULT (
  SELECT umbral
  FROM `arquifusion.metricas_modelos`
  WHERE modelo = 'logit' AND particion = 'val'
  LIMIT 1
);
DECLARE dias_preventivo INT64 DEFAULT 7;
DECLARE p95_riesgo FLOAT64 DEFAULT (SELECT APPROX_QUANTILES(prob_logit, 100)[OFFSET(95)] FROM `arquifusion.predicciones_falla_actual`);

CREATE OR REPLACE TABLE `arquifusion.plan_semanal` AS
WITH fin AS (
  SELECT MAX(DATE(ts)) AS dmax
  FROM `arquifusion.telemetria`
),
maq AS (
  SELECT maquina_id, ANY_VALUE(modelo) AS modelo
  FROM `arquifusion.telemetria`
  WHERE maquina_id IS NOT NULL
  GROUP BY maquina_id
),
-- Patron historico 1: dias de aviso tipicos (inicio de deriva -> falla) por componente.
aviso AS (
  SELECT
    componente,
    COUNT(*) AS n_fallas,
    ROUND(APPROX_QUANTILES(TIMESTAMP_DIFF(ts_falla, ts_inicio_deriva, HOUR) / 24.0, 100)[OFFSET(50)], 1) AS dias_aviso
  FROM `arquifusion.fallas`
  WHERE componente IS NOT NULL
    AND ts_inicio_deriva IS NOT NULL
    AND ts_falla IS NOT NULL
  GROUP BY componente
),
-- Patron historico 2: componente que mas falla en cada modelo de maquina.
comp_modelo AS (
  SELECT modelo, componente
  FROM (
    SELECT m.modelo, f.componente, COUNT(*) AS n
    FROM `arquifusion.fallas` f
    JOIN maq m ON m.maquina_id = f.maquina_id
    WHERE f.componente IS NOT NULL
    GROUP BY m.modelo, f.componente
  )
  WHERE TRUE
  QUALIFY ROW_NUMBER() OVER (PARTITION BY modelo ORDER BY n DESC, componente) = 1
),
riesgo AS (
  SELECT maquina_id, prob_logit
  FROM `arquifusion.predicciones_falla_actual`
),
prev AS (
  SELECT maquina_id, fecha_recomendada AS fecha_prev, horas_proyectadas
  FROM `arquifusion.preventivo_propuesto`
  WHERE fecha_recomendada IS NOT NULL
),
par AS (
  SELECT maquina_id, score_desvio, variable_principal
  FROM `arquifusion.pares_desvio`
  WHERE alerta
),
cc AS (
  SELECT DISTINCT m AS maquina_id
  FROM `arquifusion.causa_comun_grupos`, UNNEST(maquina_ids) AS m
  WHERE confianza = 'alta'
),
se AS (
  SELECT
    q.maquina_id,
    f.dmax,
    r.prob_logit,
    p.fecha_prev,
    p.horas_proyectadas,
    a.score_desvio,
    a.variable_principal,
    c.maquina_id IS NOT NULL AS en_causa_comun,
    cm.componente AS componente_probable,
    av.dias_aviso
  FROM maq q
  CROSS JOIN fin f
  LEFT JOIN riesgo r ON r.maquina_id = q.maquina_id
  LEFT JOIN prev p ON p.maquina_id = q.maquina_id
  LEFT JOIN par a ON a.maquina_id = q.maquina_id
  LEFT JOIN cc c ON c.maquina_id = q.maquina_id
  LEFT JOIN comp_modelo cm ON cm.modelo = q.modelo
  LEFT JOIN aviso av ON av.componente = cm.componente
),
dec AS (
  SELECT
    *,
    CASE
      WHEN prob_logit >= umbral_riesgo THEN 'inspeccion_inmediata'
      WHEN fecha_prev <= DATE_ADD(dmax, INTERVAL dias_preventivo DAY) THEN 'preventivo'
      WHEN score_desvio IS NOT NULL OR en_causa_comun OR prob_logit >= p95_riesgo THEN 'investigar'
    END AS accion
  FROM se
)
SELECT
  maquina_id,
  DATE_ADD(dmax, INTERVAL 1 DAY) AS semana_inicio,
  accion,
  CASE accion WHEN 'inspeccion_inmediata' THEN 1 WHEN 'preventivo' THEN 2 ELSE 3 END AS prioridad,
  CASE accion
    WHEN 'inspeccion_inmediata' THEN DATE_ADD(dmax, INTERVAL 1 DAY)
    WHEN 'preventivo' THEN GREATEST(fecha_prev, DATE_ADD(dmax, INTERVAL 1 DAY))
    ELSE DATE_ADD(dmax, INTERVAL 3 DAY)
  END AS fecha_recomendada,
  IF(accion = 'inspeccion_inmediata', componente_probable, NULL) AS componente_probable,
  ARRAY_TO_STRING(ARRAY(
    SELECT x
    FROM UNNEST([
      IF(prob_logit >= umbral_riesgo,
         FORMAT('Riesgo de falla a 7 dias %.0f%% (umbral %.0f%%)', prob_logit * 100, umbral_riesgo * 100), NULL),
      IF(fecha_prev <= DATE_ADD(dmax, INTERVAL dias_preventivo DAY),
         FORMAT('Preventivo cada 500 h: al llegar a %.0f h de operacion (fecha estimada %s)', horas_proyectadas, CAST(fecha_prev AS STRING)), NULL),
      IF(score_desvio IS NOT NULL,
         FORMAT('Desvio inusual en %s (score %.1f)', variable_principal, score_desvio), NULL),
      IF(en_causa_comun, 'Forma parte de un grupo con causa comun', NULL),
      IF(prob_logit >= p95_riesgo AND prob_logit < umbral_riesgo,
         FORMAT('Riesgo relativo alto: %.0f%%, dentro del 5%% mas alto de la flota (sin llegar al umbral de %.0f%%)', prob_logit * 100, umbral_riesgo * 100), NULL),
      IF(prob_logit >= umbral_riesgo AND dias_aviso IS NOT NULL,
         FORMAT('En el historial, las fallas de %s avisan ~%.0f dias antes', componente_probable, dias_aviso), NULL)
    ]) AS x
    WHERE x IS NOT NULL
  ), '. ') AS motivo
FROM dec
WHERE accion IS NOT NULL;

-- Control: maquinas por accion (si inspeccion_inmediata supera ~20 % de la flota, revisar el umbral)
SELECT
  accion,
  prioridad,
  COUNT(*) AS maquinas,
  MIN(fecha_recomendada) AS desde,
  MAX(fecha_recomendada) AS hasta
FROM `arquifusion.plan_semanal`
GROUP BY accion, prioridad
ORDER BY prioridad;

-- Control: ejemplos
SELECT maquina_id, accion, fecha_recomendada, componente_probable, motivo
FROM `arquifusion.plan_semanal`
ORDER BY prioridad, fecha_recomendada, maquina_id
LIMIT 8;

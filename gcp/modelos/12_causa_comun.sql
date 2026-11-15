-- 12_causa_comun.sql
-- RF-11: causa comun en varias maquinas. Genera la tabla causa_comun_grupos (tipo `causa_comun` del contrato puente-b-a).
-- La deteccion usa SOLO telemetria (no usa grupo_id ni causa_verdadera); las etiquetas del simulador
-- se usan unicamente en el control final para validar.
--
-- Pasos:
--   1) Promedio diario por maquina de temp, vib, presion y combustible_lph (sin lecturas detenidas).
--   2) z-score robusto de cada dia contra la mediana y el IQR de la propia maquina.
--   3) Episodio anomalo = dias consecutivos con z >= z_min (minimo min_dias dias).
--   4) Por zona, los episodios se agrupan en el tiempo (sesion: separacion <= gap_dias entre dias finales)
--      y en el espacio (ST_CLUSTERDBSCAN con radio radio_dbscan_m y minimo min_maquinas).
--   5) Hipotesis: el valor de contexto (clima, terreno o lote) mas sobrerrepresentado en el grupo frente
--      al resto de la zona en la misma ventana; si no es claro, 'otra'.

DECLARE z_min FLOAT64 DEFAULT 3.0;
DECLARE min_dias INT64 DEFAULT 2;
DECLARE gap_dias INT64 DEFAULT 2;
DECLARE sync_dias INT64 DEFAULT 1;
DECLARE radio_dbscan_m FLOAT64 DEFAULT 800.0;
DECLARE min_maquinas INT64 DEFAULT 3;
DECLARE ctx_pct_min FLOAT64 DEFAULT 0.5;
DECLARE ctx_dif_min FLOAT64 DEFAULT 0.30;
DECLARE conf_pct FLOAT64 DEFAULT 0.80;
DECLARE conf_dif FLOAT64 DEFAULT 0.55;

CREATE OR REPLACE TABLE `arquifusion.causa_comun_grupos` AS
WITH diario AS (
  SELECT
    maquina_id,
    DATE(ts) AS dia,
    MAX(zona) AS zona,
    AVG(lat) AS lat,
    AVG(lon) AS lon,
    AVG(temp) AS temp,
    AVG(vib) AS vib,
    AVG(presion) AS presion,
    AVG(combustible_lph) AS combustible_lph
  FROM `arquifusion.telemetria`
  WHERE maquina_id IS NOT NULL
    AND lat IS NOT NULL AND lon IS NOT NULL
    AND IFNULL(estado, '') <> 'detenida'
  GROUP BY maquina_id, dia
),
largo AS (
  SELECT d.maquina_id, d.dia, d.zona, d.lat, d.lon, v.variable, v.valor
  FROM diario d,
  UNNEST([
    STRUCT('temp' AS variable, d.temp AS valor),
    STRUCT('vib', d.vib),
    STRUCT('presion', d.presion),
    STRUCT('combustible_lph', d.combustible_lph)
  ]) v
  WHERE v.valor IS NOT NULL
),
zs AS (
  SELECT
    maquina_id, dia, zona, lat, lon, variable,
    SAFE_DIVIDE(ABS(valor - med), GREATEST(iqr / 1.349, 0.001)) AS z
  FROM (
    SELECT
      l.*,
      PERCENTILE_CONT(valor, 0.5) OVER (PARTITION BY maquina_id, variable) AS med,
      PERCENTILE_CONT(valor, 0.75) OVER (PARTITION BY maquina_id, variable)
        - PERCENTILE_CONT(valor, 0.25) OVER (PARTITION BY maquina_id, variable) AS iqr
    FROM largo l
  )
),
anom AS (
  SELECT
    maquina_id, dia,
    ANY_VALUE(zona) AS zona,
    ANY_VALUE(lat) AS lat,
    ANY_VALUE(lon) AS lon,
    MAX(z) AS z_max,
    ARRAY_AGG(variable ORDER BY z DESC LIMIT 1)[OFFSET(0)] AS variable
  FROM zs
  GROUP BY maquina_id, dia
  HAVING MAX(z) >= z_min
),
marcados AS (
  SELECT *, LAG(dia) OVER (PARTITION BY maquina_id ORDER BY dia) AS dia_prev
  FROM anom
),
islas AS (
  SELECT
    *,
    SUM(IF(dia_prev IS NULL OR DATE_DIFF(dia, dia_prev, DAY) > 1, 1, 0))
      OVER (PARTITION BY maquina_id ORDER BY dia) AS ep
  FROM marcados
),
episodios AS (
  SELECT
    maquina_id, ep,
    MAX(zona) AS zona,
    MIN(dia) AS dia_ini,
    MAX(dia) AS dia_fin,
    COUNT(*) AS dias,
    AVG(lat) AS lat,
    AVG(lon) AS lon,
    MAX(z_max) AS z_pico,
    ARRAY_AGG(variable ORDER BY z_max DESC LIMIT 1)[OFFSET(0)] AS variable
  FROM islas
  GROUP BY maquina_id, ep
  HAVING COUNT(*) >= min_dias
),
t1 AS (
  SELECT *, LAG(dia_fin) OVER (PARTITION BY zona ORDER BY dia_fin, maquina_id) AS fin_prev
  FROM episodios
),
t2 AS (
  SELECT
    *,
    SUM(IF(fin_prev IS NULL OR DATE_DIFF(dia_fin, fin_prev, DAY) > gap_dias, 1, 0))
      OVER (PARTITION BY zona ORDER BY dia_fin, maquina_id) AS sesion
  FROM t1
),
t3 AS (
  SELECT
    *,
    ST_CLUSTERDBSCAN(ST_GEOGPOINT(lon, lat), radio_dbscan_m, min_maquinas)
      OVER (PARTITION BY zona, sesion) AS cl
  FROM t2
),
candidatos AS (
  SELECT
    CONCAT(zona, '|', CAST(sesion AS STRING), '|', CAST(cl AS STRING)) AS grupo_key,
    zona,
    MIN(dia_ini) AS dia_ini,
    MAX(dia_fin) AS dia_fin,
    ARRAY_AGG(DISTINCT maquina_id ORDER BY maquina_id) AS maquina_ids,
    COUNT(DISTINCT maquina_id) AS n_maquinas,
    AVG(lat) AS centro_lat,
    AVG(lon) AS centro_lon,
    ROUND(GREATEST(
      ST_MAXDISTANCE(ST_UNION_AGG(ST_GEOGPOINT(lon, lat)), ST_GEOGPOINT(AVG(lon), AVG(lat))),
      50.0)) AS radio_m,
    MAX(z_pico) AS z_pico,
    DATE_DIFF(MAX(dia_fin), MIN(dia_fin), DAY) AS disp_fin_dias,
    DATE_DIFF(MAX(dia_ini), MIN(dia_ini), DAY) AS disp_ini_dias,
    APPROX_TOP_COUNT(variable, 1)[OFFSET(0)].value AS variable_comun
  FROM (SELECT *, PERCENTILE_DISC(UNIX_DATE(dia_fin), 0.5) OVER (PARTITION BY zona, sesion, cl) AS fin_med FROM t3 WHERE cl IS NOT NULL) t4
  WHERE ABS(UNIX_DATE(dia_fin) - fin_med) <= sync_dias
  GROUP BY zona, sesion, cl
  HAVING COUNT(DISTINCT maquina_id) >= min_maquinas
),
lect AS (
  SELECT
    c.grupo_key,
    IF(t.maquina_id IN UNNEST(c.maquina_ids), 'grupo', 'resto') AS origen,
    x.variable_ctx,
    x.valor_ctx
  FROM candidatos c
  JOIN `arquifusion.telemetria` t
    ON t.zona = c.zona
   AND t.ts >= TIMESTAMP(c.dia_ini)
   AND t.ts < TIMESTAMP_ADD(TIMESTAMP(c.dia_fin), INTERVAL 1 DAY)
  CROSS JOIN UNNEST([
    STRUCT('clima' AS variable_ctx, t.contexto_clima AS valor_ctx),
    STRUCT('terreno', t.contexto_terreno),
    STRUCT('lote_combustible', t.contexto_lote_combustible)
  ]) x
),
cuenta AS (
  SELECT
    grupo_key, origen, variable_ctx, valor_ctx,
    COUNT(*) AS n,
    SUM(COUNT(*)) OVER (PARTITION BY grupo_key, origen, variable_ctx) AS tot
  FROM lect
  WHERE valor_ctx IS NOT NULL
  GROUP BY grupo_key, origen, variable_ctx, valor_ctx
),
piv AS (
  SELECT
    grupo_key, variable_ctx, valor_ctx,
    MAX(IF(origen = 'grupo', SAFE_DIVIDE(n, tot), NULL)) AS pct_g,
    IFNULL(MAX(IF(origen = 'resto', SAFE_DIVIDE(n, tot), NULL)), 0.0) AS pct_r
  FROM cuenta
  GROUP BY grupo_key, variable_ctx, valor_ctx
),
mejor AS (
  SELECT *
  FROM piv
  WHERE pct_g IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY grupo_key ORDER BY pct_g - pct_r DESC) = 1
)
SELECT
  CONCAT('CC-', c.zona, '-', FORMAT_DATE('%Y%m%d', c.dia_ini), '-',
         SUBSTR(TO_HEX(MD5(ARRAY_TO_STRING(c.maquina_ids, ','))), 1, 6)) AS grupo_id,
  c.zona,
  TIMESTAMP(c.dia_ini) AS ts_inicio,
  TIMESTAMP_ADD(TIMESTAMP(c.dia_fin), INTERVAL 1 DAY) AS ts_fin,
  c.maquina_ids,
  c.variable_comun,
  IF(m.pct_g >= ctx_pct_min AND m.pct_g - m.pct_r >= ctx_dif_min, m.variable_ctx, 'otra') AS hipotesis,
  IF(m.pct_g >= ctx_pct_min AND m.pct_g - m.pct_r >= ctx_dif_min,
     CONCAT(m.variable_ctx, '=', m.valor_ctx), NULL) AS hipotesis_detalle,
  ROUND(c.centro_lat, 6) AS centro_lat,
  ROUND(c.centro_lon, 6) AS centro_lon,
  c.radio_m,
  c.n_maquinas,
  c.disp_fin_dias,
  c.disp_ini_dias,
  ROUND(c.z_pico, 2) AS z_pico,
  ROUND(m.pct_g, 3) AS ctx_pct_grupo,
  ROUND(m.pct_r, 3) AS ctx_pct_resto,
  IF(m.pct_g >= conf_pct AND m.pct_g - m.pct_r >= conf_dif, 'alta', 'media') AS confianza
FROM candidatos c
LEFT JOIN mejor m USING (grupo_key);

-- Control: grupos reales del simulador frente a los detectados (coinciden = maquinas en comun).
WITH reales AS (
  SELECT grupo_id AS g_real, ANY_VALUE(zona) AS zona, ANY_VALUE(causa_verdadera) AS causa_real,
         ARRAY_AGG(DISTINCT maquina_id) AS maqs
  FROM `arquifusion.fallas`
  WHERE grupo_id IS NOT NULL
  GROUP BY grupo_id
),
rm AS (
  SELECT r.g_real, m AS maquina_id FROM reales r, UNNEST(r.maqs) m
),
dm AS (
  SELECT d.grupo_id AS g_det, m AS maquina_id
  FROM `arquifusion.causa_comun_grupos` d, UNNEST(d.maquina_ids) m
),
cruce AS (
  SELECT rm.g_real, dm.g_det, COUNT(*) AS coinciden
  FROM rm JOIN dm USING (maquina_id)
  GROUP BY rm.g_real, dm.g_det
)
SELECT
  r.g_real, r.zona, r.causa_real,
  ARRAY_LENGTH(r.maqs) AS maq_reales,
  c.g_det, d.n_maquinas AS maq_detectadas, c.coinciden,
  d.hipotesis_detalle, d.variable_comun, d.radio_m
FROM reales r
LEFT JOIN cruce c ON c.g_real = r.g_real
LEFT JOIN `arquifusion.causa_comun_grupos` d ON d.grupo_id = c.g_det
ORDER BY r.g_real;

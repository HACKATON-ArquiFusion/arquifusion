-- 1. Filas por tabla.
SELECT 'telemetria' AS tabla, COUNT(*) AS filas FROM arquifusion.telemetria
UNION ALL
SELECT 'fallas', COUNT(*) FROM arquifusion.fallas
UNION ALL
SELECT 'incidentes', COUNT(*) FROM arquifusion.incidentes
UNION ALL
SELECT 'mantenimientos', COUNT(*) FROM arquifusion.mantenimientos
UNION ALL
SELECT 'consumo_repuestos', COUNT(*) FROM arquifusion.consumo_repuestos
UNION ALL
SELECT 'scores', COUNT(*) FROM arquifusion.scores
ORDER BY tabla;

-- 2. Rango de fechas de telemetria.
SELECT
  MIN(ts) AS ts_min,
  MAX(ts) AS ts_max
FROM arquifusion.telemetria;

-- 3. Cantidad de maquinas y zonas distintas.
SELECT
  COUNT(DISTINCT maquina_id) AS maquinas,
  COUNT(DISTINCT zona) AS zonas
FROM arquifusion.telemetria;

-- 4. Porcentaje de etiqueta_falla_7d = 1.
SELECT
  COUNTIF(etiqueta_falla_7d = 1) AS positivos,
  COUNT(*) AS total,
  ROUND(100 * SAFE_DIVIDE(COUNTIF(etiqueta_falla_7d = 1), COUNT(*)), 2) AS porcentaje
FROM arquifusion.telemetria;

-- 5. Fallas por escenario.
SELECT
  escenario,
  COUNT(*) AS fallas
FROM arquifusion.fallas
GROUP BY escenario
ORDER BY fallas DESC;

-- 6. Particiones existentes por tabla.
SELECT
  table_name,
  COUNT(DISTINCT partition_id) AS particiones,
  MIN(partition_id) AS particion_min,
  MAX(partition_id) AS particion_max
FROM arquifusion.INFORMATION_SCHEMA.PARTITIONS
WHERE table_name IN (
  'telemetria',
  'fallas',
  'incidentes',
  'mantenimientos',
  'consumo_repuestos',
  'scores'
)
GROUP BY table_name
ORDER BY table_name;

-- 7. Claves duplicadas por maquina_id + ts (debe ser 0).
SELECT COUNT(*) AS duplicados_maquina_ts
FROM (
  SELECT
    maquina_id,
    ts
  FROM arquifusion.telemetria
  GROUP BY maquina_id, ts
  HAVING COUNT(*) > 1
);

# Ejemplos de datos para el dashboard (B → C)

Datos de ejemplo reales, sacados de BigQuery. Sirven para montar las pantallas mientras A termina su API.
Cuando A exponga los endpoints, se cambian los JSON por llamadas reales con la misma forma.
Las fechas son ISO 8601 UTC. Los `scores_*.json` traen solo 10 registros de muestra (el total está en la tabla).

## Pantalla -> archivo

| Pantalla | RF | Archivo | Total real | Campos clave |
|---|---|---|---|---|
| Mapa de estado de la flota | RF-04 | `maquinas.json` + `scores_prob_falla.json` | 150 | `maquina_id`, `modelo`, `zona`, `lat`, `lon`, `estado` + `prob_falla` |
| Preventivo | RF-02 | `scores_preventivo.json` | 150 | `maquina_id`, `fecha_recomendada`, `horas_proyectadas`, `motivo` |
| Uso futuro (gráfico) | RF-02 | `pronostico_horas_uso.json` | 2100 | `maquina_id`, `fecha`, `horas_prevista`, `limite_inf`, `limite_sup` |
| Repuestos por zona | RF-03 | `scores_pron_repuestos.json` | 504 | `componente`, `zona`, `fecha`, `demanda_prevista`, `limite_inf`, `limite_sup` |
| Combustible y anomalías | RF-06 | `scores_combustible.json` | 1050 | `maquina_id`, `fecha`, `consumo_real_lph`, `consumo_esperado_lph`, `desviacion_pct`, `tendencia`, `anomalia` |
| Productividad de la flota | RF-07 | `scores_flota.json` | 150 | `maquina_id`, `utilizacion_pct`, `horas_op`, `costo_op`, `categoria` |
| Máquinas a investigar | RF-08 | `scores_pares.json` | 19 | `maquina_id`, `score_desvio`, `variable_principal`, `comparado_con` |
| Incidente grupal / causa común | RF-11 | `scores_causa_comun.json` | 4 | `grupo_id`, `zona`, `ts_inicio`, `ts_fin`, `maquina_ids`, `variable_comun`, `hipotesis`, `centro_lat`, `centro_lon`, `radio_m` |
| Plan semanal | RF-12 | `scores_plan_semanal.json` | 69 | `maquina_id`, `accion`, `prioridad`, `fecha_recomendada`, `componente_probable`, `motivo` |
| Línea de tiempo de la demo | RF-10 | `serie_M-008.json` + `falla_M-008.json` | 721 / 2 | `ts`, `temp`, `vib`, `combustible_lph`, `estado` + `ts_inicio_deriva`, `ts_falla` |

`catalogos.json` (en `contratos/`) trae zonas con coordenadas, modelos, componentes, clima y terreno para filtros y para centrar el mapa.

## Cómo mostrar los datos

- `prob_falla` es un **puntaje de riesgo para ordenar**, no una frecuencia. Mostrar bajo / medio / alto, no un porcentaje.
- Plan semanal: ordenar por `prioridad` (1 = inspección inmediata) y mostrar `motivo` tal cual, ya viene como texto listo.
- Si no hay `inspeccion_inmediata`, mostrar "Sin inspecciones urgentes esta semana" en vez de una tabla vacía.
- `pares` es una lista de máquinas a investigar, no una predicción de falla.
- `causa_comun`: dibujar un círculo en (`centro_lat`, `centro_lon`) con `radio_m` y resaltar las máquinas de `maquina_ids`.

## Demo: falla F-0065 en la máquina M-008 (refrigeración)

- Inicio de deriva: 2026-11-04T08:51:53Z. Falla: 2026-11-10T10:17:44Z. Reparación: 2026-11-11T05:16:54Z.
- `serie_M-008.json` cubre los últimos 30 días de lecturas horarias (también incluye la falla anterior F-0064, de octubre).
- Marcar en el gráfico el inicio de deriva y la falla.

## Pendiente

- `pronostico_horas_uso.json` sale plano (límite inferior = superior = previsto), así que la banda de confianza aún no se ve.
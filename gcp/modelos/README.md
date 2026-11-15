# Modelos de falla con BigQuery ML

## Orden de ejecucion
1. `01_vistas_falla.sql`: crea `v_base_falla`, `entrenamiento_stats` y `v_features_z`.
2. `02_modelo_logit.sql`: entrena `modelo_falla_logit`.
3. `03_modelo_boosted.sql`: entrena `modelo_falla_boosted`.
4. `04_metricas.sql`: calcula `metricas_modelos` (umbral optimo en `val`).
5. `05_pesos_export.sql`: exporta `pesos_modelo_falla` para A.
6. `06_validar_falla.sql`: valida sigmoide, sensibilidad por falla y episodios.
7. `ejecutar_modelos.ps1`: ejecuta el flujo completo.

## Definición de las 9 variables (por máquina, ordenadas por ts)
- `temp_m24`, `vib_m24`, `comb_m24`: media de últimas 24 lecturas (incluida actual). `ROWS BETWEEN 23 PRECEDING AND CURRENT ROW`.
- `temp_d72`, `vib_d72`, `comb_d72`: `m24` menos media de 72 lecturas previas. `m24 - AVG(x) OVER (ROWS BETWEEN 95 PRECEDING AND 24 PRECEDING)`.
- `temp_d14`, `vib_d14`, `comb_d14`: `m24` menos media de 312 lecturas previas. `m24 - AVG(x) OVER (ROWS BETWEEN 335 PRECEDING AND 24 PRECEDING)`.
* Se descartan primeras 336 filas por máquina. Estandarización Z con media y desviación de `train`.

## Particiones temporales
- `train`: `ts < 2026-09-26`
- `gap`: `2026-09-26` a `2026-10-02` (`ts < 2026-10-03`)
- `val`: `2026-10-03` a `2026-10-20` (`ts < 2026-10-21`)
- `gap`: `2026-10-21` a `2026-10-27` (`ts < 2026-10-28`)
- `test`: `ts >= 2026-10-28`

## Regla de alerta confirmada y episodios
- **Alerta confirmada**: Al menos 3 de las últimas 6 lecturas de una máquina con `prob >= umbral`.
- **Episodio**: Alertas confirmadas separadas por < 24 h. Verdadero si ocurre falla en 7 días tras el inicio.

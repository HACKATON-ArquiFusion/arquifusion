# Modelos de falla con BigQuery ML

Orden de ejecucion:
1. `01_vistas_falla.sql`: crea `v_base_falla`, `entrenamiento_stats` y `v_features_z`.
2. `02_modelo_logit.sql`: entrena `modelo_falla_logit`.
3. `03_modelo_boosted.sql`: entrena `modelo_falla_boosted`.
4. `04_metricas.sql`: calcula `metricas_modelos`.
5. `05_pesos_export.sql`: exporta `pesos_modelo_falla` para A.
6. `06_validar_falla.sql`: valida sigmoide, sensibilidad por falla y falsas alertas.
7. `ejecutar_modelos.ps1`: ejecuta los pasos; acepta `-Desde N` y `-Solo N`.

Variables por maquina, ordenadas por `ts`:
- `temp_m24`, `vib_m24`, `comb_m24`: media de las ultimas 24 lecturas, incluida la actual.
- `temp_d72`, `vib_d72`, `comb_d72`: media de las ultimas 24 menos la media de las 72 lecturas anteriores a esas 24.
- Se descartan las primeras 96 filas de cada maquina.

Etiqueta: `etiqueta_falla_7d` renombrada a `etiqueta`; nunca se usa como variable.
Split: train `ts < 2026-10-10`; gap `2026-10-10` a `2026-10-16`; test `ts >= 2026-10-17`.
Estandarizacion: `(x - media_train) / desviacion_train`; estadisticas en `entrenamiento_stats`.
Modelos: solo las seis variables `_z` mas `etiqueta`.
Metricas: umbral que maximiza F1 en train y se reutiliza en test.
Pesos: una fila por `logit-v1` con contrato `pesos_modelo`; `horizonte_dias=7`.
Los pesos de `ML.WEIGHTS` ya vienen denormalizados a la escala de entrada (z-scores).

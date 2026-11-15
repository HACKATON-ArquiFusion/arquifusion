-- Modelo logit con las seis variables estandarizadas y etiqueta binaria.
-- ML.WEIGHTS devuelve los pesos en la escala de las variables de entrada (z-scores).
CREATE OR REPLACE MODEL `arquifusion.modelo_falla_logit`
OPTIONS(
  model_type = 'LOGISTIC_REG',
  input_label_cols = ['etiqueta'],
  auto_class_weights = TRUE,
  data_split_method = 'NO_SPLIT'
) AS
SELECT
  temp_m24_z,
  vib_m24_z,
  comb_m24_z,
  temp_d72_z,
  vib_d72_z,
  comb_d72_z,
  etiqueta
FROM `arquifusion.v_features_z`
WHERE particion = 'train';

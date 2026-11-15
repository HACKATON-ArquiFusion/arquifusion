-- Modelo boosted tree con las mismas seis variables y etiqueta binaria.
CREATE OR REPLACE MODEL `arquifusion.modelo_falla_boosted`
OPTIONS(
  model_type = 'BOOSTED_TREE_CLASSIFIER',
  input_label_cols = ['etiqueta'],
  auto_class_weights = TRUE,
  data_split_method = 'NO_SPLIT',
  max_iterations = 50,
  learn_rate = 0.1,
  subsample = 0.8
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

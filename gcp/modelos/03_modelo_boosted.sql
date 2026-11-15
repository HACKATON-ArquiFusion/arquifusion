-- Modelo boosted tree con las nueve variables estandarizadas y etiqueta binaria.
CREATE OR REPLACE MODEL `arquifusion.modelo_falla_boosted`
OPTIONS(
  model_type = 'BOOSTED_TREE_CLASSIFIER',
  input_label_cols = ['etiqueta'],
  auto_class_weights = TRUE,
  max_iterations = 100,
  learn_rate = 0.1,
  max_tree_depth = 4,
  subsample = 0.7,
  l2_reg = 1,
  min_tree_child_weight = 20,
  early_stop = TRUE,
  data_split_method = 'CUSTOM',
  data_split_col = 'es_val'
) AS
SELECT
  temp_m24_z,
  vib_m24_z,
  comb_m24_z,
  temp_d72_z,
  vib_d72_z,
  comb_d72_z,
  temp_d14_z,
  vib_d14_z,
  comb_d14_z,
  particion = 'val' AS es_val,
  etiqueta
FROM `arquifusion.v_features_z`
WHERE particion IN ('train', 'val');

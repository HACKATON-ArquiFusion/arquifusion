CREATE OR REPLACE MODEL `arquifusion.modelo_falla_boosted_v2`
OPTIONS(
  model_type = 'BOOSTED_TREE_CLASSIFIER',
  input_label_cols = ['etiqueta'],
  auto_class_weights = TRUE,
  max_iterations = 200,
  learn_rate = 0.05,
  max_tree_depth = 6,
  subsample = 0.8,
  colsample_bytree = 0.8,
  l2_reg = 1,
  min_tree_child_weight = 10,
  early_stop = TRUE,
  min_rel_progress = 0.001,
  data_split_method = 'CUSTOM',
  data_split_col = 'es_val'
) AS
SELECT
  * EXCEPT(maquina_id, ts, particion),
  particion = 'val' AS es_val
FROM `arquifusion.v_features_v2`
WHERE particion IN ('train', 'val')

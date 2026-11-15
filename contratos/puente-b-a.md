Estado: PROPUESTA de B, pendiente de revisión de A y C

# Puente B → A (`/scores`)

B envía scores a AWS.

- URL: `POST {api-aws}/scores`
- Header: `x-api-key: <api-key>`
- Body: `{tipo, generado_ts, version_modelo, registros[]}`
- `generado_ts`: ISO 8601 UTC.
- `version_modelo`: string, p. ej. `logit-v1`.

## Respuestas

| Código | Cuerpo |
|---|---|
| `200` | `{recibidos}` |
| `400` | `{error, detalle}` (validación) |
| `401` | `{error: "api key inválida"}` |

## Formatos por tipo

### `prob_falla`

| Campo | Tipo | Unidad / valores |
|---|---|---|
| `maquina_id` | string | `M-001`…`M-999` |
| `ts` | string | ISO 8601 UTC |
| `horizonte_dias` | integer | días |
| `prob_falla` | number | 0–1 |
| `modelo` | string | p. ej. `logit-v1` |

### `pron_repuestos`

| Campo | Tipo | Unidad / valores |
|---|---|---|
| `componente` | string | id de `catalogos.json` |
| `zona` | string | `Z1`…`Z5` |
| `fecha` | string | ISO 8601 (`YYYY-MM-DD`) |
| `demanda_prevista` | number | unidades |
| `limite_inf` | number | unidades |
| `limite_sup` | number | unidades |

### `preventivo`

| Campo | Tipo | Unidad / valores |
|---|---|---|
| `maquina_id` | string | `M-001`…`M-999` |
| `fecha_recomendada` | string | ISO 8601 (`YYYY-MM-DD`) |
| `horas_proyectadas` | number | horas |
| `motivo` | string | texto breve |

### `flota`

| Campo | Tipo | Unidad / valores |
|---|---|---|
| `maquina_id` | string | `M-001`…`M-999` |
| `periodo` | string | ISO 8601 (`YYYY-Www` o `YYYY-MM-DD`) |
| `utilizacion_pct` | number | 0–100 |
| `horas_op` | number | horas |
| `costo_op` | number | PEN |
| `categoria` | string | `normal`, `baja_utilizacion`, `sobreuso`, `baja_prod_alto_costo` |

### `combustible`

| Campo | Tipo | Unidad / valores |
|---|---|---|
| `maquina_id` | string | `M-001`…`M-999` |
| `fecha` | string | ISO 8601 (`YYYY-MM-DD`) |
| `consumo_real_lph` | number | L/h |
| `consumo_esperado_lph` | number | L/h |
| `desviacion_pct` | number | % |
| `tendencia` | string | `creciente`, `estable`, `decreciente` |
| `anomalia` | boolean | `true` / `false` |

### `pares`

| Campo | Tipo | Unidad / valores |
|---|---|---|
| `maquina_id` | string | `M-001`…`M-999` |
| `score_desvio` | number | desviación estandarizada |
| `variable_principal` | string | p. ej. `temp`, `vib`, `combustible_lph` |
| `comparado_con` | string | modelo o grupo |

### `causa_comun`

| Campo | Tipo | Unidad / valores |
|---|---|---|
| `grupo_id` | string | único |
| `zona` | string | `Z1`…`Z5` |
| `ts_inicio` | string | ISO 8601 UTC |
| `ts_fin` | string | ISO 8601 UTC |
| `maquina_ids` | array[string] | ids de máquinas |
| `variable_comun` | string | p. ej. `combustible_lph` |
| `hipotesis` | string | `clima`, `terreno`, `lote_combustible`, `componente`, `otra` |
| `centro_lat` | number | grados |
| `centro_lon` | number | grados |
| `radio_m` | number | metros |

### `pesos_modelo`

| Campo | Tipo | Unidad / valores |
|---|---|---|
| `modelo` | string | p. ej. `logit-v1` |
| `version` | string | p. ej. `2026-11-15` |
| `horizonte_dias` | integer | días |
| `variables` | array[string] | nombres de variables |
| `intercepto` | number | — |
| `pesos` | object | `{variable: peso}` |
| `medias` | object | `{variable: media}` |
| `desviaciones` | object | `{variable: desviación}` |
| `umbral_sugerido` | number | 0–1 |
| `metricas` | object | `{precision, sensibilidad}` en 0–1 |

Las variables se estandarizan antes de aplicar los pesos: `(x - media) / desviación`.

## Pendiente de A

Propuesta por defecto, pendiente de confirmación:
- Variables de detección en línea: `temp`, `vib`, `combustible_lph`.
- Variables derivadas: pendiente de cada una.
- Ventana de detección: 24 h.
- Umbral de riesgo: pendiente de definir por A.

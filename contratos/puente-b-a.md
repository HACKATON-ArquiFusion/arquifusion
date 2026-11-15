Estado: PROPUESTA de B, pendiente de revisión de A y C

# Puente B → A (`/scores`)

B envía scores a AWS.

- URL: `POST {api-aws}/scores`
- Header: `x-api-key: <api-key>`
- Body: `{tipo, generado_ts, version_modelo, registros[]}`
- `generado_ts`: ISO 8601 UTC.
- `version_modelo`: string, p. ej. `logit-v1`.
- Máximo: 500 registros por llamada (propuesta, pendiente de A).

## Respuestas

| Código | Cuerpo |
|---|---|
| `200` | `{recibidos}` |
| `400` | `{error, detalle}` (validación; en `pesos_modelo` lista las variables que A no sabe calcular) |
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
| `variables` | array[string] | nombres de variables (ver gramática) |
| `intercepto` | number | — |
| `pesos` | object | `{variable: peso}` |
| `medias` | object | `{variable: media}` |
| `desviaciones` | object | `{variable: desviación}` |
| `umbral_sugerido` | number | 0–1 |
| `metricas` | object | `{precision, sensibilidad}` en 0–1 |

Las variables se estandarizan antes de aplicar los pesos: `(x - media) / desviación`.
`logit = intercepto + Σ pesos[v] · (x_v - medias[v]) / desviaciones[v]`; `prob_falla = 1 / (1 + e^-logit)`.

#### Nombres de variable aceptados por A

Formato: `<base>[_<estadistica>][_<N>h]`

- `base`: `horas_op`, `temp`, `vib`, `presion`, `combustible_lph`, `carga_t`, `velocidad`, `rpm`.
- `estadistica` (opcional, por defecto el último valor): `media`, `max`, `min`, `std`, `pend`, `rango`.
- Ventana `_<N>h` (opcional, por defecto 24 h). Incluye la lectura actual.
- `pend` en unidades por hora; `std` muestral (n-1).
- Ejemplos: `temp`, `vib_pend`, `combustible_lph_media_24h`, `presion_std_6h`.

`logit-v1` usa 9 variables: `temp`, `vib` y `combustible_lph`, cada una con `_media_24h`, `_media_96h` y `_media_336h`.
Equivale al modelo entrenado con medias de 24 lecturas y deltas frente a 72 y 312 lecturas previas
(`d72 = 4/3·(m24 − m96)`, `d14 = 14/13·(m24 − m336)`); los pesos ya incluyen la estandarización,
por eso `medias = 0` y `desviaciones = 1`. Requiere 336 h de historia por máquina.

#### Verificación

`GET {api-aws}/admin/riesgo/{maquina_id}?ts=...` devuelve `prob_falla`, `umbral`, valores calculados y
`variables_faltantes`. `prob_falla` debe coincidir con `prob_logit` calculado por B para la misma máquina y `ts`.

### `plan_semanal`

| Campo | Tipo | Unidad / valores |
|---|---|---|
| `maquina_id` | string | `M-001`…`M-999` |
| `semana_inicio` | string | ISO 8601 (`YYYY-MM-DD`), primer día de la semana planificada |
| `accion` | string | `inspeccion_inmediata`, `preventivo`, `investigar` |
| `prioridad` | integer | 1 (alta) a 3 |
| `fecha_recomendada` | string | ISO 8601 (`YYYY-MM-DD`) |
| `componente_probable` | string o null | id de `catalogos.json`; solo en `inspeccion_inmediata` |
| `motivo` | string | texto con las señales que originan la acción |

Máximo una acción por máquina y semana (clave: `maquina_id + semana_inicio`). Solo se publican las máquinas con acción.
## Pendiente de A

- Umbral de riesgo: pendiente de definir por A (B sugiere `umbral_sugerido` de `pesos_modelo`).
- Confirmar que las ventanas `_<N>h` equivalen a las últimas N lecturas horarias, incluida la actual.
- Confirmar el máximo de registros por llamada en `/scores`.

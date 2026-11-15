Estado: PROPUESTA de B, pendiente de revisión de A y C

# Puente A → B (`/ingest`)

A envía lotes a Cloud Run.

- URL: `POST {cloud-run}/ingest`
- Header: `Authorization: Bearer <token>`
- Body: `{tipo, registros[]}`
- Máximo: 500 registros por llamada.
- Reintentos con el mismo payload son seguros (idempotencia por las claves indicadas).

## Tipos

| `tipo` | Clave de idempotencia | Campos mínimos |
|---|---|---|
| `telemetria` | `maquina_id + ts` | Ver `telemetria.md`. |
| `incidente` | `incidente_id` | `incidente_id`, `maquina_id`, `ts`, `severidad`, `estado`, `descripcion` |
| `mantenimiento` | `mantenimiento_id` | `mantenimiento_id`, `maquina_id`, `ts`, `tipo_mantenimiento`, `estado`, `componentes[]` |
| `consumo_repuesto` | `consumo_id` | `consumo_id`, `maquina_id`, `ts`, `componente`, `cantidad`, `unidad` |

## Campos de los tipos no telemétricos

| Campo | Tipo | Valores / unidad |
|---|---|---|
| `incidente_id` | string | único |
| `severidad` | string | `baja`, `media`, `alta`, `critica` |
| `estado` (incidente) | string | `abierto`, `en_proceso`, `cerrado` |
| `descripcion` | string | texto breve |
| `mantenimiento_id` | string | único |
| `tipo_mantenimiento` | string | `preventivo`, `correctivo` |
| `estado` (mantenimiento) | string | `programado`, `en_proceso`, `completado` |
| `componentes` | array[string] | ids de `catalogos.json` |
| `consumo_id` | string | único |
| `componente` | string | id de `catalogos.json` |
| `cantidad` | number | unidades |
| `unidad` | string | p. ej. `unidad`, `litro`, `kg` |

`ts` en ISO 8601 UTC. `maquina_id`, `modelo`, `zona` y `componente` usan `catalogos.json`.

## Respuestas

| Código | Cuerpo |
|---|---|
| `200` | `{recibidos, insertados, duplicados}` |
| `400` | `{error, detalle}` (validación) |
| `401` | `{error: "token inválido"}` |

`recibidos = insertados + duplicados`. Un duplicado no se inserta y no es un error.

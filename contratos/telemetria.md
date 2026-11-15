Estado: PROPUESTA de B, pendiente de revisión de A y C

# Telemetría

Registro puntual de una máquina. Clave de idempotencia: `maquina_id + ts`.

## Campos

| Campo | Tipo | Unidad | Rango típico | Descripción |
|---|---|---|---|---|
| `maquina_id` | string | — | `M-001`…`M-999` | Identificador de la máquina. |
| `ts` | string | ISO 8601 UTC | `2026-11-15T14:30:00Z` | Marca de tiempo del registro. |
| `modelo` | string | — | `EXC-320`, `CAM-730`, `CAR-950`, `TRC-D8`, `PER-ROC` | Modelo según `catalogos.json`. |
| `zona` | string | — | `Z1`…`Z5` | Zona según `catalogos.json`. |
| `lat` | number | grados | ≈ -15.48 a -15.52 | Latitud. |
| `lon` | number | grados | ≈ -71.48 a -71.56 | Longitud. |
| `horas_op` | number | horas | 0–50 000 | Horas acumuladas de operación. |
| `temp` | number | °C | 60–110 | Temperatura de operación. |
| `vib` | number | mm/s | 0–15 | Vibración. |
| `presion` | number | bar | 1–300 | Presión del sistema. |
| `combustible_lph` | number | L/h | 5–80 | Consumo de combustible. |
| `carga_t` | number | t | 0–120 | Carga transportada. |
| `velocidad` | number | km/h | 0–60 | Velocidad. |
| `rpm` | number | rpm | 0–2 500 | Revoluciones del motor. |
| `estado` | string | — | `operativa`, `detenida`, `requiere_intervencion` | Estado actual. |
| `contexto` | object | — | ver abajo | Contexto operativo. |

## Contexto

| Campo | Tipo | Valores |
|---|---|---|
| `clima` | string | `soleado`, `lluvia`, `polvo`, `frio` |
| `terreno` | string | `llano`, `pendiente`, `rocoso`, `barro` |
| `lote_combustible` | string | p. ej. `LC-2026-11-A` |

Los rangos son orientativos; los límites duros los define la validación de cada servicio. El ejemplo `telemetria.ejemplo.json` contiene dos registros.

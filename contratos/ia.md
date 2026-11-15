Estado: PROPUESTA de C, pendiente de revisión de A y B

# API de IA (`/ia/*`)

Capa de IA del proyecto. Tres endpoints: `/ia/predecir`, `/ia/explicar` y `/ia/chat`.

## Dónde corre

Bedrock **no está disponible** en las cuentas Learner Lab del equipo: el rol `LabRole` no tiene
`bedrock:InvokeModel` en `us-east-1` ni en `us-west-2`, y en Learner Lab no se pueden crear ni
modificar roles IAM. Se comprobó desde una Lambda con `LabRole` el 2026-11-15 (ver
`prueba-humo-persona-c.md`). Se activa el plan B del plan de trabajo: **Gemini en Vertex AI (GCP)**,
servido desde Cloud Run.

- URL base: `{cloud-run-ia}` (servicio `ia` del proyecto `arquifusion-hackathon-514806`)
- Header: `x-api-key: <api-key>` — la misma clave que usa el puente B → A.
- Todas las respuestas son JSON y llevan `Content-Type: application/json`.

El contrato no depende del modelo que haya detrás. Si mañana apareciera Bedrock, cambia la
implementación y no cambia este documento.

## Degradación

Ningún endpoint depende de que el LLM responda. Si Gemini falla, da timeout o devuelve algo que no
valida, el servicio responde igual con una **plantilla determinista** y marca `"fuente": "plantilla"`.
Un `200` con `fuente: plantilla` es una respuesta correcta, no un error. Quien consume no necesita
distinguirlas para funcionar.

| Campo | Tipo | Valores |
|---|---|---|
| `fuente` | string | `modelo` (respondió Gemini), `plantilla` (respaldo determinista) |
| `latencia_ms` | integer | milisegundos que tardó la llamada |

## Respuestas comunes

| Código | Cuerpo |
|---|---|
| `200` | Según el endpoint. |
| `400` | `{error, detalle}` (validación) |
| `401` | `{error: "api key inválida"}` |
| `503` | `{error: "ia no disponible", detalle}` — solo si falla el modelo **y** la plantilla. |

---

## `POST /ia/predecir`

Segunda opinión sobre una máquina: horizonte y componente probable. **No sustituye al modelo
numérico.** La probabilidad la manda el modelo de B (regresión logística en BigQuery ML); este
endpoint la recibe y la devuelve sin alterarla.

### Petición

| Campo | Tipo | Oblig. | Descripción |
|---|---|---|---|
| `maquina_id` | string | sí | `M-001`…`M-999` |
| `ts` | string | sí | ISO 8601 UTC, momento del análisis |
| `modelo` | string | sí | Modelo de máquina según `catalogos.json` |
| `zona` | string | sí | `Z1`…`Z5` |
| `ventana` | array[object] | sí | Lecturas de telemetría ordenadas por `ts`, 2 a 200 registros. Mismos campos que `telemetria.md`. |
| `score_modelo` | object | no | Salida del modelo de B. Si falta, `prob_falla` se devuelve `null`. |
| `score_modelo.prob_falla` | number | — | 0–1 |
| `score_modelo.horizonte_dias` | integer | — | días |
| `score_modelo.modelo` | string | — | p. ej. `logit-v1` |

### Respuesta `200`

| Campo | Tipo | Valores |
|---|---|---|
| `maquina_id` | string | eco de la petición |
| `prob_falla` | number \| null | 0–1. Es `score_modelo.prob_falla` tal cual; `null` si no vino. |
| `horizonte_dias` | integer | 1–30 |
| `componente_probable` | string | id de `componentes` en `catalogos.json` |
| `confianza` | number | 0–1. Confianza del LLM en el componente, **no** en la probabilidad. |
| `variables_clave` | array[string] | hasta 3 nombres de variables de `telemetria.md` |
| `fuente` | string | `modelo` \| `plantilla` |
| `latencia_ms` | integer | ms |

`prob_falla` y `confianza` son cosas distintas y no se deben sumar ni promediar: la primera es del
modelo numérico, la segunda es del LLM. En el pitch se muestran por separado.

Plantilla de respaldo: `componente_probable` sale de la variable con mayor desviación de la ventana
(`temp` → `refrigeracion`, `vib` → `tren_rodaje`, `presion` → `sistema_hidraulico`,
`combustible_lph` → `inyeccion_combustible`, `rpm` → `motor`), `horizonte_dias` = 7 y
`confianza` = 0.3.

---

## `POST /ia/explicar`

Convierte un incidente en lenguaje para personas. Devuelve una causa, una acción y **un mensaje
distinto por rol**. Lo consume el flujo 1 de Step Functions de A, antes de notificar.

### Petición

| Campo | Tipo | Oblig. | Descripción |
|---|---|---|---|
| `incidente_id` | string | sí | único |
| `maquina_id` | string | sí | `M-001`…`M-999` |
| `ts` | string | sí | ISO 8601 UTC |
| `severidad` | string | sí | `baja`, `media`, `alta`, `critica` |
| `zona` | string | sí | `Z1`…`Z5` |
| `modelo` | string | no | Modelo de máquina |
| `desviaciones` | object | sí | `{variable: sigmas}`, p. ej. `{"temp": 3.4, "vib": 3.9}` |
| `ventana` | array[object] | no | Lecturas recientes, hasta 50 |
| `contexto` | object | no | `{clima, terreno, lote_combustible}` |
| `repuesto_reservado` | string | no | id de componente |
| `tecnico_asignado` | string | no | id del técnico |
| `roles` | array[string] | no | Por defecto `["operador","tecnico","supervisor","gerente"]` |

### Respuesta `200`

| Campo | Tipo | Valores |
|---|---|---|
| `incidente_id` | string | eco |
| `causa_probable` | string | texto breve, 1 frase |
| `componente` | string | id de `componentes` |
| `accion_recomendada` | string | texto breve, 1 frase |
| `urgencia` | string | `rutina`, `programar`, `atender_hoy`, `inmediata` |
| `mensajes` | array[object] | uno por rol pedido |
| `mensajes[].rol` | string | `operador`, `tecnico`, `supervisor`, `gerente` |
| `mensajes[].titulo` | string | ≤ 60 caracteres |
| `mensajes[].mensaje` | string | ≤ 240 caracteres |
| `fuente` | string | `modelo` \| `plantilla` |
| `latencia_ms` | integer | ms |

Los roles coinciden con los que ya usa `GET /notificaciones?rol=` de A. `titulo` y `mensaje` entran
tal cual en la tabla de notificaciones y en el cuerpo de SNS, sin recortes adicionales.

Nivel de detalle por rol, que es lo que pide el caso:

| Rol | Qué recibe |
|---|---|
| `operador` | Qué hacer ahora con su máquina. Sin cifras técnicas. |
| `tecnico` | Causa, variables desviadas con sus sigmas, repuesto y zona. |
| `supervisor` | Impacto en la operación, máquinas afectadas y tiempo estimado. |
| `gerente` | Una frase: cuántos equipos parados y si hay impacto. Sin jerga. |

---

## `POST /ia/chat`

Asistente de RF-13. Responde preguntas sobre la operación consultando la API de A. No inventa
datos: si la API no los tiene, lo dice.

### Petición

| Campo | Tipo | Oblig. | Descripción |
|---|---|---|---|
| `mensaje` | string | sí | Pregunta del usuario, ≤ 500 caracteres |
| `rol` | string | no | Rol de quien pregunta, ajusta el detalle. Por defecto `supervisor`. |
| `historial` | array[object] | no | Turnos previos, hasta 10. `{autor: "usuario"\|"asistente", texto}` |

### Respuesta `200`

| Campo | Tipo | Valores |
|---|---|---|
| `respuesta` | string | texto, ≤ 600 caracteres |
| `datos_usados` | array[object] | de dónde salió la respuesta |
| `datos_usados[].endpoint` | string | p. ej. `/flota`, `/kpis`, `/incidentes` |
| `datos_usados[].resumen` | string | qué se tomó de ahí |
| `fuente` | string | `modelo` \| `plantilla` |
| `latencia_ms` | integer | ms |

`datos_usados` nunca va vacío cuando la respuesta afirma algo sobre la flota: es lo que permite
comprobar en el pitch que el asistente lee datos reales y no alucina.

---

## Pendiente de A

- Confirmar que el flujo 1 de Step Functions llama a `/ia/explicar` con estos campos.
- Dejar `{cloud-run-ia}` en SSM Parameter Store y la api-key en Secrets Manager.
- Confirmar los 4 roles (`operador`, `tecnico`, `supervisor`, `gerente`). Los ejemplos actuales de
  `notificaciones.json` solo usan tres: falta `supervisor`.

## Pendiente de B

- Confirmar que `score_modelo` de `/ia/predecir` se llena con el mismo formato que `prob_falla`
  de `puente-b-a.md`.
- `roles` y `urgencia` no están en `catalogos.json`: conviene añadirlos ahí para no repetirlos.

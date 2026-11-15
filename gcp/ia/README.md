# Capa de IA ArquiFusion

Cloud Run con los tres endpoints de `contratos/ia.md`: `/ia/predecir`, `/ia/explicar` y `/ia/chat`.
Modelo: `gemini-3.8-flash` en Vertex AI, region `global`. Autenticacion: header `x-api-key`.

Desplegado en: https://ia-1073552578418.us-central1.run.app

Bedrock quedo descartado: `LabRole` no tiene `bedrock:InvokeModel` en `us-east-1` ni `us-west-2`,
comprobado el 2026-11-15 desde una Lambda en las dos cuentas Learner Lab del equipo.

El servicio corre en `us-central1` pero llama al modelo en `global`: los modelos Gemini de este
proyecto solo resuelven en esa ubicacion. `gemini-4-carbon` responde pero devuelve 429 la mayoria
de las veces; `gemini-3.8-flash` es estable. Ante 429 o 503 hay 3 reintentos con espera creciente.

Despliegue:

```
.\desplegar.ps1 -IaApiKey "<clave>" -ApiAwsUrl "https://<api>.execute-api.us-east-1.amazonaws.com/prod" -ApiAwsKey "<api-key-de-A>"
```

Variables de entorno del servicio:

| Variable | Para que |
|---|---|
| `GCP_PROJECT`, `VERTEX_LOCATION` | Proyecto y ubicacion del modelo. `global` por defecto. |
| `GEMINI_MODELO` | Por defecto `gemini-3.8-flash`. |
| `MAX_TOKENS` | 4000. El modelo razona antes de responder y gasta ~700 en eso. |
| `IA_API_KEY` | Clave que exige este servicio. |
| `API_AWS_URL`, `API_AWS_KEY` | API de A, la consulta `/ia/chat`. |
| `TIMEOUT_MODELO`, `TIMEOUT_API`, `REINTENTOS` | 20 s, 8 s y 3 por defecto. |

Ningun endpoint depende de que Gemini responda: si falla, devuelve `200` con `fuente: plantilla`
y un texto determinista. `GET /health` no pide api key.

Las plantillas deducen el componente por la variable con mayor desviacion de la ventana
(`temp` -> refrigeracion, `vib` -> tren_rodaje, `presion` -> sistema_hidraulico,
`combustible_lph` -> inyeccion_combustible, `rpm` -> motor).

`/ia/predecir` no recalcula la probabilidad: devuelve `score_modelo.prob_falla` de B tal cual.
`confianza` es del LLM sobre el componente y es otra cosa; no se promedian.

"""Cloud Run publicar-scores: lee tablas de BigQuery y las publica en /scores de A.

POST /publicar?tipo=<tipo|todos>[&enviar=1][&completo=1]
  - Sin enviar=1 es un dry-run: arma los registros y devuelve conteos y una muestra.
  - Con enviar=1 hace POST a {AWS_API_URL}/scores con el header x-api-key.
  - completo=1 devuelve todos los registros en la respuesta (solo para revisar).
"""
import json
import logging
import os
import time
from datetime import date, datetime, timezone
from decimal import Decimal

import requests
from flask import Flask, jsonify, request
from google.cloud import bigquery

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

DATASET = os.environ.get("BQ_DATASET", "arquifusion")
AWS_API_URL = os.environ.get("AWS_API_URL", "").strip().rstrip("/")
AWS_API_KEY = os.environ.get("AWS_API_KEY", "").strip()
TAM_LOTE = 500
client = bigquery.Client()


class ErrorEnvio(Exception):
    pass


def limpiar(valor, redondear=True):
    """Convierte fechas a ISO 8601 y redondea decimales (salvo que se pida lo contrario)."""
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            valor = valor.replace(tzinfo=timezone.utc)
        return valor.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, (float, Decimal)):
        return round(float(valor), 4) if redondear else float(valor)
    return valor


def filas(sql, redondear=True):
    return [
        {k: limpiar(v, redondear) for k, v in dict(fila.items()).items()}
        for fila in client.query(sql).result()
    ]


def reg_prob_falla():
    return filas(f"""
SELECT
  maquina_id,
  ts,
  7 AS horizonte_dias,
  prob_logit AS prob_falla,
  'logit-v1' AS modelo
FROM `{DATASET}.predicciones_falla_actual`
ORDER BY maquina_id
""")


def reg_pron_repuestos():
    return filas(f"""
SELECT
  componente,
  zona,
  fecha,
  demanda_prevista,
  limite_inf,
  limite_sup
FROM `{DATASET}.pronostico_repuestos`
ORDER BY componente, zona, fecha
""")


def reg_preventivo():
    return filas(f"""
SELECT
  maquina_id,
  fecha_recomendada,
  horas_proyectadas,
  motivo
FROM `{DATASET}.preventivo_propuesto`
WHERE fecha_recomendada IS NOT NULL
ORDER BY fecha_recomendada, maquina_id
""")


def reg_combustible():
    # Solo los ultimos 7 dias para no saturar el dashboard con 30 dias de historial.
    return filas(f"""
WITH fin AS (
  SELECT MAX(fecha) AS dmax
  FROM `{DATASET}.anomalias_combustible`
)
SELECT
  a.maquina_id,
  a.fecha,
  a.consumo_real_lph,
  a.consumo_esperado_lph,
  a.desviacion_pct,
  a.tendencia,
  a.anomalia
FROM `{DATASET}.anomalias_combustible` a
CROSS JOIN fin
WHERE a.fecha > DATE_SUB(fin.dmax, INTERVAL 7 DAY)
ORDER BY a.fecha, a.maquina_id
""")


def reg_pesos_modelo():
    # Sin redondeo: los pesos deben reproducir exactamente a ML.PREDICT.
    regs = filas(f"""
SELECT
  modelo,
  version,
  horizonte_dias,
  variables,
  intercepto,
  pesos,
  medias,
  desviaciones,
  umbral_sugerido,
  metricas
FROM `{DATASET}.pesos_modelo_falla`
""", redondear=False)
    for reg in regs:
        for campo in ("pesos", "medias", "desviaciones", "metricas"):
            if isinstance(reg.get(campo), str):
                reg[campo] = json.loads(reg[campo])
    return regs


# tipo del contrato -> (version_modelo del sobre, funcion que arma los registros)
TIPOS = {
    "prob_falla": ("logit-v1", reg_prob_falla),
    "pron_repuestos": ("arima-plus-v1", reg_pron_repuestos),
    "preventivo": ("arima-plus-v1", reg_preventivo),
    "combustible": ("arima-plus-v1", reg_combustible),
    "pesos_modelo": ("logit-v1", reg_pesos_modelo),
}


def enviar_lote(tipo, generado_ts, version, lote):
    cuerpo = {
        "tipo": tipo,
        "generado_ts": generado_ts,
        "version_modelo": version,
        "registros": lote,
    }
    ultimo_error = "sin respuesta"
    for intento in range(3):
        try:
            r = requests.post(
                f"{AWS_API_URL}/scores",
                json=cuerpo,
                headers={"x-api-key": AWS_API_KEY},
                timeout=30,
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code in (400, 401):
                raise ErrorEnvio(f"{r.status_code}: {r.text[:300]}")
            ultimo_error = f"{r.status_code}: {r.text[:200]}"
        except requests.RequestException as e:
            ultimo_error = str(e)[:200]
        time.sleep(2 ** intento)
    raise ErrorEnvio(f"fallo tras 3 intentos: {ultimo_error}")


def respaldar_en_bigquery(tipo, generado_ts, version, registros):
    """Copia lo publicado en la tabla scores (respaldo en la otra nube)."""
    filas_scores = [
        {
            "tipo": tipo,
            "generado_ts": generado_ts,
            "version_modelo": version,
            "registro": json.dumps(reg),
        }
        for reg in registros
    ]
    errores = []
    for i in range(0, len(filas_scores), TAM_LOTE):
        errores += client.insert_rows_json(f"{DATASET}.scores", filas_scores[i:i + TAM_LOTE])
    return not errores


@app.get("/")
def salud():
    return jsonify({"estado": "ok", "envio_configurado": bool(AWS_API_URL and AWS_API_KEY)})


@app.post("/publicar")
def publicar():
    tipo = request.args.get("tipo", "todos")
    enviar = request.args.get("enviar") == "1"
    completo = request.args.get("completo") == "1"

    if tipo == "todos":
        seleccion = list(TIPOS)
    elif tipo in TIPOS:
        seleccion = [tipo]
    else:
        return jsonify({"error": "tipo no disponible", "disponibles": list(TIPOS)}), 400

    if enviar and not (AWS_API_URL and AWS_API_KEY):
        return jsonify({"error": "faltan AWS_API_URL o AWS_API_KEY; no se puede enviar"}), 400

    generado_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    resultados = []
    hubo_error = False

    for nombre in seleccion:
        version, armar = TIPOS[nombre]
        res = {"tipo": nombre, "version_modelo": version}
        try:
            registros = armar()
            lotes = [registros[i:i + TAM_LOTE] for i in range(0, len(registros), TAM_LOTE)]
            res["registros"] = len(registros)
            res["lotes"] = len(lotes)
            if completo:
                res["payload"] = registros
            else:
                res["muestra"] = registros[:2]
            if enviar and registros:
                res["respuestas"] = [enviar_lote(nombre, generado_ts, version, lote) for lote in lotes]
                res["respaldo_bigquery"] = respaldar_en_bigquery(nombre, generado_ts, version, registros)
        except Exception as e:  # noqa: BLE001
            logging.exception("fallo publicando %s", nombre)
            res["error"] = str(e)[:300]
            hubo_error = True
        resultados.append(res)

    cuerpo = {
        "modo": "enviado" if enviar else "dry-run",
        "generado_ts": generado_ts,
        "resultados": resultados,
    }
    return jsonify(cuerpo), (502 if hubo_error else 200)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))

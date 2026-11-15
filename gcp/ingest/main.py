"""Cloud Run ingest: recibe lotes de A y los carga en BigQuery sin duplicados."""
import hmac
import json
import logging
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from google.cloud import bigquery

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

DATASET = os.environ.get("BQ_DATASET", "arquifusion")
TOKEN = os.environ.get("INGEST_TOKEN", "").strip()
MAX_REGISTROS = 500
client = bigquery.Client()

# Por tipo: tabla, claves de idempotencia, columnas (nombre, tipo BigQuery).
TIPOS = {
    "telemetria": {
        "tabla": "telemetria",
        "claves": ["maquina_id", "ts"],
        "requeridos": ["maquina_id", "ts"],
        "columnas": [
            ("maquina_id", "STRING"), ("ts", "TIMESTAMP"), ("modelo", "STRING"),
            ("zona", "STRING"), ("lat", "FLOAT64"), ("lon", "FLOAT64"),
            ("horas_op", "FLOAT64"), ("temp", "FLOAT64"), ("vib", "FLOAT64"),
            ("presion", "FLOAT64"), ("combustible_lph", "FLOAT64"),
            ("carga_t", "FLOAT64"), ("velocidad", "FLOAT64"), ("rpm", "INT64"),
            ("estado", "STRING"), ("contexto_clima", "STRING"),
            ("contexto_terreno", "STRING"), ("contexto_lote_combustible", "STRING"),
        ],
        "usa_rango_ts": True,
        "ingest_ts": True,
    },
    "incidente": {
        "tabla": "incidentes",
        "claves": ["incidente_id"],
        "requeridos": ["incidente_id", "maquina_id", "ts", "severidad", "estado", "descripcion"],
        "columnas": [
            ("incidente_id", "STRING"), ("maquina_id", "STRING"), ("ts", "TIMESTAMP"),
            ("severidad", "STRING"), ("estado", "STRING"), ("descripcion", "STRING"),
        ],
    },
    "mantenimiento": {
        "tabla": "mantenimientos",
        "claves": ["mantenimiento_id"],
        "requeridos": ["mantenimiento_id", "maquina_id", "ts", "tipo_mantenimiento", "estado", "componentes"],
        "columnas": [
            ("mantenimiento_id", "STRING"), ("maquina_id", "STRING"), ("ts", "TIMESTAMP"),
            ("tipo_mantenimiento", "STRING"), ("estado", "STRING"), ("componentes", "STRING"),
        ],
    },
    "consumo_repuesto": {
        "tabla": "consumo_repuestos",
        "claves": ["consumo_id"],
        "requeridos": ["consumo_id", "maquina_id", "ts", "componente", "cantidad", "unidad"],
        "columnas": [
            ("consumo_id", "STRING"), ("maquina_id", "STRING"), ("ts", "TIMESTAMP"),
            ("componente", "STRING"), ("cantidad", "INT64"), ("unidad", "STRING"),
        ],
    },
}


class ErrorValidacion(Exception):
    pass


def parse_ts(valor):
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        raise ErrorValidacion(f"ts invalido: {valor}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalizar(tipo, reg):
    """Aplana el contexto y convierte listas, segun el esquema de BigQuery."""
    out = dict(reg)
    if tipo == "telemetria":
        ctx = out.pop("contexto", None) or {}
        out["contexto_clima"] = ctx.get("clima")
        out["contexto_terreno"] = ctx.get("terreno")
        out["contexto_lote_combustible"] = ctx.get("lote_combustible")
    if tipo == "mantenimiento":
        comp = out.get("componentes")
        if isinstance(comp, list):
            out["componentes"] = ";".join(str(c) for c in comp)
    return out


def expr_columna(nombre, tipo_bq):
    base = f"JSON_VALUE(r, '$.{nombre}')"
    if tipo_bq == "STRING":
        return base
    if tipo_bq == "TIMESTAMP":
        return f"SAFE_CAST({base} AS TIMESTAMP)"
    if tipo_bq == "FLOAT64":
        return f"SAFE_CAST({base} AS FLOAT64)"
    if tipo_bq == "INT64":
        return f"SAFE_CAST(SAFE_CAST({base} AS FLOAT64) AS INT64)"
    raise ValueError(tipo_bq)


def construir_merge(cfg):
    cols = cfg["columnas"]
    nombres = [c[0] for c in cols]
    select_cols = ",\n      ".join(f"{expr_columna(n, t)} AS {n}" for n, t in cols)
    particion = ", ".join(cfg["claves"])
    on = " AND ".join(f"T.{k} = S.{k}" for k in cfg["claves"])
    if cfg.get("usa_rango_ts"):
        on += " AND T.ts BETWEEN @min_ts AND @max_ts"
    insert_cols = ", ".join(nombres)
    insert_vals = ", ".join(f"S.{n}" for n in nombres)
    if cfg.get("ingest_ts"):
        insert_cols += ", ingest_ts"
        insert_vals += ", CURRENT_TIMESTAMP()"
    return f"""
MERGE `{DATASET}.{cfg['tabla']}` T
USING (
  SELECT * EXCEPT (fila) FROM (
    SELECT
      *,
      ROW_NUMBER() OVER (PARTITION BY {particion} ORDER BY off) AS fila
    FROM (
      SELECT
        {select_cols},
        off
      FROM UNNEST(JSON_QUERY_ARRAY(PARSE_JSON(@payload))) AS r WITH OFFSET AS off
    )
  ) WHERE fila = 1
) S
ON {on}
WHEN NOT MATCHED THEN
  INSERT ({insert_cols}) VALUES ({insert_vals})
"""


def token_valido():
    cabecera = request.headers.get("Authorization", "")
    if not TOKEN or not cabecera.startswith("Bearer "):
        return False
    return hmac.compare_digest(cabecera[7:].strip(), TOKEN)


@app.get("/")
def salud():
    return jsonify({"estado": "ok"})


@app.post("/ingest")
def ingest():
    if not token_valido():
        return jsonify({"error": "token inválido"}), 401

    cuerpo = request.get_json(silent=True)
    if not isinstance(cuerpo, dict):
        return jsonify({"error": "validación", "detalle": "el cuerpo debe ser un objeto JSON"}), 400

    tipo = cuerpo.get("tipo")
    registros = cuerpo.get("registros")
    if tipo not in TIPOS:
        return jsonify({"error": "validación", "detalle": f"tipo desconocido: {tipo}"}), 400
    if not isinstance(registros, list) or not registros:
        return jsonify({"error": "validación", "detalle": "registros debe ser una lista no vacía"}), 400
    if len(registros) > MAX_REGISTROS:
        return jsonify({"error": "validación", "detalle": f"máximo {MAX_REGISTROS} registros por llamada"}), 400

    cfg = TIPOS[tipo]
    try:
        normalizados = []
        marcas = []
        for i, reg in enumerate(registros):
            if not isinstance(reg, dict):
                raise ErrorValidacion(f"registro {i} no es un objeto")
            faltan = [c for c in cfg["requeridos"] if reg.get(c) in (None, "")]
            if faltan:
                raise ErrorValidacion(f"registro {i}: faltan campos {faltan}")
            marcas.append(parse_ts(reg["ts"]))
            normalizados.append(normalizar(tipo, reg))
    except ErrorValidacion as e:
        return jsonify({"error": "validación", "detalle": str(e)}), 400

    params = [bigquery.ScalarQueryParameter("payload", "STRING", json.dumps(normalizados))]
    if cfg.get("usa_rango_ts"):
        params.append(bigquery.ScalarQueryParameter("min_ts", "TIMESTAMP", min(marcas)))
        params.append(bigquery.ScalarQueryParameter("max_ts", "TIMESTAMP", max(marcas)))

    try:
        job = client.query(
            construir_merge(cfg),
            job_config=bigquery.QueryJobConfig(query_parameters=params),
        )
        job.result()
        insertados = job.num_dml_affected_rows or 0
    except Exception as e:  # noqa: BLE001
        logging.exception("fallo MERGE en BigQuery")
        return jsonify({"error": "interno", "detalle": str(e)[:300]}), 500

    recibidos = len(registros)
    return jsonify({
        "recibidos": recibidos,
        "insertados": insertados,
        "duplicados": recibidos - insertados,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
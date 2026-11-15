import hmac
import json
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# ---------------------------------------------------------------- configuración
PREFIJO = os.environ.get("PREFIJO", "arquifusion")
QUEUE_URL = os.environ.get("QUEUE_URL", "")
API_KEY_ENV = os.environ.get("API_KEY", "")  # respaldo si Secrets Manager no responde
SECRET_API_KEY = os.environ.get("SECRET_API_KEY", PREFIJO + "/api-key")
BASE = os.path.join(os.path.dirname(__file__), "ejemplos")

MAX_INGESTA = 1000  # registros por llamada a /ingesta
CHUNK = 25          # registros por mensaje SQS

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,x-api-key",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}

# ---------------------------------------------------------------- catálogos (contratos de B)
ZONAS = {"Z1", "Z2", "Z3", "Z4", "Z5"}
MODELOS = {"EXC-320", "CAM-730", "CAR-950", "TRC-D8", "PER-ROC"}
ESTADOS_MAQ = {"operativa", "detenida", "requiere_intervencion"}
CLIMAS = {"soleado", "lluvia", "polvo", "frio"}
TERRENOS = {"llano", "pendiente", "rocoso", "barro"}
# límites duros (holgados) de cada variable numérica de telemetría
LIMITES = {
    "lat": (-90, 90), "lon": (-180, 180), "horas_op": (0, 100000),
    "temp": (-50, 200), "vib": (0, 100), "presion": (0, 1000),
    "combustible_lph": (0, 500), "carga_t": (0, 500),
    "velocidad": (0, 200), "rpm": (0, 10000),
}
RE_MAQ = re.compile(r"^M-\d{3}$")
RE_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# ---------------------------------------------------------------- utilidades AWS
_CACHE = {}


def _cacheado(clave, segundos, fn):
    ahora = time.time()
    v = _CACHE.get(clave)
    if v and v[0] > ahora:
        return v[1]
    val = fn()
    _CACHE[clave] = (ahora + segundos, val)
    return val


def cliente(nombre):
    return _cacheado(("c", nombre), 10 ** 9, lambda: boto3.client(nombre))


def tabla(nombre):
    return _cacheado(("t", nombre), 10 ** 9,
                     lambda: boto3.resource("dynamodb").Table(PREFIJO + "-" + nombre))


def api_key():
    """API key: Secrets Manager (cache 5 min); si falla, variable de entorno."""
    def leer():
        try:
            v = cliente("secretsmanager").get_secret_value(SecretId=SECRET_API_KEY)["SecretString"]
            try:
                d = json.loads(v)
                if isinstance(d, dict):
                    v = d.get("api_key", "")
            except json.JSONDecodeError:
                pass
            return v
        except Exception as ex:
            print("secreto no disponible, uso variable de entorno:", type(ex).__name__)
            return API_KEY_ENV
    return _cacheado("api_key", 300, leer)


def param(nombre, defecto):
    """Parámetro global de SSM (cache 5 min); si falla, el valor por defecto."""
    def leer():
        try:
            return cliente("ssm").get_parameter(Name="/" + PREFIJO + "/" + nombre)["Parameter"]["Value"]
        except Exception:
            return defecto
    return _cacheado(("p", nombre), 300, leer)


# ---------------------------------------------------------------- utilidades HTTP
def _dec(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(type(o).__name__)


def cargar(nombre):
    with open(os.path.join(BASE, nombre), encoding="utf-8") as f:
        return json.load(f)


def resp(codigo, cuerpo):
    return {
        "statusCode": codigo,
        "headers": {"Content-Type": "application/json", **CORS},
        "body": json.dumps(cuerpo, ensure_ascii=False, default=_dec),
    }


def cuerpo_json(ev):
    try:
        raw = ev.get("body") or "{}"
        if ev.get("isBase64Encoded"):
            import base64
            raw = base64.b64decode(raw).decode("utf-8")
        return json.loads(raw, parse_float=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None


def query(ev):
    return ev.get("queryStringParameters") or {}


def err400(error, detalle):
    return resp(400, {"error": error, "detalle": detalle})


def es_num(x):
    return isinstance(x, (int, float, Decimal)) and not isinstance(x, bool)


def a_epoch(ts):
    return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def ahora_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_valido(ts):
    if not isinstance(ts, str) or not RE_TS.match(ts):
        return False
    try:
        a_epoch(ts)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- telemetría: validación
def validar_telemetria(r):
    """Devuelve None si el registro es válido, o el motivo del rechazo."""
    if not isinstance(r, dict):
        return "el registro no es un objeto"
    if not RE_MAQ.match(str(r.get("maquina_id", ""))):
        return "maquina_id inválido (M-001..M-999)"
    if not ts_valido(r.get("ts")):
        return "ts inválido (ISO 8601 UTC, p. ej. 2026-11-15T14:30:00Z)"
    if r.get("modelo") not in MODELOS:
        return "modelo inválido"
    if r.get("zona") not in ZONAS:
        return "zona inválida"
    for campo, (lo, hi) in LIMITES.items():
        v = r.get(campo)
        if not es_num(v) or not (lo <= v <= hi):
            return campo + " inválido o fuera de rango"
    if r.get("estado") not in ESTADOS_MAQ:
        return "estado inválido"
    ctx = r.get("contexto")
    if ctx is not None:
        if not isinstance(ctx, dict):
            return "contexto debe ser un objeto"
        if "clima" in ctx and ctx["clima"] not in CLIMAS:
            return "contexto.clima inválido"
        if "terreno" in ctx and ctx["terreno"] not in TERRENOS:
            return "contexto.terreno inválido"
    return None


# ---------------------------------------------------------------- telemetría: ingesta (API -> SQS)
def encolar(registros):
    sqs = cliente("sqs")
    mensajes = [registros[i:i + CHUNK] for i in range(0, len(registros), CHUNK)]
    for j in range(0, len(mensajes), 10):
        entradas = [{"Id": str(k), "MessageBody": json.dumps(chunk, default=_dec)}
                    for k, chunk in enumerate(mensajes[j:j + 10])]
        r = sqs.send_message_batch(QueueUrl=QUEUE_URL, Entries=entradas)
        if r.get("Failed"):
            raise RuntimeError(str(len(r["Failed"])) + " mensajes no se pudieron encolar")


def post_ingesta(ev, m):
    b = cuerpo_json(ev)
    if b is None:
        return err400("JSON inválido", "el cuerpo no es JSON")
    if isinstance(b, list):
        registros = b
    elif isinstance(b, dict) and isinstance(b.get("registros"), list):
        registros = b["registros"]
    elif isinstance(b, dict) and b.get("maquina_id"):
        registros = [b]
    else:
        return err400("formato inválido", "se espera un arreglo o {registros: [...]}")
    if not registros:
        return err400("sin registros", "la lista de registros está vacía")
    if len(registros) > MAX_INGESTA:
        return err400("demasiados registros", "máximo " + str(MAX_INGESTA) + " por llamada")

    buenos, errores = [], []
    for i, r in enumerate(registros):
        motivo = validar_telemetria(r)
        if motivo:
            errores.append({"indice": i, "motivo": motivo})
        else:
            buenos.append(r)
    if not buenos:
        return err400("ningún registro válido", errores[:20])
    if not QUEUE_URL:
        return resp(500, {"error": "cola no configurada", "detalle": "falta la variable QUEUE_URL"})
    try:
        encolar(buenos)
    except Exception as ex:
        print("ERROR encolando:", repr(ex))
        return resp(500, {"error": "no se pudo encolar", "detalle": str(ex)[:200]})
    return resp(202, {
        "recibidos": len(registros),
        "aceptados": len(buenos),
        "rechazados": [e["indice"] for e in errores],
        "errores": errores[:20],
    })


# ---------------------------------------------------------------- telemetría: procesado (SQS -> DynamoDB)
CAMPOS_MAQ = ["modelo", "zona", "lat", "lon", "horas_op", "temp", "vib", "presion",
              "combustible_lph", "carga_t", "velocidad", "rpm", "estado", "contexto"]


def actualizar_maquina(r):
    """Deja en 'maquinas' el último estado conocido (solo si el registro es más nuevo)."""
    campos = {"ultimo_ts": r["ts"]}
    for c in CAMPOS_MAQ:
        if c in r:
            campos[c] = r[c]
    nombres = {"#c" + str(i): k for i, k in enumerate(campos)}
    valores = {":v" + str(i): v for i, v in enumerate(campos.values())}
    expr = "SET " + ", ".join("#c" + str(i) + " = :v" + str(i) for i in range(len(campos)))
    try:
        tabla("maquinas").update_item(
            Key={"maquina_id": r["maquina_id"]},
            UpdateExpression=expr,
            ConditionExpression="attribute_not_exists(#c0) OR #c0 < :v0",
            ExpressionAttributeNames=nombres,
            ExpressionAttributeValues=valores,
        )
    except ClientError as ex:
        if ex.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def guardar_lote(registros):
    t = tabla("telemetria")
    ttl_s = int(float(param("ttl_dias", "30")) * 86400)
    ahora = int(time.time())
    ultimos = {}
    ins = dup = desc = 0
    for r in registros:
        motivo = validar_telemetria(r)
        if motivo:
            print("descartado:", motivo)
            desc += 1
            continue
        prev = ultimos.get(r["maquina_id"])
        if prev is None or r["ts"] > prev["ts"]:
            ultimos[r["maquina_id"]] = r
        item = dict(r)
        item["ttl"] = max(a_epoch(r["ts"]), ahora) + ttl_s
        item["recibido_ts"] = ahora_iso()
        try:
            # idempotencia: la clave maquina_id + ts solo se inserta una vez
            t.put_item(Item=item, ConditionExpression="attribute_not_exists(maquina_id)")
            ins += 1
        except ClientError as ex:
            if ex.response["Error"]["Code"] == "ConditionalCheckFailedException":
                dup += 1
            else:
                raise
    for r in ultimos.values():
        actualizar_maquina(r)
    print(json.dumps({"insertados": ins, "duplicados": dup, "descartados": desc}))
    return ins, dup, desc


def procesar(event, context):
    """Lambda disparada por SQS. Devuelve solo los mensajes que fallaron."""
    fallos = []
    for rec in event.get("Records", []):
        try:
            registros = json.loads(rec["body"], parse_float=Decimal)
            guardar_lote(registros)
        except Exception as ex:
            print("ERROR mensaje", rec.get("messageId"), repr(ex))
            fallos.append({"itemIdentifier": rec["messageId"]})
    return {"batchItemFailures": fallos}


# ---------------------------------------------------------------- telemetría: lectura del historial
def get_historial(ev, m):
    maq = m.group(1)
    if not RE_MAQ.match(maq):
        return err400("maquina_id inválido", "formato M-001..M-999")
    try:
        n = int(query(ev).get("n") or param("n_lecturas", "336"))
    except ValueError:
        return err400("n inválido", "n debe ser un entero")
    n = max(1, min(n, 1000))
    r = tabla("telemetria").query(
        KeyConditionExpression=Key("maquina_id").eq(maq),
        ScanIndexForward=False, Limit=n, ConsistentRead=True)
    lecturas = list(reversed(r["Items"]))
    for x in lecturas:
        x.pop("ttl", None)
    return resp(200, {"maquina_id": maq, "n": len(lecturas), "lecturas": lecturas})


# ---------------------------------------------------------------- scores (B -> A)
REQ = {
    "prob_falla": ["maquina_id", "ts", "horizonte_dias", "prob_falla", "modelo"],
    "pron_repuestos": ["componente", "zona", "fecha", "demanda_prevista", "limite_inf", "limite_sup"],
    "preventivo": ["maquina_id", "fecha_recomendada", "horas_proyectadas", "motivo"],
    "flota": ["maquina_id", "periodo", "utilizacion_pct", "horas_op", "costo_op", "categoria"],
    "combustible": ["maquina_id", "fecha", "consumo_real_lph", "consumo_esperado_lph",
                    "desviacion_pct", "tendencia", "anomalia"],
    "pares": ["maquina_id", "score_desvio", "variable_principal", "comparado_con"],
    "causa_comun": ["grupo_id", "zona", "ts_inicio", "ts_fin", "maquina_ids", "variable_comun",
                    "hipotesis", "centro_lat", "centro_lon", "radio_m"],
    "pesos_modelo": ["modelo", "version", "horizonte_dias", "variables", "intercepto", "pesos",
                     "medias", "desviaciones", "umbral_sugerido", "metricas"],
}
TIPOS_SCORES = set(REQ)


def _k(v):
    if isinstance(v, Decimal) and v == v.to_integral_value():
        return str(int(v))
    return str(v)


CLAVE = {
    "prob_falla": lambda r: _k(r["maquina_id"]) + "#" + _k(r["horizonte_dias"]),
    "pron_repuestos": lambda r: _k(r["zona"]) + "#" + _k(r["componente"]) + "#" + _k(r["fecha"]),
    "preventivo": lambda r: _k(r["maquina_id"]),
    "flota": lambda r: _k(r["maquina_id"]) + "#" + _k(r["periodo"]),
    "combustible": lambda r: _k(r["maquina_id"]) + "#" + _k(r["fecha"]),
    "pares": lambda r: _k(r["maquina_id"]),
    "causa_comun": lambda r: _k(r["grupo_id"]),
    "pesos_modelo": lambda r: _k(r["modelo"]) + "#" + _k(r["horizonte_dias"]),
}


def validar_score(tipo, r):
    if not isinstance(r, dict):
        return "no es un objeto"
    for c in REQ[tipo]:
        if r.get(c) is None:
            return "falta el campo " + c
    if "maquina_id" in r and not RE_MAQ.match(str(r["maquina_id"])):
        return "maquina_id inválido"
    if "zona" in r and r["zona"] not in ZONAS:
        return "zona inválida"
    if tipo == "prob_falla":
        if not es_num(r["prob_falla"]) or not (0 <= r["prob_falla"] <= 1):
            return "prob_falla debe estar entre 0 y 1"
    if tipo == "pesos_modelo":
        vs = r["variables"]
        if not isinstance(vs, list) or not vs:
            return "variables debe ser una lista"
        for d in ("pesos", "medias", "desviaciones"):
            if not isinstance(r[d], dict) or any(v not in r[d] for v in vs):
                return d + " debe tener todas las variables"
        if any((not es_num(r["desviaciones"][v])) or r["desviaciones"][v] == 0 for v in vs):
            return "desviaciones no puede tener ceros"
    return None


def post_scores(ev, m):
    b = cuerpo_json(ev)
    if not isinstance(b, dict) or b.get("tipo") not in TIPOS_SCORES:
        return err400("tipo inválido", "valores válidos: " + ", ".join(sorted(TIPOS_SCORES)))
    tipo = b["tipo"]
    if not ts_valido(b.get("generado_ts")):
        return err400("generado_ts inválido", "ISO 8601 UTC, p. ej. 2026-11-15T14:30:00Z")
    if not isinstance(b.get("version_modelo"), str) or not b["version_modelo"]:
        return err400("version_modelo inválida", "debe ser un string, p. ej. logit-v1")
    registros = b.get("registros")
    if not isinstance(registros, list):
        return err400("registros inválidos", "registros debe ser una lista")
    errores = []
    for i, r in enumerate(registros):
        motivo = validar_score(tipo, r)
        if motivo:
            errores.append("registro " + str(i) + ": " + motivo)
    if errores:
        return err400("registros inválidos", "; ".join(errores[:5]))
    items = {}
    for r in registros:
        items[CLAVE[tipo](r)] = r  # si se repite la clave en el lote, gana el último
    with tabla("scores").batch_writer() as bw:
        for clave, r in items.items():
            bw.put_item(Item={"tipo": tipo, "clave": clave, "generado_ts": b["generado_ts"],
                              "version_modelo": b["version_modelo"], "datos": r})
    return resp(200, {"recibidos": len(registros)})


def get_scores(ev, m):
    tipo = m.group(1)
    if tipo not in TIPOS_SCORES:
        return err400("tipo inválido", "valores válidos: " + ", ".join(sorted(TIPOS_SCORES)))
    kw = {"KeyConditionExpression": Key("tipo").eq(tipo)}
    items = []
    while True:
        r = tabla("scores").query(**kw)
        items += r["Items"]
        if "LastEvaluatedKey" not in r or len(items) >= 2000:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    gen = max((i["generado_ts"] for i in items), default=None)
    return resp(200, {"tipo": tipo, "cantidad": len(items), "generado_ts": gen,
                      "registros": [i["datos"] for i in items]})


# ---------------------------------------------------------------- salud de la ingesta
def get_salud(ev, m):
    try:
        sqs = cliente("sqs")
        a = sqs.get_queue_attributes(QueueUrl=QUEUE_URL, AttributeNames=[
            "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        d = sqs.get_queue_attributes(QueueUrl=QUEUE_URL + "-dlq", AttributeNames=[
            "ApproximateNumberOfMessages"])["Attributes"]
        cola = {"pendientes": int(a["ApproximateNumberOfMessages"]),
                "en_proceso": int(a["ApproximateNumberOfMessagesNotVisible"]),
                "dlq": int(d["ApproximateNumberOfMessages"])}
    except Exception as ex:
        cola = {"error": type(ex).__name__}
    return resp(200, {"cola": cola, "fase": 2})


# ---------------------------------------------------------------- endpoints todavía con ejemplos (Fase 1)
def fijo(nombre):
    return lambda ev, m: resp(200, cargar(nombre))


def get_maquina(ev, m):
    d = cargar("maquina_detalle.json")
    d["maquina_id"] = m.group(1)
    return resp(200, d)


def get_incidentes(ev, m):
    d = cargar("incidentes.json")
    estado = query(ev).get("estado")
    if estado:
        d["incidentes"] = [i for i in d["incidentes"] if i["estado"] == estado]
    return resp(200, d)


def get_incidente(ev, m):
    d = cargar("incidente_detalle.json")
    d["incidente_id"] = m.group(1)
    return resp(200, d)


def get_notificaciones(ev, m):
    d = cargar("notificaciones.json")
    rol = query(ev).get("rol")
    if rol:
        d["notificaciones"] = [n for n in d["notificaciones"] if n["rol"] == rol]
    return resp(200, d)


def post_cierre(ev, m):
    b = cuerpo_json(ev)
    if b is None:
        return err400("JSON inválido", "el cuerpo no es JSON")
    return resp(200, {"incidente_id": m.group(1), "estado": "cerrado"})


RUTAS = [
    ("GET", r"^/flota$", fijo("flota.json")),
    ("GET", r"^/maquinas/([^/]+)$", get_maquina),
    ("GET", r"^/telemetria/([^/]+)$", get_historial),
    ("GET", r"^/incidentes$", get_incidentes),
    ("GET", r"^/incidentes/([^/]+)$", get_incidente),
    ("POST", r"^/incidentes/([^/]+)/cierre$", post_cierre),
    ("GET", r"^/tecnicos$", fijo("tecnicos.json")),
    ("GET", r"^/repuestos$", fijo("repuestos.json")),
    ("GET", r"^/preventivo$", fijo("preventivo.json")),
    ("GET", r"^/kpis$", fijo("kpis.json")),
    ("GET", r"^/notificaciones$", get_notificaciones),
    ("GET", r"^/salud$", get_salud),
    ("GET", r"^/scores/([^/]+)$", get_scores),
    ("POST", r"^/ingesta$", post_ingesta),
    ("POST", r"^/scores$", post_scores),
]


def handler(event, context):
    metodo = event["httpMethod"]
    ruta = event["path"].rstrip("/") or "/"
    if metodo == "OPTIONS":
        return resp(200, {})
    cab = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    clave = api_key()
    if not clave or not hmac.compare_digest(str(cab.get("x-api-key", "")).encode("utf-8"),
                                            clave.encode("utf-8")):
        return resp(401, {"error": "api key inválida"})
    for met, patron, fn in RUTAS:
        if met == metodo:
            m = re.match(patron, ruta)
            if m:
                try:
                    return fn(event, m)
                except Exception as ex:
                    print("ERROR", metodo, ruta, repr(ex))
                    return resp(500, {"error": "error interno", "detalle": type(ex).__name__})
    return resp(404, {"error": "ruta no existe", "ruta": ruta})

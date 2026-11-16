import hmac
import json
import math
import os
import re
import urllib.request
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

# ---------------------------------------------------------------- configuración
PREFIJO = os.environ.get("PREFIJO", "arquifusion")
QUEUE_URL = os.environ.get("QUEUE_URL", "")
API_KEY_ENV = os.environ.get("API_KEY", "")  # respaldo si Secrets Manager no responde
SECRET_API_KEY = os.environ.get("SECRET_API_KEY", PREFIJO + "/api-key")
IA_URL = os.environ.get("IA_URL", "")      # capa de IA en Cloud Run; vacio = solo plantilla
IA_TIMEOUT = float(os.environ.get("IA_TIMEOUT", "25"))
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
        return True
    except ClientError as ex:
        if ex.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return False


def guardar_lote(registros):
    t = tabla("telemetria")
    ttl_s = int(float(param("ttl_dias", "30")) * 86400)
    ahora = int(time.time())
    ultimos = {}
    nuevos = []
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
            nuevos.append(r)
        except ClientError as ex:
            if ex.response["Error"]["Code"] == "ConditionalCheckFailedException":
                dup += 1
            else:
                raise
    for r in ultimos.values():
        if actualizar_maquina(r):  # solo si es la lectura más nueva de la máquina
            try:
                evaluar_maquina(r["maquina_id"], r["ts"])
            except Exception as ex:  # la detección nunca debe hacer fallar la ingesta
                print("ERROR detección", r["maquina_id"], repr(ex))
    exportar_encolar("telemetria", nuevos)
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
        malas = [str(v) for v in vs if not isinstance(v, str) or not RE_VAR.match(v)]
        if malas:  # una variable que no sé calcular cambiaría la probabilidad en silencio: se rechaza
            return ("variables no reconocidas: " + ", ".join(malas) + ". Formato: <base>[_<estadistica>][_<N>h]; "
                    "base = " + ", ".join(sorted(BASES)) + "; estadistica = ult, media, max, min, std, pend, rango")
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
    if len(registros) > 500:
        return err400("demasiados registros", "máximo 500 por llamada; recibidos " + str(len(registros)))
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
    exp = None
    url = os.environ.get("EXPORT_QUEUE_URL", "")
    if url:
        try:
            sqs = cliente("sqs")
            a = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=[
                "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
            d = sqs.get_queue_attributes(QueueUrl=url + "-dlq", AttributeNames=[
                "ApproximateNumberOfMessages"])["Attributes"]
            exp = {"pendientes": int(a["ApproximateNumberOfMessages"]),
                   "en_proceso": int(a["ApproximateNumberOfMessagesNotVisible"]),
                   "dlq": int(d["ApproximateNumberOfMessages"]),
                   "puente_gcp": "configurado" if puente_gcp() else "pendiente"}
        except Exception as ex:
            exp = {"error": type(ex).__name__}
    return resp(200, {"cola": cola, "exportacion": exp, "fase": 8})


def fijo(nombre):
    return lambda ev, m: resp(200, cargar(nombre))


# ================================================================= FASE 3: detección e incidentes
FMT = "%Y-%m-%dT%H:%M:%SZ"
ZONAS_INFO = {
    "Z1": ("Tajo Norte", -15.480, -71.520), "Z2": ("Tajo Sur", -15.520, -71.520),
    "Z3": ("Botadero", -15.500, -71.480), "Z4": ("Planta", -15.500, -71.560),
    "Z5": ("Taller", -15.505, -71.535),
}
COMPONENTES = {
    "motor": "Motor", "sistema_hidraulico": "Sistema hidráulico", "transmision": "Transmisión",
    "frenos": "Frenos", "tren_rodaje": "Tren de rodaje", "refrigeracion": "Refrigeración",
    "inyeccion_combustible": "Inyección de combustible", "filtros": "Filtros",
}
# heurística: variable que más se desvía -> componente probable
MAP_COMP = {"temp": "refrigeracion", "vib": "motor", "presion": "sistema_hidraulico",
            "combustible_lph": "inyeccion_combustible", "rpm": "motor", "horas_op": "motor",
            "carga_t": "frenos", "velocidad": "tren_rodaje"}
PESOS_ASIGNACION = {"especialidad": 0.4, "cercania": 0.25, "disponibilidad": 0.2, "tiempo_estimado": 0.15}
ROLES_SEV = {"critica": ["operador", "supervisor", "gerente", "tecnico"],
             "alta": ["operador", "supervisor", "tecnico"],
             "media": ["supervisor", "tecnico"], "baja": ["supervisor"]}
ORDEN_SEV = {"critica": 0, "alta": 1, "media": 2, "baja": 3}
BASES = sorted([k for k in LIMITES if k not in ("lat", "lon")], key=len, reverse=True)
RE_VAR = re.compile(r"^(" + "|".join(BASES) + r")(?:_(media|promedio|max|min|std|pend|pendiente|ult|rango))?(?:_(\d+)h)?$")


def a_dec(o):
    if isinstance(o, float):
        return Decimal(str(round(o, 6))) if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: a_dec(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [a_dec(v) for v in o]
    return o


def pf(nombre, defecto):
    try:
        return float(param(nombre, str(defecto)))
    except (TypeError, ValueError):
        return float(defecto)


def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def json_seguro(o):
    return json.loads(json.dumps(o, default=_dec))


# ---------------------------------------------------------------- variables del modelo
def _pendiente(puntos):
    n = len(puntos)
    if n < 2:
        return 0.0
    mx = sum(x for x, _ in puntos) / n
    my = sum(y for _, y in puntos) / n
    den = sum((x - mx) ** 2 for x, _ in puntos)
    return 0.0 if den == 0 else sum((x - mx) * (y - my) for x, y in puntos) / den


def calcular_variable(nombre, lecturas, ventana_h):
    """Ej.: temp, temp_pend, vib_max_24h, combustible_lph_media. None si no se reconoce el nombre."""
    m = RE_VAR.match(nombre)
    if not m:
        return None
    base, stat, h = m.groups()
    stat = {"promedio": "media", "pendiente": "pend"}.get(stat, stat or "ult")
    fin = a_epoch(lecturas[-1]["ts"])
    ini = fin - (int(h) if h else int(ventana_h)) * 3600
    ven = [l for l in lecturas if a_epoch(l["ts"]) >= ini]
    vals = [float(l[base]) for l in ven]
    if stat == "ult":
        return vals[-1]
    if stat == "media":
        return sum(vals) / len(vals)
    if stat == "max":
        return max(vals)
    if stat == "min":
        return min(vals)
    if stat == "rango":
        return max(vals) - min(vals)
    if stat == "std":
        if len(vals) < 2:
            return 0.0
        mu = sum(vals) / len(vals)
        return math.sqrt(sum((v - mu) ** 2 for v in vals) / (len(vals) - 1))
    if stat == "pend":  # unidades por hora
        return _pendiente([((a_epoch(l["ts"]) - ini) / 3600.0, float(l[base])) for l in ven])
    return None


def prob_logit(modelo, lecturas):
    ventana = int(pf("ventana_horas", 24))
    logit = float(modelo["intercepto"])
    aportes, valores, faltantes = {}, {}, []
    for v in modelo["variables"]:
        x = calcular_variable(v, lecturas, ventana)
        if x is None:
            faltantes.append(v)
            continue
        z = (x - float(modelo["medias"][v])) / float(modelo["desviaciones"][v])
        a = float(modelo["pesos"][v]) * z
        logit += a
        aportes[v] = a
        valores[v] = round(x, 4)
    prob = 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, logit))))
    orden = sorted(aportes, key=lambda k: aportes[k], reverse=True)
    bases = []
    for v in orden:
        if aportes[v] <= 0:
            break
        b = RE_VAR.match(v).group(1)
        if b not in bases:
            bases.append(b)
    return prob, {"variables": bases[:3], "valores": valores, "faltantes": faltantes}


def riesgo_reglas(l):
    """Respaldo mientras B no envía pesos_modelo: exceso sobre rangos normales (0 a 1)."""
    e = {"temp": (float(l["temp"]) - 85) / 20, "vib": (float(l["vib"]) - 4) / 6,
         "combustible_lph": (float(l["combustible_lph"]) - 40) / 30}
    e = {k: max(0.0, min(1.0, v)) for k, v in e.items()}
    orden = sorted(e, key=lambda k: e[k], reverse=True)
    return max(e.values()), {"variables": [k for k in orden if e[k] > 0.3][:3] or orden[:1],
                             "valores": {k: round(v, 3) for k, v in e.items()}, "faltantes": []}


def modelo_activo():
    def leer():
        items = tabla("scores").query(KeyConditionExpression=Key("tipo").eq("pesos_modelo"))["Items"]
        if not items:
            return None
        pref = param("modelo_falla", "")
        ok = [i for i in items if i["datos"].get("modelo") == pref] or items
        ok.sort(key=lambda i: i["generado_ts"])
        return ok[-1]["datos"]
    return _cacheado("modelo", 60, leer)


def severidad_de(prob, estado):
    sc, sa, sm = pf("sev_critica", 0.9), pf("sev_alta", 0.75), pf("sev_media", 0.6)
    s = "critica" if prob >= sc else "alta" if prob >= sa else "media" if prob >= sm else "baja"
    if estado == "requiere_intervencion" and ORDEN_SEV[s] > ORDEN_SEV["alta"]:
        s = "alta"
    return s


# ---------------------------------------------------------------- detección en línea
def calcular_riesgo(maq_id, ts_hasta=None):
    """Riesgo de la máquina usando solo lecturas hasta ts_hasta (sin efectos). None si no hay datos."""
    n = int(pf("n_lecturas", 336))
    cond = Key("maquina_id").eq(maq_id)
    if ts_hasta:
        cond = cond & Key("ts").lte(ts_hasta)
    items = tabla("telemetria").query(KeyConditionExpression=cond, ScanIndexForward=False,
                                      Limit=n, ConsistentRead=True)["Items"]
    lecturas = list(reversed(items))
    if not lecturas:
        return None
    actual = lecturas[-1]
    modelo = modelo_activo()
    if modelo:
        prob, evid = prob_logit(modelo, lecturas)
        metodo = "logit:" + str(modelo.get("modelo"))
        ov = param("umbral_override", "auto")
        try:
            umbral = float(ov) if ov != "auto" else float(modelo.get("umbral_sugerido", 0.5))
        except ValueError:
            umbral = float(modelo.get("umbral_sugerido", 0.5))
    else:
        prob, evid = riesgo_reglas(actual)
        metodo = "reglas"
        umbral = pf("umbral_reglas", 0.6)
    return {"prob": prob, "evid": evid, "metodo": metodo, "umbral": umbral, "actual": actual,
            "lecturas": len(lecturas)}


def evaluar_maquina(maq_id, ts_hasta=None):
    if param("deteccion_activa", "true") != "true":
        return None
    r = calcular_riesgo(maq_id, ts_hasta)
    if not r:
        return None
    actual, prob, umbral = r["actual"], r["prob"], r["umbral"]
    tabla("maquinas").update_item(
        Key={"maquina_id": maq_id}, UpdateExpression="SET riesgo = :r, riesgo_ts = :t, metodo_riesgo = :m",
        ExpressionAttributeValues=a_dec({":r": prob, ":t": actual["ts"], ":m": r["metodo"]}))
    if prob < umbral and actual["estado"] != "requiere_intervencion":
        return None
    return abrir_incidente(maq_id, actual, prob, umbral, r["metodo"], r["evid"])


def siguiente_id(ts):
    anio = ts[:4]
    r = tabla("incidentes").update_item(
        Key={"incidente_id": "CONTADOR#" + anio}, UpdateExpression="ADD n :u",
        ExpressionAttributeValues={":u": 1}, ReturnValues="UPDATED_NEW")
    return "INC-%s-%04d" % (anio, int(r["Attributes"]["n"]))


def abrir_incidente(maq_id, lectura, prob, umbral, metodo, evid, severidad=None, forzar=False):
    ts = lectura["ts"]
    limite = datetime.fromtimestamp(a_epoch(ts) - pf("cooldown_horas", 6) * 3600, timezone.utc).strftime(FMT)
    # revisión barata antes de gastar un número de incidente
    previa = tabla("maquinas").get_item(Key={"maquina_id": maq_id}, ConsistentRead=True).get("Item") or {}
    if previa.get("incidente_abierto"):
        return None
    if not forzar and previa.get("ultimo_cierre_ts") and previa["ultimo_cierre_ts"] >= limite:
        return None
    inc_id = siguiente_id(ts)
    cond = "attribute_not_exists(incidente_abierto)"
    vals = {":i": inc_id}
    if not forzar:  # el período de enfriamiento evita reabrir justo después de un cierre
        cond += " AND (attribute_not_exists(ultimo_cierre_ts) OR ultimo_cierre_ts < :lim)"
        vals[":lim"] = limite
    try:
        tabla("maquinas").update_item(Key={"maquina_id": maq_id}, UpdateExpression="SET incidente_abierto = :i",
                                      ConditionExpression=cond, ExpressionAttributeValues=vals)
    except ClientError as ex:
        if ex.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    try:
        sev = severidad or severidad_de(prob, lectura["estado"])
        vars_ = evid.get("variables") or ["temp"]
        comp = evid.get("componente") or MAP_COMP.get(vars_[0], "motor")
        previos = tabla("incidentes").query(IndexName="maquina-index", Limit=1,
                                            KeyConditionExpression=Key("maquina_id").eq(maq_id))["Items"]
        accion = {"critica": "Detener el equipo y revisar ", "alta": "Programar inspección urgente de ",
                  "media": "Inspeccionar en la próxima parada: ", "baja": "Monitorear y revisar en el próximo mantenimiento: "}[sev]
        item = a_dec({
            "incidente_id": inc_id, "maquina_id": maq_id, "zona": lectura["zona"], "severidad": sev,
            "estado": "abierto", "fase": "detectado", "abierto_ts": ts, "inicio": ts, "fin": None,
            "variables_desvio": vars_,
            "causa_probable": "Desviación en " + ", ".join(vars_) + "; componente probable: " + COMPONENTES.get(comp, comp),
            "accion_recomendada": accion + COMPONENTES.get(comp, comp).lower(),
            "componente_probable": comp, "tecnico_id": None, "eta_min": None,
            "justificacion_asignacion": None, "repuestos_reservados": [],
            "reincidente": bool(previos), "prob_falla": prob, "umbral": umbral, "metodo": metodo,
            "valores": evid.get("valores", {}), "variables_faltantes": evid.get("faltantes", []),
            "modelo": metodo, "en_cola": False, "componentes_reemplazados": [], "intervencion": None,
            "tiempo_detenido_min": None,
            "eventos": [{"fase": "detectado", "estado": "abierto", "ts": ts}],
        })
        tabla("incidentes").put_item(Item=item)
    except Exception:
        tabla("maquinas").update_item(Key={"maquina_id": maq_id}, UpdateExpression="REMOVE incidente_abierto")
        raise
    try:
        correlacionar(item)
    except Exception as ex:
        print("AVISO: correlación falló:", repr(ex))
    iniciar_flujo(item)
    return item


def iniciar_flujo(item):
    arn = os.environ.get("STATE_MACHINE_ARN", "")
    if not arn:
        print("AVISO: falta STATE_MACHINE_ARN, el incidente", item["incidente_id"], "queda sin flujo")
        return False
    entrada = {"incidente_id": item["incidente_id"], "maquina_id": item["maquina_id"],
               "severidad": item["severidad"], "timeout_cierre_seg": int(pf("timeout_cierre_seg", 3600))}
    try:
        cliente("stepfunctions").start_execution(stateMachineArn=arn, name=item["incidente_id"] + "-" + str(int(time.time())),
                                                 input=json.dumps(entrada))
        return True
    except ClientError as ex:
        if ex.response["Error"]["Code"] == "ExecutionAlreadyExists":
            return True
        print("ERROR iniciando flujo:", repr(ex))
    except Exception as ex:
        print("ERROR iniciando flujo:", repr(ex))
    tabla("incidentes").update_item(Key={"incidente_id": item["incidente_id"]},
                                    UpdateExpression="SET flujo_error = :t", ExpressionAttributeValues={":t": True})
    return False


# ---------------------------------------------------------------- helpers de incidentes
def get_inc(inc_id):
    return tabla("incidentes").get_item(Key={"incidente_id": inc_id}, ConsistentRead=True).get("Item")


def evento(inc_id, fase, estado):
    tabla("incidentes").update_item(
        Key={"incidente_id": inc_id},
        UpdateExpression="SET #f = :f, #e = :e, eventos = list_append(if_not_exists(eventos, :v), :ev)",
        ExpressionAttributeNames={"#f": "fase", "#e": "estado"},
        ExpressionAttributeValues={":f": fase, ":e": estado, ":v": [],
                                   ":ev": [{"fase": fase, "estado": estado, "ts": ahora_iso()}]})


# ---------------------------------------------------------------- acciones de la Step Function (Lambda "flujo")
def accion_explicar(inc, ev):
    vars_ = ", ".join(inc.get("variables_desvio") or [])
    comp = COMPONENTES.get(inc.get("componente_probable"), inc.get("componente_probable"))
    prob = float(inc.get("prob_falla", 0))
    base = ("%s presenta desvío en %s. Probabilidad de falla estimada: %d%% (umbral %d%%). "
            "Componente probable: %s.") % (inc["maquina_id"], vars_ or "sus variables", round(prob * 100),
                                          round(float(inc.get("umbral", 0)) * 100), comp)
    texto, fuente = base, "plantilla"
    modelo_id = os.environ.get("BEDROCK_MODEL_ID", "")
    if modelo_id:
        try:
            pedido = ("Eres asistente de mantenimiento de maquinaria pesada minera. Explica en 2 frases, en español, "
                      "para un supervisor, por qué se abrió este incidente y qué revisar. No cambies la severidad ni "
                      "la asignación. Datos: " + json.dumps(json_seguro({
                          "maquina": inc["maquina_id"], "zona": inc["zona"], "severidad": inc["severidad"],
                          "prob_falla": prob, "variables_en_desvio": inc.get("variables_desvio"),
                          "valores": inc.get("valores"), "componente_probable": comp}), ensure_ascii=False))
            r = cliente("bedrock-runtime").converse(
                modelId=modelo_id, messages=[{"role": "user", "content": [{"text": pedido}]}],
                inferenceConfig={"maxTokens": 250, "temperature": 0.2})
            t = r["output"]["message"]["content"][0]["text"].strip()
            if t:
                texto, fuente = t, "bedrock"
        except Exception as ex:
            print("Bedrock no disponible, uso plantilla:", type(ex).__name__)
    if fuente == "plantilla" and IA_URL:
        # Bedrock no existe en el Learner Lab: la explicacion sale de /ia/explicar (Gemini en Cloud Run).
        # Sobrescribe causa_probable y accion_recomendada, que son los campos que lee notificar().
        try:
            pedido = json.dumps(json_seguro({
                "incidente_id": inc["incidente_id"], "maquina_id": inc["maquina_id"], "ts": ahora_iso(),
                "severidad": inc["severidad"], "zona": inc["zona"],
                "desviaciones": inc.get("valores") or {},
                "repuesto_reservado": inc.get("componente_probable"),
                "tecnico_asignado": inc.get("tecnico_id")}), ensure_ascii=False).encode("utf-8")
            pet = urllib.request.Request(IA_URL, data=pedido, method="POST")
            pet.add_header("Content-Type", "application/json")
            pet.add_header("x-api-key", api_key())
            with urllib.request.urlopen(pet, timeout=IA_TIMEOUT) as r:
                d = json.loads(r.read().decode("utf-8"))
            causa, accion = d["causa_probable"], d["accion_recomendada"]
            tabla("incidentes").update_item(
                Key={"incidente_id": inc["incidente_id"]},
                UpdateExpression="SET causa_probable = :c, accion_recomendada = :a",
                ExpressionAttributeValues={":c": causa, ":a": accion})
            texto, fuente = causa + " " + accion, "gemini"
        except Exception as ex:
            print("IA no disponible, uso plantilla:", type(ex).__name__)
    tabla("incidentes").update_item(Key={"incidente_id": inc["incidente_id"]},
                                    UpdateExpression="SET explicacion = :e",
                                    ExpressionAttributeValues={":e": {"texto": texto, "fuente": fuente}})
    return {"fuente": fuente}


def accion_reservar(inc, ev):
    comp, zona = inc["componente_probable"], inc["zona"]
    _, lat, lon = ZONAS_INFO.get(zona, ("", 0, 0))
    orden = sorted(ZONAS_INFO, key=lambda z: km(lat, lon, ZONAS_INFO[z][1], ZONAS_INFO[z][2]))
    reservado = None
    for z in orden:
        try:
            tabla("repuestos_stock").update_item(
                Key={"zona": z, "componente": comp},
                UpdateExpression="SET #s = #s - :u, reservado = if_not_exists(reservado, :c) + :u",
                ConditionExpression="attribute_exists(#s) AND #s >= :u",
                ExpressionAttributeNames={"#s": "stock"}, ExpressionAttributeValues={":u": 1, ":c": 0})
            reservado = {"componente": comp, "cantidad": 1, "zona": z}
            break
        except ClientError as ex:
            if ex.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    tabla("incidentes").update_item(
        Key={"incidente_id": inc["incidente_id"]},
        UpdateExpression="SET repuestos_reservados = :r, faltante_repuesto = :f",
        ExpressionAttributeValues={":r": [reservado] if reservado else [], ":f": reservado is None})
    return {"reservado": reservado is not None}


def accion_asignar(inc, ev):
    comp = inc["componente_probable"]
    m = tabla("maquinas").get_item(Key={"maquina_id": inc["maquina_id"]}).get("Item") or {}
    lat = float(m.get("lat", ZONAS_INFO.get(inc["zona"], ("", 0, 0))[1]))
    lon = float(m.get("lon", ZONAS_INFO.get(inc["zona"], ("", 0, 0))[2]))
    try:
        w = {**PESOS_ASIGNACION, **json.loads(param("pesos_asignacion", "{}"))}
    except ValueError:
        w = PESOS_ASIGNACION
    tecs, kw = [], {}
    while True:
        r = tabla("tecnicos").scan(**kw)
        tecs += r["Items"]
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    cands = []
    for t in tecs:
        if t.get("disponibilidad") != "disponible":
            continue
        d = km(float(t["lat"]), float(t["lon"]), lat, lon)
        eta = round(5 + d * 2)
        j = {"especialidad": 1.0 if comp in (t.get("especialidades") or []) else 0.0,
             "cercania": round(1 / (1 + d / 2), 2), "disponibilidad": 1.0,
             "tiempo_estimado": round(1 / (1 + eta / 30), 2)}
        j["score"] = round(sum(w[k] * j[k] for k in w), 2)
        cands.append((j["score"], t, j, eta))
    cands.sort(key=lambda c: c[0], reverse=True)
    for score, t, j, eta in cands:
        try:
            tabla("tecnicos").update_item(
                Key={"tecnico_id": t["tecnico_id"]},
                UpdateExpression="SET disponibilidad = :a, incidente_actual = :i",
                ConditionExpression="disponibilidad = :d",
                ExpressionAttributeValues={":a": "asignado", ":i": inc["incidente_id"], ":d": "disponible"})
        except ClientError as ex:
            if ex.response["Error"]["Code"] == "ConditionalCheckFailedException":
                continue  # otro incidente se lo llevó primero
            raise
        tabla("incidentes").update_item(
            Key={"incidente_id": inc["incidente_id"]},
            UpdateExpression="SET tecnico_id = :t, eta_min = :e, justificacion_asignacion = :j, en_cola = :c",
            ExpressionAttributeValues=a_dec({":t": t["tecnico_id"], ":e": eta, ":j": j, ":c": False}))
        evento(inc["incidente_id"], "asignado", "en_proceso")
        return {"tecnico_id": t["tecnico_id"], "eta_min": eta, "score": score}
    tabla("incidentes").update_item(Key={"incidente_id": inc["incidente_id"]},
                                    UpdateExpression="SET en_cola = :c", ExpressionAttributeValues={":c": True})
    return {"tecnico_id": None, "en_cola": True}


def notificar(inc, roles=None, titulo_extra=""):
    sev, maq = inc["severidad"], inc["maquina_id"]
    zona = ZONAS_INFO.get(inc["zona"], (inc["zona"],))[0]
    rep = ", ".join("%s (%s)" % (r["componente"], r["zona"]) for r in inc.get("repuestos_reservados") or [])
    tec = inc.get("tecnico_id")
    resumen = "%s. %s." % (inc.get("causa_probable", ""), inc.get("accion_recomendada", ""))
    msgs = {
        "operador": ("Detener " + maq if sev in ("critica", "alta") else "Revisión de " + maq,
                     {"critica": "Detenga el equipo y espere al técnico.",
                      "alta": "Reduzca la carga y espere la inspección.",
                      "media": "Continúe con precaución; se programará una inspección.",
                      "baja": "Sin acción inmediata."}[sev]),
        "supervisor": ("Incidente %s en %s" % (sev, maq),
                       resumen + (" Técnico: %s (ETA %s min)." % (tec, inc.get("eta_min")) if tec
                                  else " Sin técnico disponible: el incidente queda en cola.") +
                       (" Repuesto reservado: " + rep + "." if rep else
                        (" Sin stock del repuesto." if inc.get("faltante_repuesto") else ""))),
        "tecnico": ("Asignado a " + maq,
                    resumen + " Zona %s." % inc["zona"] + (" Repuesto reservado: " + rep + "." if rep else "")),
        "gerente": ("Incidente %s en %s" % (sev, zona),
                    "Equipo %s con riesgo %d%%. %s" % (maq, round(float(inc.get("prob_falla", 0)) * 100),
                                                       "Técnico asignado." if tec else "Sin técnico disponible.")),
    }
    enviados = []
    for rol in roles or ROLES_SEV[sev]:
        if rol == "tecnico" and not tec:
            continue
        titulo, mensaje = msgs[rol]
        ts = ahora_iso()
        tabla("notificaciones").put_item(Item={
            "rol": rol, "ts_id": ts + "#" + inc["incidente_id"] + "#" + uuid.uuid4().hex[:6], "ts": ts,
            "severidad": sev, "incidente_id": inc["incidente_id"], "titulo": titulo_extra + titulo,
            "mensaje": mensaje})
        enviados.append(rol)
        topic = os.environ.get("SNS_TOPIC_ARN", "")
        if topic:
            try:
                cliente("sns").publish(TopicArn=topic, Subject=(titulo_extra + titulo)[:100], Message=mensaje,
                                       MessageAttributes={"rol": {"DataType": "String", "StringValue": rol}})
            except Exception as ex:
                print("SNS no disponible:", type(ex).__name__)
    return enviados


def accion_notificar(inc, ev):
    return {"notificados": notificar(inc)}


def accion_registrar_token(inc, ev):
    token = ev["token"]
    if inc["estado"] == "cerrado":  # se cerró antes de llegar a la espera: reanudar de inmediato
        enviar_exito(token, "ya_cerrado")
        return {"reanudado": True}
    tabla("incidentes").update_item(Key={"incidente_id": inc["incidente_id"]},
                                    UpdateExpression="SET task_token = :t", ExpressionAttributeValues={":t": token})
    return {"esperando": True}


def accion_escalar(inc, ev):
    tabla("incidentes").update_item(Key={"incidente_id": inc["incidente_id"]},
                                    UpdateExpression="SET escalado = :t", ExpressionAttributeValues={":t": True})
    return {"notificados": notificar(inc, roles=["supervisor", "gerente"], titulo_extra="ESCALADO: ")}


def accion_cerrar(inc, ev):
    return {"cerrado": cerrar_incidente(inc["incidente_id"], {}, ev.get("motivo", "confirmado"))}


def enviar_exito(token, motivo):
    try:
        cliente("stepfunctions").send_task_success(taskToken=token, output=json.dumps({"cierre": motivo}))
    except Exception as ex:
        print("send_task_success:", type(ex).__name__)  # token vencido o ya usado: no es un error


ACCIONES = {"explicar": accion_explicar, "reservar_repuestos": accion_reservar, "asignar": accion_asignar,
            "notificar": accion_notificar, "registrar_token": accion_registrar_token,
            "escalar": accion_escalar, "cerrar": accion_cerrar}


def flujo(event, context):
    """Lambda que ejecuta cada paso de la Step Function de incidentes."""
    if event.get("accion") in ACCIONES_NOCTURNO:
        return json_seguro(ACCIONES_NOCTURNO[event["accion"]](event))
    fn = ACCIONES.get(event.get("accion"))
    if not fn:
        raise ValueError("accion desconocida: " + str(event.get("accion")))
    inc = get_inc(event["incidente_id"])
    if not inc:
        raise ValueError("incidente no existe: " + str(event["incidente_id"]))
    return json_seguro(fn(inc, event))


# ---------------------------------------------------------------- cierre de incidentes
def cerrar_incidente(inc_id, datos, motivo):
    """Idempotente: devuelve False si ya estaba cerrado."""
    ahora = ahora_iso()
    try:
        r = tabla("incidentes").update_item(
            Key={"incidente_id": inc_id},
            UpdateExpression=("SET #e = :c, #f = :r, fin = :t, intervencion = :i, componentes_reemplazados = :cr, "
                              "repuestos_usados = :ru, motivo_cierre = :m, "
                              "eventos = list_append(if_not_exists(eventos, :v), :ev)"),
            ConditionExpression="attribute_exists(incidente_id) AND #e <> :c",
            ExpressionAttributeNames={"#e": "estado", "#f": "fase"},
            ExpressionAttributeValues=a_dec({
                ":c": "cerrado", ":r": "resuelto", ":t": ahora, ":i": datos.get("intervencion"),
                ":cr": datos.get("componentes_reemplazados") or [], ":ru": datos.get("repuestos_usados") or [],
                ":m": motivo, ":v": [], ":ev": [{"fase": "resuelto", "estado": "cerrado", "ts": ahora}]}),
            ReturnValues="ALL_NEW")
    except ClientError as ex:
        if ex.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    inc = r["Attributes"]
    try:
        mins = max(0, (a_epoch(ahora) - a_epoch(inc["inicio"])) // 60)
        tabla("incidentes").update_item(Key={"incidente_id": inc_id}, UpdateExpression="SET tiempo_detenido_min = :m",
                                        ExpressionAttributeValues={":m": int(mins)})
    except Exception:
        pass
    tec = inc.get("tecnico_id")
    if tec:
        try:
            tabla("tecnicos").update_item(
                Key={"tecnico_id": tec},
                UpdateExpression="SET disponibilidad = :d, incidente_actual = :n ADD atendidos_mes :u",
                ConditionExpression="incidente_actual = :i",
                ExpressionAttributeValues={":d": "disponible", ":n": None, ":i": inc_id, ":u": 1})
        except ClientError as ex:
            if ex.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    maq = tabla("maquinas").get_item(Key={"maquina_id": inc["maquina_id"]}).get("Item") or {}
    tabla("maquinas").update_item(
        Key={"maquina_id": inc["maquina_id"]},
        UpdateExpression="SET ultimo_cierre_ts = :u REMOVE incidente_abierto",
        ExpressionAttributeValues={":u": maq.get("ultimo_ts", ahora)})
    comps = datos.get("componentes_reemplazados") or []
    man = a_dec({
        "mantenimiento_id": "MAN-" + inc_id[4:], "maquina_id": inc["maquina_id"], "ts": ahora,
        "tipo_mantenimiento": "correctivo", "estado": "completado", "componentes": comps,
        "repuestos_usados": datos.get("repuestos_usados") or [], "incidente_id": inc_id,
        "intervencion": datos.get("intervencion")})
    tabla("mantenimientos").put_item(Item=man)
    exportar_encolar("incidente", [registro_incidente(inc)])
    exportar_encolar("mantenimiento", [registro_mantenimiento(man)])
    exportar_encolar("consumo_repuesto", registros_consumo(man))
    try:
        asignar_pendientes()
    except Exception as ex:
        print("asignar_pendientes:", repr(ex))
    return True


def asignar_pendientes():
    """Si hay incidentes esperando técnico, el que acaba de liberarse atiende al más urgente."""
    items = tabla("incidentes").query(IndexName="estado-index",
                                      KeyConditionExpression=Key("estado").eq("abierto"))["Items"]
    cola = sorted([i for i in items if i.get("en_cola")], key=lambda i: (ORDEN_SEV[i["severidad"]], i["abierto_ts"]))
    for inc in cola[:3]:
        res = accion_asignar(inc, {})
        if not res.get("tecnico_id"):
            break
        notificar(get_inc(inc["incidente_id"]), roles=["tecnico", "supervisor"])


# ---------------------------------------------------------------- endpoints de incidentes, técnicos, notificaciones
CAMPOS_LISTA = ["incidente_id", "maquina_id", "zona", "severidad", "estado", "fase", "inicio", "fin",
                "variables_desvio", "causa_probable", "accion_recomendada", "tecnico_id", "eta_min",
                "justificacion_asignacion", "repuestos_reservados", "reincidente", "prob_falla", "en_cola",
                "grupo_causa_comun"]
CAMPOS_DETALLE = CAMPOS_LISTA + ["tiempo_detenido_min", "intervencion", "componentes_reemplazados", "eventos",
                                 "explicacion", "escalado", "motivo_cierre", "componente_probable", "umbral",
                                 "metodo", "faltante_repuesto", "grupo_causa_comun"]


def vista(item, campos):
    return {c: item.get(c) for c in campos}


def get_incidentes(ev, m):
    estado = query(ev).get("estado")
    estados = [estado] if estado else ["abierto", "en_proceso", "cerrado"]
    items = []
    for e in estados:
        items += tabla("incidentes").query(IndexName="estado-index", ScanIndexForward=False, Limit=200,
                                           KeyConditionExpression=Key("estado").eq(e))["Items"]
    items.sort(key=lambda i: i["abierto_ts"], reverse=True)
    return resp(200, {"incidentes": [vista(i, CAMPOS_LISTA) for i in items]})


def get_incidente(ev, m):
    inc = get_inc(m.group(1))
    if not inc:
        return resp(404, {"error": "incidente no existe", "incidente_id": m.group(1)})
    return resp(200, vista(inc, CAMPOS_DETALLE))


def post_cierre(ev, m):
    b = cuerpo_json(ev)
    if not isinstance(b, dict):
        return err400("JSON inválido", "el cuerpo debe ser un objeto JSON")
    inc = get_inc(m.group(1))
    if not inc:
        return resp(404, {"error": "incidente no existe", "incidente_id": m.group(1)})
    if inc["estado"] == "cerrado":
        return resp(200, {"incidente_id": inc["incidente_id"], "estado": "cerrado", "ya_estaba_cerrado": True})
    cerrar_incidente(inc["incidente_id"], b, "confirmado")
    if inc.get("task_token"):
        enviar_exito(inc["task_token"], "confirmado")
    return resp(200, {"incidente_id": inc["incidente_id"], "estado": "cerrado"})


def post_atencion(ev, m):
    inc = get_inc(m.group(1))
    if not inc:
        return resp(404, {"error": "incidente no existe", "incidente_id": m.group(1)})
    if inc["estado"] != "en_proceso":
        return err400("estado inválido", "el incidente debe estar en_proceso (técnico asignado)")
    evento(inc["incidente_id"], "en_atencion", "en_proceso")
    tec = inc.get("tecnico_id")
    if tec:
        tabla("tecnicos").update_item(Key={"tecnico_id": tec}, UpdateExpression="SET disponibilidad = :d",
                                      ExpressionAttributeValues={":d": "en_atencion"})
    return resp(200, {"incidente_id": inc["incidente_id"], "fase": "en_atencion"})


def get_tecnicos(ev, m):
    items, kw = [], {}
    while True:
        r = tabla("tecnicos").scan(**kw)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    items.sort(key=lambda t: t["tecnico_id"])
    return resp(200, {"tecnicos": items})


def get_notificaciones(ev, m):
    rol = query(ev).get("rol")
    items = []
    for r in ([rol] if rol else ["operador", "tecnico", "supervisor", "gerente"]):
        items += tabla("notificaciones").query(KeyConditionExpression=Key("rol").eq(r),
                                               ScanIndexForward=False, Limit=50)["Items"]
    items.sort(key=lambda n: n["ts"], reverse=True)
    campos = ["rol", "ts", "severidad", "incidente_id", "titulo", "mensaje"]
    return resp(200, {"notificaciones": [vista(n, campos) for n in items[:50]]})


# ---------------------------------------------------------------- administración (datos base y pruebas)
def post_semilla(ev, m):
    tecs = [
        ("TEC-01", "Carlos Quispe", ["motor", "refrigeracion"], "Z2"),
        ("TEC-02", "Lucía Mamani", ["sistema_hidraulico", "filtros"], "Z1"),
        ("TEC-03", "Jorge Huamán", ["transmision", "frenos"], "Z5"),
        ("TEC-04", "Rosa Condori", ["tren_rodaje", "inyeccion_combustible"], "Z3"),
        ("TEC-05", "Miguel Apaza", ["motor", "sistema_hidraulico", "refrigeracion"], "Z4"),
    ]
    for tid, nombre, esp, z in tecs:
        tabla("tecnicos").put_item(Item=a_dec({
            "tecnico_id": tid, "nombre": nombre, "especialidades": esp, "zona": z,
            "lat": ZONAS_INFO[z][1], "lon": ZONAS_INFO[z][2], "disponibilidad": "disponible",
            "incidente_actual": None, "atendidos_mes": 0}))
    n = 0
    for z, stock in (("Z5", 4), ("Z4", 2), ("Z1", 1), ("Z2", 1)):
        for comp, desc in COMPONENTES.items():
            tabla("repuestos_stock").put_item(Item={
                "zona": z, "componente": comp, "descripcion": desc, "stock": stock, "minimo": 1,
                "demanda_proyectada_7d": 0})
            n += 1
    return resp(200, {"tecnicos": len(tecs), "stock_filas": n})


def post_admin_incidente(ev, m):
    """Abre un incidente a mano (para demos y pruebas del flujo)."""
    b = cuerpo_json(ev)
    if not isinstance(b, dict) or not RE_MAQ.match(str(b.get("maquina_id", ""))):
        return err400("maquina_id inválido", "se espera {maquina_id, severidad?, componente?}")
    sev = b.get("severidad", "alta")
    if sev not in ORDEN_SEV:
        return err400("severidad inválida", "critica, alta, media o baja")
    maq = tabla("maquinas").get_item(Key={"maquina_id": b["maquina_id"]}).get("Item")
    if not maq:
        return resp(404, {"error": "máquina sin telemetría", "maquina_id": b["maquina_id"]})
    comp = b.get("componente")
    if comp is not None and comp not in COMPONENTES:
        return err400("componente inválido", ", ".join(COMPONENTES))
    lectura = {"ts": maq["ultimo_ts"], "zona": maq["zona"], "estado": maq.get("estado", "operativa")}
    evid = {"variables": ["temp"], "valores": {}, "faltantes": [], "componente": comp}
    prob = {"critica": 0.95, "alta": 0.8, "media": 0.65, "baja": 0.5}[sev]
    inc = abrir_incidente(b["maquina_id"], lectura, prob, 0.5, "manual", evid, severidad=sev, forzar=True)
    if not inc:
        return resp(409, {"error": "ya existe un incidente abierto para esa máquina"})
    return resp(201, {"incidente_id": inc["incidente_id"], "severidad": sev})


def get_admin_riesgo(ev, m):
    """Riesgo de una máquina en un instante, sin crear nada (para comparar con BigQuery)."""
    maq = m.group(1)
    if not RE_MAQ.match(maq):
        return err400("maquina_id inválido", "formato M-001..M-999")
    ts = query(ev).get("ts")
    if ts and not ts_valido(ts):
        return err400("ts inválido", "ISO 8601 UTC, p. ej. 2026-11-07T20:00:00Z")
    r = calcular_riesgo(maq, ts)
    if not r:
        return resp(404, {"error": "sin lecturas", "maquina_id": maq})
    return resp(200, {"maquina_id": maq, "ts": r["actual"]["ts"], "prob_falla": r["prob"], "umbral": r["umbral"],
                      "supera_umbral": r["prob"] >= r["umbral"], "metodo": r["metodo"],
                      "lecturas_usadas": r["lecturas"], "valores": r["evid"].get("valores"),
                      "variables_desvio": r["evid"].get("variables"),
                      "variables_faltantes": r["evid"].get("faltantes")})


def get_admin_resumen(ev, m):
    cuenta, kw = {}, {"ProjectionExpression": "maquina_id"}
    while True:
        r = tabla("telemetria").scan(**kw)
        for i in r["Items"]:
            cuenta[i["maquina_id"]] = cuenta.get(i["maquina_id"], 0) + 1
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    n_inc = {}
    for e in ("abierto", "en_proceso", "cerrado"):
        n_inc[e] = tabla("incidentes").query(IndexName="estado-index", Select="COUNT",
                                             KeyConditionExpression=Key("estado").eq(e))["Count"]
    return resp(200, {"telemetria_total": sum(cuenta.values()), "maquinas": len(cuenta),
                      "por_maquina": dict(sorted(cuenta.items())), "incidentes": n_inc})


CLAVES_TABLA = {"maquinas": ["maquina_id"], "incidentes": ["incidente_id"], "notificaciones": ["rol", "ts_id"],
                "mantenimientos": ["mantenimiento_id"]}


def post_admin_reiniciar(ev, m):
    """Deja en cero el estado operativo (incidentes, notificaciones, bloqueos, técnicos, stock).
    Conserva telemetría y scores: al repetir la demo con los mismos ts, la detección vuelve a disparar."""
    borrados = {}
    for nombre, claves in CLAVES_TABLA.items():
        t, kw, n = tabla(nombre), {"ProjectionExpression": ", ".join("#k%d" % i for i in range(len(claves))),
                                    "ExpressionAttributeNames": {"#k%d" % i: c for i, c in enumerate(claves)}}, 0
        while True:
            r = t.scan(**kw)
            with t.batch_writer() as bw:
                for it in r["Items"]:
                    bw.delete_item(Key={c: it[c] for c in claves})
                    n += 1
            if "LastEvaluatedKey" not in r:
                break
            kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
        borrados[nombre] = n
    sem = json.loads(post_semilla(ev, m)["body"])
    return resp(200, {"borrados": borrados, "semilla": sem})


# ================================================================= PUENTE AWS -> GCP (Fase 4)
def puente_gcp():
    """(url, token) del secreto arquifusion/puente-gcp, o None si aún dice 'pendiente'."""
    def leer():
        try:
            d = json.loads(cliente("secretsmanager").get_secret_value(SecretId=PREFIJO + "/puente-gcp")["SecretString"])
            url, tok = str(d.get("ingest_url", "")), str(d.get("ingest_token", ""))
            return (url, tok) if url.startswith("https://") and tok and tok != "pendiente" else None
        except Exception as ex:
            print("secreto puente-gcp no disponible:", type(ex).__name__)
            return None
    return _cacheado("puente", 120, leer)


def registro_incidente(inc):
    desc = ((inc.get("causa_probable") or "") + ". " + (inc.get("accion_recomendada") or "")).strip(". ")
    return {"incidente_id": inc["incidente_id"], "maquina_id": inc["maquina_id"], "ts": inc["inicio"],
            "severidad": inc["severidad"], "estado": inc["estado"], "descripcion": desc[:500]}


def registro_mantenimiento(man):
    return {"mantenimiento_id": man["mantenimiento_id"], "maquina_id": man["maquina_id"], "ts": man["ts"],
            "tipo_mantenimiento": man["tipo_mantenimiento"], "estado": man["estado"],
            "componentes": [c for c in (man.get("componentes") or []) if c in COMPONENTES]}


def registros_consumo(man):
    out = []
    for i, u in enumerate(man.get("repuestos_usados") or []):
        if isinstance(u, dict) and u.get("componente") in COMPONENTES and es_num(u.get("cantidad")) and u["cantidad"] > 0:
            out.append({"consumo_id": "CON-" + man["mantenimiento_id"][4:] + "-" + str(i + 1),
                        "maquina_id": man["maquina_id"], "ts": man["ts"], "componente": u["componente"],
                        "cantidad": u["cantidad"], "unidad": u.get("unidad") or "unidad"})
    return out


def exportar_encolar(tipo, registros):
    """Deja los registros en la cola de exportación. Nunca rompe a quien lo llama."""
    url = os.environ.get("EXPORT_QUEUE_URL", "")
    if not url or not registros:
        return 0
    try:
        sqs = cliente("sqs")
        mensajes = [registros[i:i + CHUNK] for i in range(0, len(registros), CHUNK)]
        for j in range(0, len(mensajes), 10):
            entradas = [{"Id": str(k), "MessageBody": json.dumps({"tipo": tipo, "registros": chunk}, default=_dec)}
                        for k, chunk in enumerate(mensajes[j:j + 10])]
            r = sqs.send_message_batch(QueueUrl=url, Entries=entradas)
            if r.get("Failed"):
                print("AVISO: no se encolaron", len(r["Failed"]), "mensajes de exportación")
        return len(registros)
    except Exception as ex:
        print("ERROR encolando exportación:", repr(ex))
        return 0


def post_gcp(url, token, tipo, registros):
    cuerpo = json.dumps({"tipo": tipo, "registros": registros}, default=_dec).encode("utf-8")
    req = urllib.request.Request(url, data=cuerpo, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as ex:
        raise RuntimeError("GCP respondió %s: %s" % (ex.code, ex.read()[:300].decode("utf-8", "replace")))


def exportar(event, context):
    """Lambda disparada por la cola de exportación: manda lotes (<= 500) al Cloud Run de B."""
    msgs = event.get("Records", [])
    cfg = puente_gcp()
    if not cfg:
        print("puente-gcp sin configurar: los mensajes volverán a la cola")
        return {"batchItemFailures": [{"itemIdentifier": m["messageId"]} for m in msgs]}
    url, token = cfg
    por_tipo, fallos = {}, set()
    for m in msgs:
        try:
            c = json.loads(m["body"], parse_float=Decimal)
            por_tipo.setdefault(c["tipo"], []).append((m["messageId"], c["registros"]))
        except Exception as ex:
            print("mensaje ilegible:", m.get("messageId"), repr(ex))
            fallos.add(m["messageId"])
    resumen = {}
    for tipo, lista in por_tipo.items():
        lote, ids = [], []
        def enviar():
            if not lote:
                return
            try:
                r = post_gcp(url, token, tipo, lote)
                for k in ("recibidos", "insertados", "duplicados"):
                    resumen[tipo + "." + k] = resumen.get(tipo + "." + k, 0) + int(r.get(k, 0))
            except Exception as ex:
                print("ERROR enviando", tipo, repr(ex)[:300])
                fallos.update(ids)
        for mid, regs in lista:
            if lote and len(lote) + len(regs) > 500:
                enviar()
                lote, ids = [], []
            lote = lote + regs
            ids = ids + [mid]
        enviar()
    print(json.dumps(resumen))
    return {"batchItemFailures": [{"itemIdentifier": i} for i in sorted(fallos)]}


def post_exportar_historial(ev, m):
    """Reenvía a GCP lo que ya está guardado (telemetría, incidentes cerrados, mantenimientos)."""
    if not os.environ.get("EXPORT_QUEUE_URL"):
        return resp(500, {"error": "exportación no configurada", "detalle": "falta la variable EXPORT_QUEUE_URL"})
    b = cuerpo_json(ev)
    b = b if isinstance(b, dict) else {}
    t0, total = time.time(), {"telemetria": 0, "incidente": 0, "mantenimiento": 0, "consumo_repuesto": 0}
    kw = {}
    if isinstance(b.get("continuar"), dict) and b["continuar"].get("maquina_id") and b["continuar"].get("ts"):
        kw["ExclusiveStartKey"] = {"maquina_id": b["continuar"]["maquina_id"], "ts": b["continuar"]["ts"]}
    if b.get("maquina_id"):
        kw["FilterExpression"] = Attr("maquina_id").eq(b["maquina_id"])
    while True:
        r = tabla("telemetria").scan(**kw)
        regs = [{k: v for k, v in i.items() if k not in ("ttl", "recibido_ts")} for i in r["Items"]]
        total["telemetria"] += exportar_encolar("telemetria", regs)
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
        if time.time() - t0 > 9:
            return resp(200, {"parcial": True, "encolados": total, "continuar": r["LastEvaluatedKey"],
                              "detalle": "repite la llamada con {\"continuar\": <este valor>}"})
    incs = [i for i in tabla("incidentes").scan()["Items"]
            if str(i.get("incidente_id", "")).startswith("INC-") and i.get("estado") == "cerrado"]
    mans = tabla("mantenimientos").scan()["Items"]
    total["incidente"] = exportar_encolar("incidente", [registro_incidente(i) for i in incs])
    total["mantenimiento"] = exportar_encolar("mantenimiento", [registro_mantenimiento(x) for x in mans])
    total["consumo_repuesto"] = exportar_encolar("consumo_repuesto", [c for x in mans for c in registros_consumo(x)])
    return resp(200, {"parcial": False, "encolados": total})



# ================================================================= FASES 5-8: datos reales, correlación y flujo nocturno
def scan_todo(nombre, **kw):
    out = []
    while True:
        r = tabla(nombre).scan(**kw)
        out += r["Items"]
        if "LastEvaluatedKey" not in r:
            return out
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def scores_tipo(tipo):
    out, kw = [], {"KeyConditionExpression": Key("tipo").eq(tipo)}
    while True:
        r = tabla("scores").query(**kw)
        out += r["Items"]
        if "LastEvaluatedKey" not in r:
            return out
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def incidentes_estado(estado):
    out, kw = [], {"IndexName": "estado-index", "KeyConditionExpression": Key("estado").eq(estado)}
    while True:
        r = tabla("incidentes").query(**kw)
        out += r["Items"]
        if "LastEvaluatedKey" not in r:
            return out
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def guardar_score(tipo, clave, datos, version="aws-v1"):
    tabla("scores").put_item(Item=a_dec({"tipo": tipo, "clave": clave, "generado_ts": ahora_iso(),
                                         "version_modelo": version, "datos": datos}))


def fnum(x, d=0.0):
    return float(x) if es_num(x) else d


def referencia_fecha(maqs):
    """Fecha 'de hoy' para la simulación: la lectura más reciente (o hoy si no hay datos)."""
    ts = [m["ultimo_ts"] for m in maqs if m.get("ultimo_ts")]
    return (max(ts) if ts else ahora_iso())[:10]


def sumar_dias(fecha, n):
    return (datetime.strptime(fecha, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def riesgo_pct(maq, probs):
    r = maq.get("riesgo")
    if r is None and maq["maquina_id"] in probs:
        r = probs[maq["maquina_id"]].get("prob_falla")
    return int(round(fnum(r) * 100))


def probs_por_maquina():
    d = {}
    for it in scores_tipo("prob_falla"):
        r = it["datos"]
        if r["maquina_id"] not in d or r["ts"] > d[r["maquina_id"]]["ts"]:
            d[r["maquina_id"]] = r
    return d


def abiertos_por_maquina():
    d = {}
    for e in ("abierto", "en_proceso"):
        for i in incidentes_estado(e):
            d[i["maquina_id"]] = i
    return d


def estado_prev(riesgo, abierto):
    if abierto or riesgo >= 60:
        return "adelantar"
    return "posponible" if riesgo < 20 else "a_tiempo"


# ---------------------------------------------------------------- /flota
def get_flota(ev, m):
    q = query(ev)
    maqs, ab, probs = scan_todo("maquinas"), abiertos_por_maquina(), probs_por_maquina()
    out = []
    for x in sorted(maqs, key=lambda y: y["maquina_id"]):
        inc = ab.get(x["maquina_id"])
        estado = "requiere_intervencion" if inc else (x.get("estado") or "operativa")
        if q.get("zona") and x.get("zona") != q["zona"]:
            continue
        if q.get("estado") and estado != q["estado"]:
            continue
        out.append({"maquina_id": x["maquina_id"], "modelo": x.get("modelo"), "zona": x.get("zona"),
                    "lat": x.get("lat"), "lon": x.get("lon"), "estado": estado, "riesgo": riesgo_pct(x, probs),
                    "severidad": inc["severidad"] if inc else None,
                    "incidente_abierto": inc["incidente_id"] if inc else None,
                    "horas_op": x.get("horas_op"), "ultima_lectura": x.get("ultimo_ts")})
    return resp(200, {"generado_en": ahora_iso(), "maquinas": out})


# ---------------------------------------------------------------- /maquinas/{id}
def _pendiente_h(lects, v):
    pts = [(a_epoch(l["ts"]) / 3600.0, float(l[v])) for l in lects if es_num(l.get(v))]
    if len(pts) < 3:
        return None
    mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    den = sum((p[0] - mx) ** 2 for p in pts)
    return round(sum((p[0] - mx) * (p[1] - my) for p in pts) / den, 3) if den else 0.0


def get_maquina(ev, m):
    mid = m.group(1)
    maq = tabla("maquinas").get_item(Key={"maquina_id": mid}).get("Item")
    if not maq:
        return resp(404, {"error": "máquina no existe", "maquina_id": mid})
    n = int(pf("n_lecturas", 336))
    items = tabla("telemetria").query(KeyConditionExpression=Key("maquina_id").eq(mid), ScanIndexForward=False,
                                      Limit=n)["Items"]
    lects = list(reversed(items))
    z, tend = {}, {}
    for v in ("temp", "vib", "presion", "combustible_lph", "rpm"):
        xs = [float(l[v]) for l in lects if es_num(l.get(v))]
        if len(xs) >= 3:
            mu = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))
            z[v] = round((xs[-1] - mu) / sd, 2) if sd > 0 else 0.0
    for v in ("temp", "vib", "combustible_lph"):
        p = _pendiente_h(lects[-24:], v)
        if p is not None:
            tend[v] = p
    ult = lects[-1] if lects else {}
    ab = abiertos_por_maquina().get(mid)
    probs = probs_por_maquina()
    r = calcular_riesgo(mid)
    pred = None
    if r:
        mod = modelo_activo()
        var = max(z, key=lambda k: abs(z[k])) if z else None
        pred = {"prob_falla": round(r["prob"], 4), "horizonte_dias": int((mod or {}).get("horizonte_dias", 7)),
                "componente_probable": (ab or {}).get("componente_probable") or MAP_COMP.get(var, "motor"),
                "modelo": r["metodo"], "fuente": "logit-aws"}
    elif mid in probs:
        pr = probs[mid]
        pred = {"prob_falla": pr["prob_falla"], "horizonte_dias": pr["horizonte_dias"], "componente_probable": None,
                "modelo": pr["modelo"], "fuente": "bqml"}
    riesgo = int(round(pred["prob_falla"] * 100)) if pred else riesgo_pct(maq, probs)
    prev = tabla("scores").get_item(Key={"tipo": "preventivo", "clave": mid}).get("Item")
    prev_o = None
    if prev:
        prev_o = {"fecha_recomendada": prev["datos"]["fecha_recomendada"], "estado": estado_prev(riesgo, bool(ab)),
                  "motivo": prev["datos"]["motivo"]}
    hist = tabla("incidentes").query(IndexName="maquina-index", KeyConditionExpression=Key("maquina_id").eq(mid))["Items"]
    hist.sort(key=lambda i: i["abierto_ts"], reverse=True)
    return resp(200, {
        "maquina_id": mid, "modelo": maq.get("modelo"), "zona": maq.get("zona"), "lat": maq.get("lat"),
        "lon": maq.get("lon"), "estado": "requiere_intervencion" if ab else (maq.get("estado") or "operativa"),
        "riesgo": riesgo, "horas_op": maq.get("horas_op"), "incidente_abierto": ab["incidente_id"] if ab else None,
        "ultima_lectura": {k: ult.get(k) for k in ("ts", "temp", "vib", "presion", "combustible_lph", "carga_t",
                                                   "velocidad", "rpm")} if ult else None,
        "contexto": maq.get("contexto"), "z_scores": z, "tendencia_por_hora": tend, "prediccion": pred,
        "ultimos_incidentes": [i["incidente_id"] for i in hist[:5]], "preventivo": prev_o})


# ---------------------------------------------------------------- /preventivo
def ordenes_preventivo():
    maqs, ab, probs = scan_todo("maquinas"), abiertos_por_maquina(), probs_por_maquina()
    por_id = {x["maquina_id"]: x for x in maqs}
    hoy = referencia_fecha(maqs)
    scs = {it["datos"]["maquina_id"]: it["datos"] for it in scores_tipo("preventivo")}
    out = []
    for mid in sorted(por_id):
        x = por_id[mid]
        riesgo = riesgo_pct(x, probs)
        if mid in scs:
            d, fuente = scs[mid], "bqml"
            fecha, horas, motivo = d["fecha_recomendada"], d["horas_proyectadas"], d["motivo"]
        else:  # sin pronóstico de B: intervalo de 250 h con 16 h/día de uso
            h = fnum(x.get("horas_op"))
            falta = 250 - (h % 250)
            fecha, horas, fuente = sumar_dias(hoy, int(math.ceil(falta / 16.0))), round(h + falta, 1), "reglas"
            motivo = "Intervalo de 250 h (estimado sin pronóstico del modelo)"
        out.append({"maquina_id": mid, "zona": x.get("zona"), "fecha_recomendada": fecha, "horas_proyectadas": horas,
                    "estado": estado_prev(riesgo, mid in ab), "motivo": motivo, "riesgo": riesgo, "fuente": fuente})
    out.sort(key=lambda o: o["fecha_recomendada"])
    return out


def get_preventivo(ev, m):
    return resp(200, {"ordenes": ordenes_preventivo()})


# ---------------------------------------------------------------- /repuestos
def calcular_repuestos():
    stock = scan_todo("repuestos_stock")
    pron = [it["datos"] for it in scores_tipo("pron_repuestos")]
    dem = {}
    if pron:
        f0 = min(p["fecha"] for p in pron)
        fin = sumar_dias(f0, 6)
        for p in pron:
            if p["fecha"] <= fin:
                k = (p["zona"], p["componente"])
                dem[k] = dem.get(k, 0.0) + fnum(p["demanda_prevista"])
    filas, alertas = [], []
    for s in sorted(stock, key=lambda y: (y["zona"], y["componente"])):
        st, mn = fnum(s.get("stock")), fnum(s.get("minimo"), 1.0)
        d = round(dem.get((s["zona"], s["componente"]), fnum(s.get("demanda_proyectada_7d"))), 1)
        if d > st:
            est = "faltante_proyectado"
            alertas.append({"tipo": est, "componente": s["componente"], "zona": s["zona"],
                            "detalle": "La demanda proyectada a 7 días (%g) supera el stock (%g)" % (d, st)})
        elif st < mn:
            est = "bajo_minimo"
            alertas.append({"tipo": est, "componente": s["componente"], "zona": s["zona"],
                            "detalle": "El stock (%g) está bajo el mínimo (%g)" % (st, mn)})
        elif st > 3 * max(mn, 1):
            est = "sobre_stock"
        else:
            est = "ok"
        filas.append({"zona": s["zona"], "componente": s["componente"], "descripcion": s.get("descripcion"),
                      "stock": s.get("stock"), "minimo": s.get("minimo"), "demanda_proyectada_7d": d, "estado": est})
    return filas, alertas


def get_repuestos(ev, m):
    filas, alertas = calcular_repuestos()
    return resp(200, {"stock": filas, "alertas": alertas})


# ---------------------------------------------------------------- /kpis
def calcular_kpis():
    maqs, ab = scan_todo("maquinas"), abiertos_por_maquina()
    por_estado = {}
    for x in maqs:
        e = "requiere_intervencion" if x["maquina_id"] in ab else (x.get("estado") or "operativa")
        por_estado[e] = por_estado.get(e, 0) + 1
    incs = []
    for e in ("abierto", "en_proceso", "cerrado"):
        incs += incidentes_estado(e)
    tiempos = [fnum(i["tiempo_detenido_min"]) for i in incs if i["estado"] == "cerrado" and es_num(i.get("tiempo_detenido_min"))]
    reinc = sum(1 for i in incs if i.get("reincidente"))
    costos = []
    for it in scores_tipo("flota"):
        d = it["datos"]
        if es_num(d.get("costo_op")):
            costos.append(float(d["costo_op"]) / (7.0 if "W" in str(d.get("periodo", "")) else 1.0))
    return {"generado_en": ahora_iso(), "maquinas_total": len(maqs), "por_estado": por_estado,
            "incidentes_abiertos": len(ab), "tiempo_medio_reparacion_h": round(sum(tiempos) / len(tiempos) / 60, 1) if tiempos else 0.0,
            "reincidencia_pct": round(100.0 * reinc / len(incs), 1) if incs else 0.0,
            "costo_por_maquina_dia_pen": round(sum(costos) / len(costos), 1) if costos else None}


def get_kpis(ev, m):
    return resp(200, calcular_kpis())


# ================================================================= FASE 5: correlación (causa común)
def _ctx(maq, k):
    return (maq.get("contexto") or {}).get(k)


def correlacionar(inc):
    """Si varias máquinas abren incidente casi a la vez y comparten zona o lote de combustible,
    registra un grupo de causa común, lo enlaza a los incidentes y avisa a supervisor y gerente."""
    ventana = pf("ventana_corr_horas", 6) * 3600
    minimo = int(pf("correlacion_min", 2))
    ref = a_epoch(inc["inicio"])
    cand = {}
    for e in ("abierto", "en_proceso"):
        for i in incidentes_estado(e):
            if abs(ref - a_epoch(i["inicio"])) <= ventana:
                cand[i["incidente_id"]] = i
    cand[inc["incidente_id"]] = inc
    maqs = {}
    for i in cand.values():
        maqs[i["maquina_id"]] = tabla("maquinas").get_item(Key={"maquina_id": i["maquina_id"]}).get("Item") or {}
    yo = maqs[inc["maquina_id"]]
    lote_yo = _ctx(yo, "lote_combustible")
    grupo = [i for i in cand.values() if i["incidente_id"] == inc["incidente_id"]
             or i["zona"] == inc["zona"]
             or (lote_yo and _ctx(maqs[i["maquina_id"]], "lote_combustible") == lote_yo)]
    ids_maq = sorted({i["maquina_id"] for i in grupo})
    if len(ids_maq) < minimo:
        return None
    ms = [maqs[x] for x in ids_maq]

    def igual(k):
        vs = {_ctx(x, k) for x in ms}
        return len(vs) == 1 and None not in vs
    if igual("lote_combustible"):
        hip, det = "lote_combustible", "lote " + str(_ctx(ms[0], "lote_combustible"))
    elif len({i.get("componente_probable") for i in grupo}) == 1:
        hip, det = "componente", "componente " + str(grupo[0].get("componente_probable"))
    elif igual("clima"):
        hip, det = "clima", "clima " + str(_ctx(ms[0], "clima"))
    elif igual("terreno"):
        hip, det = "terreno", "terreno " + str(_ctx(ms[0], "terreno"))
    else:
        hip, det = "otra", "sin factor común claro"
        if len(ids_maq) < max(minimo, 3):  # solo coincidir en la zona no basta con 2 máquinas
            return None
    cuenta = {}
    for i in grupo:
        for v in i.get("variables_desvio") or []:
            cuenta[v] = cuenta.get(v, 0) + 1
    var = max(cuenta, key=cuenta.get) if cuenta else "temp"
    grupo.sort(key=lambda i: i["inicio"])
    lat = sum(fnum(x.get("lat")) for x in ms) / len(ms)
    lon = sum(fnum(x.get("lon")) for x in ms) / len(ms)
    radio = max([km(lat, lon, fnum(x.get("lat")), fnum(x.get("lon"))) * 1000 for x in ms] + [100.0])
    gid = "CC-%s-%s-%s" % (grupo[0]["zona"], hip, re.sub(r"\D", "", grupo[0]["inicio"])[:10])
    datos = {"grupo_id": gid, "zona": grupo[0]["zona"], "ts_inicio": grupo[0]["inicio"], "ts_fin": grupo[-1]["inicio"],
             "maquina_ids": ids_maq, "variable_comun": var, "hipotesis": hip, "detalle": det,
             "centro_lat": round(lat, 5), "centro_lon": round(lon, 5), "radio_m": round(radio),
             "incidentes": [i["incidente_id"] for i in grupo], "origen": "aws"}
    guardar_score("causa_comun", gid, datos, "reglas-aws-v1")
    for i in grupo:
        tabla("incidentes").update_item(Key={"incidente_id": i["incidente_id"]}, UpdateExpression="SET grupo_causa_comun = :g",
                                        ExpressionAttributeValues={":g": gid})
    inc["grupo_causa_comun"] = gid
    for rol in ("supervisor", "gerente"):
        ts = ahora_iso()
        tabla("notificaciones").put_item(Item={
            "rol": rol, "ts_id": ts + "#" + inc["incidente_id"] + "#" + uuid.uuid4().hex[:6], "ts": ts,
            "severidad": inc["severidad"], "incidente_id": inc["incidente_id"],
            "titulo": "Posible causa común (%s)" % hip,
            "mensaje": "%d máquinas (%s) con desvío en %s. Factor compartido: %s." % (len(ids_maq), ", ".join(ids_maq), var, det)})
    return datos


def get_causas_comunes(ev, m):
    gs = [it["datos"] for it in scores_tipo("causa_comun")]
    q = query(ev)
    if q.get("origen"):
        gs = [g for g in gs if g.get("origen", "bqml") == q["origen"]]
    gs.sort(key=lambda g: g["ts_inicio"], reverse=True)
    return resp(200, {"grupos": gs})


# ================================================================= FASE 6: flujo nocturno
def semana_clave(hoy):
    return datetime.strptime(hoy, "%Y-%m-%d").strftime("%G-W%V")


def accion_plan_preventivo(ev):
    maqs = scan_todo("maquinas")
    hoy = referencia_fecha(maqs)
    ordenes = ordenes_preventivo()
    cap = max(1, len(scan_todo("tecnicos")))
    pesos = {"adelantar": 1.0, "a_tiempo": 0.5, "posponible": 0.1}
    for o in ordenes:
        dias = (datetime.strptime(o["fecha_recomendada"], "%Y-%m-%d") - datetime.strptime(hoy, "%Y-%m-%d")).days
        urg = 1.0 if dias <= 3 else (0.6 if dias <= 7 else (0.3 if dias <= 14 else 0.0))
        o["prioridad"] = round(0.5 * o["riesgo"] / 100.0 + 0.3 * urg + 0.2 * pesos[o["estado"]], 3)
    ordenes.sort(key=lambda o: o["prioridad"], reverse=True)
    tareas = []
    for i, o in enumerate(ordenes):
        d = i // cap
        tareas.append({"maquina_id": o["maquina_id"], "zona": o["zona"], "prioridad": o["prioridad"],
                       "estado": o["estado"], "motivo": o["motivo"],
                       "dia": sumar_dias(hoy, 1 + d) if d < 7 else "semana_siguiente"})
    datos = {"semana": semana_clave(hoy), "generado": ahora_iso(), "capacidad_diaria": cap, "tareas": tareas}
    guardar_score("plan_semanal", datos["semana"], datos)
    return {"tareas": len(tareas), "capacidad_diaria": cap}


def accion_reposicion(ev):
    _, alertas = calcular_repuestos()
    hoy = referencia_fecha(scan_todo("maquinas"))
    guardar_score("alertas_repuestos", semana_clave(hoy), {"generado": ahora_iso(), "alertas": alertas})
    return {"alertas": len(alertas)}


def accion_kpis(ev):
    k = calcular_kpis()
    guardar_score("kpis_snapshot", k["generado_en"][:10], k)
    return {"maquinas": k["maquinas_total"], "incidentes_abiertos": k["incidentes_abiertos"]}


def accion_resumen(ev):
    k = calcular_kpis()
    filas, alertas = calcular_repuestos()
    maqs, probs = scan_todo("maquinas"), probs_por_maquina()
    top = sorted(({"maquina_id": x["maquina_id"], "riesgo": riesgo_pct(x, probs), "zona": x.get("zona")} for x in maqs),
                 key=lambda t: t["riesgo"], reverse=True)[:3]
    causas = [it["datos"] for it in scores_tipo("causa_comun")]
    causas.sort(key=lambda g: g["ts_inicio"], reverse=True)
    planes = sorted(scores_tipo("plan_semanal"), key=lambda i: i["generado_ts"], reverse=True)
    tareas = planes[0]["datos"]["tareas"] if planes else []
    ab = [i for e in ("abierto", "en_proceso") for i in incidentes_estado(e)]
    crit = sum(1 for i in ab if i["severidad"] == "critica")
    pe = k["por_estado"]
    partes = ["Flota de %d máquinas: %d operativas y %d requieren intervención." % (
        k["maquinas_total"], pe.get("operativa", 0), pe.get("requiere_intervencion", 0)),
        "Incidentes abiertos: %d (%d críticos). Tiempo medio de reparación: %.1f h; reincidencia: %.1f%%." % (
            len(ab), crit, k["tiempo_medio_reparacion_h"], k["reincidencia_pct"])]
    if top and top[0]["riesgo"] > 0:
        partes.append("Mayor riesgo: " + ", ".join("%s (%d%%)" % (t["maquina_id"], t["riesgo"]) for t in top) + ".")
    if alertas:
        partes.append("Repuestos con alerta: " + ", ".join("%s en %s" % (a["componente"], a["zona"]) for a in alertas[:5]) + ".")
    if causas:
        partes.append("Causas comunes recientes: %d (última: %s en %s)." % (len(causas), causas[0]["hipotesis"], causas[0]["zona"]))
    if tareas:
        partes.append("Plan semanal: %d tareas; primera: %s el %s." % (len(tareas), tareas[0]["maquina_id"], tareas[0]["dia"]))
    texto, fuente = " ".join(partes), "plantilla"
    modelo_id = os.environ.get("BEDROCK_MODEL_ID", "")
    if modelo_id:
        try:
            r = cliente("bedrock-runtime").converse(
                modelId=modelo_id, inferenceConfig={"maxTokens": 300, "temperature": 0.2},
                messages=[{"role": "user", "content": [{"text": "Redacta en español, en 4 frases, un resumen ejecutivo "
                                                         "para un gerente de operaciones mineras con estos datos: " + texto}]}])
            t = r["output"]["message"]["content"][0]["text"].strip()
            if t:
                texto, fuente = t, "bedrock"
        except Exception as ex:
            print("Bedrock no disponible, uso plantilla:", type(ex).__name__)
    datos = {"generado": ahora_iso(), "texto": texto, "fuente": fuente, "kpis": k, "top_riesgo": top,
             "alertas_repuestos": alertas, "causas_comunes": len(causas), "tareas_semana": len(tareas)}
    guardar_score("resumen_ejecutivo", "ultimo", datos)
    guardar_score("resumen_ejecutivo", k["generado_en"][:10], datos)
    return {"fuente": fuente}


ACCIONES_NOCTURNO = {"plan_preventivo": accion_plan_preventivo, "reposicion": accion_reposicion,
                     "kpis": accion_kpis, "resumen": accion_resumen}


def get_plan_semanal(ev, m):
    planes = sorted(scores_tipo("plan_semanal"), key=lambda i: i["generado_ts"], reverse=True)
    if not planes:
        return resp(404, {"error": "aún no hay plan semanal", "detalle": "ejecuta POST /admin/nocturno"})
    return resp(200, planes[0]["datos"])


def get_resumen_ejecutivo(ev, m):
    it = tabla("scores").get_item(Key={"tipo": "resumen_ejecutivo", "clave": "ultimo"}).get("Item")
    if not it:
        return resp(404, {"error": "aún no hay resumen ejecutivo", "detalle": "ejecuta POST /admin/nocturno"})
    return resp(200, it["datos"])


def post_admin_nocturno(ev, m):
    """Lanza el flujo nocturno. Con {"directo": true} lo ejecuta aquí mismo, sin Step Functions."""
    b = cuerpo_json(ev)
    b = b if isinstance(b, dict) else {}
    arn = os.environ.get("NOCTURNO_ARN", "")
    if b.get("directo") or not arn:
        out = {}
        for nombre in ("plan_preventivo", "reposicion", "kpis", "resumen"):
            out[nombre] = ACCIONES_NOCTURNO[nombre]({})
        return resp(200, {"modo": "directo", "resultado": out})
    r = cliente("stepfunctions").start_execution(stateMachineArn=arn, name="manual-" + str(int(time.time())),
                                                 input=json.dumps({"origen": "manual"}))
    return resp(202, {"modo": "step_functions", "ejecucion": r["executionArn"]})


RUTAS = [
    ("GET", r"^/flota$", get_flota),
    ("GET", r"^/maquinas/([^/]+)$", get_maquina),
    ("GET", r"^/telemetria/([^/]+)$", get_historial),
    ("GET", r"^/incidentes$", get_incidentes),
    ("GET", r"^/incidentes/([^/]+)$", get_incidente),
    ("POST", r"^/incidentes/([^/]+)/cierre$", post_cierre),
    ("POST", r"^/incidentes/([^/]+)/atencion$", post_atencion),
    ("GET", r"^/tecnicos$", get_tecnicos),
    ("GET", r"^/repuestos$", get_repuestos),
    ("GET", r"^/preventivo$", get_preventivo),
    ("GET", r"^/kpis$", get_kpis),
    ("GET", r"^/causas-comunes$", get_causas_comunes),
    ("GET", r"^/plan-semanal$", get_plan_semanal),
    ("GET", r"^/resumen-ejecutivo$", get_resumen_ejecutivo),
    ("GET", r"^/notificaciones$", get_notificaciones),
    ("GET", r"^/salud$", get_salud),
    ("GET", r"^/scores/([^/]+)$", get_scores),
    ("POST", r"^/ingesta$", post_ingesta),
    ("POST", r"^/scores$", post_scores),
    ("POST", r"^/admin/semilla$", post_semilla),
    ("POST", r"^/admin/incidente$", post_admin_incidente),
    ("GET", r"^/admin/riesgo/([^/]+)$", get_admin_riesgo),
    ("GET", r"^/admin/resumen$", get_admin_resumen),
    ("POST", r"^/admin/reiniciar$", post_admin_reiniciar),
    ("POST", r"^/admin/exportar-historial$", post_exportar_historial),
    ("POST", r"^/admin/nocturno$", post_admin_nocturno),
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

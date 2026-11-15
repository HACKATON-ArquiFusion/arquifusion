import hmac
import json
import math
import os
import re
import time
import uuid
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
        if actualizar_maquina(r):  # solo si es la lectura más nueva de la máquina
            try:
                evaluar_maquina(r["maquina_id"])
            except Exception as ex:  # la detección nunca debe hacer fallar la ingesta
                print("ERROR detección", r["maquina_id"], repr(ex))
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
def evaluar_maquina(maq_id):
    if param("deteccion_activa", "true") != "true":
        return None
    n = int(pf("n_lecturas", 336))
    items = tabla("telemetria").query(KeyConditionExpression=Key("maquina_id").eq(maq_id),
                                      ScanIndexForward=False, Limit=n, ConsistentRead=True)["Items"]
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
    tabla("maquinas").update_item(
        Key={"maquina_id": maq_id}, UpdateExpression="SET riesgo = :r, riesgo_ts = :t, metodo_riesgo = :m",
        ExpressionAttributeValues=a_dec({":r": prob, ":t": actual["ts"], ":m": metodo}))
    if prob < umbral and actual["estado"] != "requiere_intervencion":
        return None
    return abrir_incidente(maq_id, actual, prob, umbral, metodo, evid)


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
        cliente("stepfunctions").start_execution(stateMachineArn=arn, name=item["incidente_id"],
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
    tabla("mantenimientos").put_item(Item=a_dec({
        "mantenimiento_id": "MAN-" + inc_id[4:], "maquina_id": inc["maquina_id"], "ts": ahora,
        "tipo_mantenimiento": "correctivo", "estado": "completado", "componentes": comps,
        "repuestos_usados": datos.get("repuestos_usados") or [], "incidente_id": inc_id,
        "intervencion": datos.get("intervencion")}))
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
                "justificacion_asignacion", "repuestos_reservados", "reincidente", "prob_falla", "en_cola"]
CAMPOS_DETALLE = CAMPOS_LISTA + ["tiempo_detenido_min", "intervencion", "componentes_reemplazados", "eventos",
                                 "explicacion", "escalado", "motivo_cierre", "componente_probable", "umbral",
                                 "metodo", "faltante_repuesto"]


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


RUTAS = [
    ("GET", r"^/flota$", fijo("flota.json")),
    ("GET", r"^/maquinas/([^/]+)$", get_maquina),
    ("GET", r"^/telemetria/([^/]+)$", get_historial),
    ("GET", r"^/incidentes$", get_incidentes),
    ("GET", r"^/incidentes/([^/]+)$", get_incidente),
    ("POST", r"^/incidentes/([^/]+)/cierre$", post_cierre),
    ("POST", r"^/incidentes/([^/]+)/atencion$", post_atencion),
    ("GET", r"^/tecnicos$", get_tecnicos),
    ("GET", r"^/repuestos$", fijo("repuestos.json")),
    ("GET", r"^/preventivo$", fijo("preventivo.json")),
    ("GET", r"^/kpis$", fijo("kpis.json")),
    ("GET", r"^/notificaciones$", get_notificaciones),
    ("GET", r"^/salud$", get_salud),
    ("GET", r"^/scores/([^/]+)$", get_scores),
    ("POST", r"^/ingesta$", post_ingesta),
    ("POST", r"^/scores$", post_scores),
    ("POST", r"^/admin/semilla$", post_semilla),
    ("POST", r"^/admin/incidente$", post_admin_incidente),
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

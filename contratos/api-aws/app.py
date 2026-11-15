import hmac
import json
import os
import re

API_KEY = os.environ.get("API_KEY", "")
BASE = os.path.join(os.path.dirname(__file__), "ejemplos")
TIPOS_SCORES = {"prob_falla", "pron_repuestos", "preventivo", "flota",
                "combustible", "pares", "causa_comun", "pesos_modelo"}
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,x-api-key",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


def cargar(nombre):
    with open(os.path.join(BASE, nombre), encoding="utf-8") as f:
        return json.load(f)


def resp(codigo, cuerpo):
    return {
        "statusCode": codigo,
        "headers": {"Content-Type": "application/json", **CORS},
        "body": json.dumps(cuerpo, ensure_ascii=False),
    }


def cuerpo_json(ev):
    try:
        return json.loads(ev.get("body") or "{}")
    except json.JSONDecodeError:
        return None


def query(ev):
    return ev.get("queryStringParameters") or {}


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
        return resp(400, {"error": "JSON inválido", "detalle": "el cuerpo no es JSON"})  # CAMBIO: formato {error, detalle}
    return resp(200, {"incidente_id": m.group(1), "estado": "cerrado"})  # CAMBIO: 'cerrado' (estados de B)


def post_ingesta(ev, m):
    b = cuerpo_json(ev)
    if b is None:
        return resp(400, {"error": "JSON inválido", "detalle": "el cuerpo no es JSON"})  # CAMBIO
    registros = b if isinstance(b, list) else b.get("registros", [b])
    ok, malos = 0, []
    for i, r in enumerate(registros):
        if isinstance(r, dict) and r.get("maquina_id") and r.get("ts"):
            ok += 1
        else:
            malos.append(i)
    return resp(202, {"recibidos": len(registros), "aceptados": ok, "rechazados": malos})  # CAMBIO: + recibidos


def post_scores(ev, m):
    b = cuerpo_json(ev)
    if not isinstance(b, dict) or b.get("tipo") not in TIPOS_SCORES:  # CAMBIO
        return resp(400, {"error": "tipo inválido",
                          "detalle": "valores válidos: " + ", ".join(sorted(TIPOS_SCORES))})  # CAMBIO
    registros = b.get("registros")
    if not isinstance(registros, list):  # CAMBIO: valida registros[]
        return resp(400, {"error": "registros inválidos", "detalle": "registros debe ser una lista"})
    return resp(200, {"recibidos": len(registros)})  # CAMBIO: solo {recibidos}, como pide B


RUTAS = [
    ("GET", r"^/flota$", fijo("flota.json")),
    ("GET", r"^/maquinas/([^/]+)$", get_maquina),
    ("GET", r"^/incidentes$", get_incidentes),
    ("GET", r"^/incidentes/([^/]+)$", get_incidente),
    ("POST", r"^/incidentes/([^/]+)/cierre$", post_cierre),
    ("GET", r"^/tecnicos$", fijo("tecnicos.json")),
    ("GET", r"^/repuestos$", fijo("repuestos.json")),
    ("GET", r"^/preventivo$", fijo("preventivo.json")),
    ("GET", r"^/kpis$", fijo("kpis.json")),
    ("GET", r"^/notificaciones$", get_notificaciones),
    ("POST", r"^/ingesta$", post_ingesta),
    ("POST", r"^/scores$", post_scores),
]


def handler(event, context):
    metodo = event["httpMethod"]
    ruta = event["path"].rstrip("/") or "/"
    if metodo == "OPTIONS":
        return resp(200, {})
    cab = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not API_KEY or not hmac.compare_digest(cab.get("x-api-key", ""), API_KEY):
        return resp(401, {"error": "api key inválida"})  # CAMBIO: con tilde, igual que el contrato de B
    for met, patron, fn in RUTAS:
        if met == metodo:
            m = re.match(patron, ruta)
            if m:
                return fn(event, m)
    return resp(404, {"error": "ruta no existe", "ruta": ruta})

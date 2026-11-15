import hmac
import json
import logging
import os
import statistics
import time

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

PROJECT = os.environ.get("GCP_PROJECT", "")
LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
MODELO = os.environ.get("GEMINI_MODELO", "gemini-2.5-flash")
API_KEY = os.environ.get("IA_API_KEY", "").strip()
API_AWS = os.environ.get("API_AWS_URL", "").rstrip("/")
API_AWS_KEY = os.environ.get("API_AWS_KEY", "").strip()
TIMEOUT_MODELO = float(os.environ.get("TIMEOUT_MODELO", "20"))
TIMEOUT_API = float(os.environ.get("TIMEOUT_API", "8"))

ROLES = ["operador", "tecnico", "supervisor", "gerente"]
COMPONENTES = [
    "motor", "sistema_hidraulico", "transmision", "frenos",
    "tren_rodaje", "refrigeracion", "inyeccion_combustible", "filtros",
]
SEVERIDADES = ["baja", "media", "alta", "critica"]
URGENCIA_POR_SEVERIDAD = {
    "baja": "rutina",
    "media": "programar",
    "alta": "atender_hoy",
    "critica": "inmediata",
}
COMPONENTE_POR_VARIABLE = {
    "temp": "refrigeracion",
    "vib": "tren_rodaje",
    "presion": "sistema_hidraulico",
    "combustible_lph": "inyeccion_combustible",
    "rpm": "motor",
    "carga_t": "transmision",
    "velocidad": "transmision",
}
VARIABLES = ["temp", "vib", "presion", "combustible_lph", "carga_t", "velocidad", "rpm"]

_cliente = None


class ErrorValidacion(Exception):
    pass


def cliente():
    global _cliente
    if _cliente is None:
        from google import genai

        _cliente = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    return _cliente


def autorizado():
    enviada = str(request.headers.get("x-api-key", ""))
    return bool(API_KEY) and hmac.compare_digest(enviada.encode("utf-8"), API_KEY.encode("utf-8"))


def cuerpo():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        raise ErrorValidacion("el cuerpo debe ser un objeto JSON")
    return d


def exigir(d, campo, tipo=str):
    v = d.get(campo)
    if v is None or (tipo is str and not str(v).strip()):
        raise ErrorValidacion(f"falta el campo {campo}")
    if tipo is not str and not isinstance(v, tipo):
        raise ErrorValidacion(f"{campo} tiene un tipo inválido")
    return v


def recortar(texto, limite):
    t = " ".join(str(texto).split())
    return t if len(t) <= limite else t[: limite - 1].rstrip() + "…"


def desviaciones_de_ventana(ventana):
    fuera = {}
    for var in VARIABLES:
        serie = [r.get(var) for r in ventana if isinstance(r.get(var), (int, float))]
        if len(serie) < 2:
            continue
        base, ultimo = serie[:-1], serie[-1]
        media = statistics.fmean(base)
        desv = statistics.pstdev(base) if len(base) > 1 else 0.0
        if desv > 0:
            fuera[var] = round((ultimo - media) / desv, 2)
        elif ultimo != media:
            fuera[var] = round(ultimo - media, 2)
    return fuera


def variables_top(desviaciones, n=3):
    return [v for v, _ in sorted(desviaciones.items(), key=lambda kv: abs(kv[1]), reverse=True)[:n]]


def componente_por_desviacion(desviaciones):
    top = variables_top(desviaciones, 1)
    return COMPONENTE_POR_VARIABLE.get(top[0], "motor") if top else "motor"


def generar(prompt, esquema, instruccion):
    from google.genai import types

    t0 = time.perf_counter()
    r = cliente().models.generate_content(
        model=MODELO,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=1200,
            response_mime_type="application/json",
            response_schema=esquema,
            system_instruction=instruccion,
            http_options=types.HttpOptions(timeout=int(TIMEOUT_MODELO * 1000)),
        ),
    )
    ms = int((time.perf_counter() - t0) * 1000)
    datos = json.loads(r.text)
    if not isinstance(datos, dict):
        raise ValueError("el modelo no devolvió un objeto")
    return datos, ms


def leer_api_aws(ruta):
    if not API_AWS:
        return None
    try:
        r = requests.get(
            f"{API_AWS}{ruta}",
            headers={"x-api-key": API_AWS_KEY},
            timeout=TIMEOUT_API,
        )
        if r.status_code != 200:
            logging.warning("api aws %s devolvió %s", ruta, r.status_code)
            return None
        return r.json()
    except requests.RequestException as e:
        logging.warning("api aws %s falló: %s", ruta, e)
        return None


ESQUEMA_PREDECIR = {
    "type": "OBJECT",
    "properties": {
        "horizonte_dias": {"type": "INTEGER"},
        "componente_probable": {"type": "STRING", "enum": COMPONENTES},
        "confianza": {"type": "NUMBER"},
        "variables_clave": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["horizonte_dias", "componente_probable", "confianza", "variables_clave"],
}

ESQUEMA_EXPLICAR = {
    "type": "OBJECT",
    "properties": {
        "causa_probable": {"type": "STRING"},
        "componente": {"type": "STRING", "enum": COMPONENTES},
        "accion_recomendada": {"type": "STRING"},
        "mensajes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "rol": {"type": "STRING", "enum": ROLES},
                    "titulo": {"type": "STRING"},
                    "mensaje": {"type": "STRING"},
                },
                "required": ["rol", "titulo", "mensaje"],
            },
        },
    },
    "required": ["causa_probable", "componente", "accion_recomendada", "mensajes"],
}

ESQUEMA_CHAT = {
    "type": "OBJECT",
    "properties": {
        "respuesta": {"type": "STRING"},
        "datos_usados": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "endpoint": {"type": "STRING"},
                    "resumen": {"type": "STRING"},
                },
                "required": ["endpoint", "resumen"],
            },
        },
    },
    "required": ["respuesta", "datos_usados"],
}

INSTRUCCION_PREDECIR = (
    "Eres un especialista en mantenimiento de maquinaria pesada. Recibes una ventana de "
    "telemetría y la probabilidad de falla que ya calculó un modelo estadístico. No recalcules "
    "esa probabilidad ni la menciones. Tu tarea es estimar en cuántos días se materializaría la "
    "falla y qué componente es el más probable, usando solo los componentes del catálogo. "
    "La confianza es tu seguridad sobre el componente, entre 0 y 1."
)

INSTRUCCION_EXPLICAR = (
    "Eres el responsable de mantenimiento de una operación minera. Explicas incidentes en "
    "español claro y breve. Escribes un mensaje por cada rol pedido, con distinto nivel de "
    "detalle: el operador recibe solo qué hacer con su máquina y sin cifras; el técnico recibe "
    "causa, variables desviadas con sus sigmas, repuesto y zona; el supervisor recibe impacto "
    "operativo y tiempo estimado; el gerente recibe una sola frase sin jerga. "
    "Títulos de 60 caracteres como máximo y mensajes de 240 como máximo. No inventes datos "
    "que no estén en la entrada."
)

INSTRUCCION_CHAT = (
    "Eres un asistente de operaciones de una flota de maquinaria pesada. Respondes solo con los "
    "datos de la API que se te entregan. Si el dato no está, dilo con claridad y ofrece lo más "
    "cercano que sí tengas. Nunca inventes cifras, identificadores ni fechas. Responde en "
    "español, en 600 caracteres como máximo. En datos_usados indica los endpoints que "
    "sustentan tu respuesta."
)


@app.get("/health")
def health():
    return jsonify({"estado": "ok", "modelo": MODELO, "proyecto": PROJECT, "region": LOCATION})


@app.post("/ia/predecir")
def predecir():
    if not autorizado():
        return jsonify({"error": "api key inválida"}), 401
    try:
        d = cuerpo()
        maquina_id = exigir(d, "maquina_id")
        exigir(d, "ts")
        modelo_maquina = exigir(d, "modelo")
        zona = exigir(d, "zona")
        ventana = exigir(d, "ventana", list)
        if not 2 <= len(ventana) <= 200:
            raise ErrorValidacion("ventana debe tener entre 2 y 200 registros")
    except ErrorValidacion as e:
        return jsonify({"error": "validación", "detalle": str(e)}), 400

    score = d.get("score_modelo") or {}
    prob = score.get("prob_falla")
    if prob is not None and not isinstance(prob, (int, float)):
        return jsonify({"error": "validación", "detalle": "score_modelo.prob_falla debe ser numérico"}), 400

    desv = desviaciones_de_ventana(ventana)
    claves = variables_top(desv)

    prompt = json.dumps(
        {
            "maquina_id": maquina_id,
            "modelo": modelo_maquina,
            "zona": zona,
            "prob_falla_del_modelo": prob,
            "horizonte_del_modelo_dias": score.get("horizonte_dias"),
            "desviaciones_sigma": desv,
            "ultima_lectura": ventana[-1],
            "lecturas": len(ventana),
            "componentes_posibles": COMPONENTES,
        },
        ensure_ascii=False,
    )

    try:
        datos, ms = generar(prompt, ESQUEMA_PREDECIR, INSTRUCCION_PREDECIR)
        horizonte = int(datos["horizonte_dias"])
        componente = datos["componente_probable"]
        confianza = float(datos["confianza"])
        if componente not in COMPONENTES:
            raise ValueError("componente fuera del catálogo")
        horizonte = max(1, min(30, horizonte))
        confianza = max(0.0, min(1.0, confianza))
        variables = [v for v in datos.get("variables_clave", []) if v in VARIABLES][:3] or claves
        fuente = "modelo"
    except Exception as e:
        logging.warning("predecir con plantilla: %s", e)
        t0 = time.perf_counter()
        horizonte = int(score.get("horizonte_dias") or 7)
        componente = componente_por_desviacion(desv)
        confianza = 0.3
        variables = claves
        ms = int((time.perf_counter() - t0) * 1000)
        fuente = "plantilla"

    return jsonify(
        {
            "maquina_id": maquina_id,
            "prob_falla": prob,
            "horizonte_dias": horizonte,
            "componente_probable": componente,
            "confianza": round(confianza, 2),
            "variables_clave": variables,
            "fuente": fuente,
            "latencia_ms": ms,
        }
    )


def mensajes_de_plantilla(roles, maquina_id, zona, severidad, desv, componente, tecnico, repuesto):
    detalle = ", ".join(f"{v} {s:+} sigma" for v, s in sorted(desv.items(), key=lambda kv: abs(kv[1]), reverse=True))
    por_rol = {
        "operador": (
            f"Detener {maquina_id}",
            "Detenga el equipo y espere al técnico." if severidad in ("alta", "critica")
            else "Reporte el comportamiento al técnico de turno y continúe con precaución.",
        ),
        "tecnico": (
            f"Asignado a {maquina_id} ({zona})",
            f"Severidad {severidad}. Desviaciones: {detalle or 'sin desviaciones registradas'}."
            + (f" Repuesto reservado: {repuesto}." if repuesto else "")
            + (f" Técnico {tecnico}." if tecnico else ""),
        ),
        "supervisor": (
            f"{maquina_id} requiere intervención en {zona}",
            f"Incidente de severidad {severidad} en {maquina_id} ({zona})."
            + (f" Técnico {tecnico} asignado." if tecnico else " Sin técnico asignado todavía."),
        ),
        "gerente": (
            f"Incidente de severidad {severidad} en {zona}",
            f"1 equipo afectado en {zona}" + (", técnico asignado." if tecnico else ", sin técnico asignado."),
        ),
    }
    salida = []
    for rol in roles:
        titulo, mensaje = por_rol.get(rol, (f"Incidente en {maquina_id}", f"Severidad {severidad} en {zona}."))
        salida.append({"rol": rol, "titulo": recortar(titulo, 60), "mensaje": recortar(mensaje, 240)})
    return salida


@app.post("/ia/explicar")
def explicar():
    if not autorizado():
        return jsonify({"error": "api key inválida"}), 401
    try:
        d = cuerpo()
        incidente_id = exigir(d, "incidente_id")
        maquina_id = exigir(d, "maquina_id")
        exigir(d, "ts")
        severidad = exigir(d, "severidad")
        zona = exigir(d, "zona")
        desv = exigir(d, "desviaciones", dict)
        if severidad not in SEVERIDADES:
            raise ErrorValidacion(f"severidad debe ser una de {SEVERIDADES}")
        roles = d.get("roles") or ROLES
        if not isinstance(roles, list) or not all(r in ROLES for r in roles):
            raise ErrorValidacion(f"roles debe ser una lista dentro de {ROLES}")
    except ErrorValidacion as e:
        return jsonify({"error": "validación", "detalle": str(e)}), 400

    desv = {k: v for k, v in desv.items() if isinstance(v, (int, float))}
    tecnico = d.get("tecnico_asignado")
    repuesto = d.get("repuesto_reservado")
    urgencia = URGENCIA_POR_SEVERIDAD[severidad]

    prompt = json.dumps(
        {
            "incidente_id": incidente_id,
            "maquina_id": maquina_id,
            "modelo": d.get("modelo"),
            "zona": zona,
            "severidad": severidad,
            "desviaciones_sigma": desv,
            "contexto": d.get("contexto") or {},
            "repuesto_reservado": repuesto,
            "tecnico_asignado": tecnico,
            "ultimas_lecturas": (d.get("ventana") or [])[-5:],
            "roles_pedidos": roles,
            "componentes_posibles": COMPONENTES,
        },
        ensure_ascii=False,
    )

    try:
        datos, ms = generar(prompt, ESQUEMA_EXPLICAR, INSTRUCCION_EXPLICAR)
        componente = datos["componente"]
        if componente not in COMPONENTES:
            raise ValueError("componente fuera del catálogo")
        recibidos = {m["rol"]: m for m in datos.get("mensajes", []) if m.get("rol") in roles}
        if set(recibidos) != set(roles):
            raise ValueError("faltan mensajes por rol")
        mensajes = [
            {
                "rol": rol,
                "titulo": recortar(recibidos[rol]["titulo"], 60),
                "mensaje": recortar(recibidos[rol]["mensaje"], 240),
            }
            for rol in roles
        ]
        causa = recortar(datos["causa_probable"], 240)
        accion = recortar(datos["accion_recomendada"], 240)
        fuente = "modelo"
    except Exception as e:
        logging.warning("explicar con plantilla: %s", e)
        t0 = time.perf_counter()
        componente = repuesto if repuesto in COMPONENTES else componente_por_desviacion(desv)
        top = variables_top(desv, 2)
        causa = recortar(
            f"Desviación alta en {' y '.join(top)} respecto al historial de la máquina."
            if top else "Comportamiento fuera de lo esperado respecto al historial de la máquina.",
            240,
        )
        accion = recortar(f"Inspeccionar {componente} antes de reanudar la operación.", 240)
        mensajes = mensajes_de_plantilla(roles, maquina_id, zona, severidad, desv, componente, tecnico, repuesto)
        ms = int((time.perf_counter() - t0) * 1000)
        fuente = "plantilla"

    return jsonify(
        {
            "incidente_id": incidente_id,
            "causa_probable": causa,
            "componente": componente,
            "accion_recomendada": accion,
            "urgencia": urgencia,
            "mensajes": mensajes,
            "fuente": fuente,
            "latencia_ms": ms,
        }
    )


PLURAL_ESTADO = {
    "operativa": "operativas",
    "detenida": "detenidas",
    "requiere_intervencion": "requieren intervención",
}


def resumen_kpis(kpis):
    if not kpis:
        return None
    estados = kpis.get("por_estado") or {}
    partes = [f"{kpis.get('maquinas_total', '?')} máquinas"]
    for k, v in estados.items():
        partes.append(f"{v} {PLURAL_ESTADO.get(k, k.replace('_', ' '))}")
    abiertos = kpis.get("incidentes_abiertos", 0)
    partes.append(f"{abiertos} incidente abierto" if abiertos == 1 else f"{abiertos} incidentes abiertos")
    return ", ".join(partes)


@app.post("/ia/chat")
def chat():
    if not autorizado():
        return jsonify({"error": "api key inválida"}), 401
    try:
        d = cuerpo()
        mensaje = exigir(d, "mensaje")
        if len(str(mensaje)) > 500:
            raise ErrorValidacion("mensaje no puede superar 500 caracteres")
        rol = d.get("rol") or "supervisor"
        if rol not in ROLES:
            raise ErrorValidacion(f"rol debe ser uno de {ROLES}")
        historial = d.get("historial") or []
        if not isinstance(historial, list) or len(historial) > 10:
            raise ErrorValidacion("historial debe ser una lista de hasta 10 turnos")
    except ErrorValidacion as e:
        return jsonify({"error": "validación", "detalle": str(e)}), 400

    kpis = leer_api_aws("/kpis")
    flota = leer_api_aws("/flota")
    incidentes = leer_api_aws("/incidentes")

    maquinas = (flota or {}).get("maquinas", [])
    criticas = sorted(
        [m for m in maquinas if m.get("estado") != "operativa"],
        key=lambda m: m.get("riesgo", 0),
        reverse=True,
    )[:10]

    contexto = {
        "kpis": kpis,
        "maquinas_no_operativas": criticas,
        "total_maquinas": len(maquinas),
        "incidentes": (incidentes or {}).get("incidentes", [])[:10],
        "endpoints_disponibles": ["/kpis", "/flota", "/incidentes"],
    }

    prompt = json.dumps(
        {
            "pregunta": mensaje,
            "rol_de_quien_pregunta": rol,
            "historial": historial[-10:],
            "datos_api": contexto,
        },
        ensure_ascii=False,
        default=str,
    )

    try:
        if kpis is None and flota is None:
            raise ValueError("la api de aws no respondió")
        datos, ms = generar(prompt, ESQUEMA_CHAT, INSTRUCCION_CHAT)
        respuesta = recortar(datos["respuesta"], 600)
        usados = [
            {"endpoint": u.get("endpoint", ""), "resumen": recortar(u.get("resumen", ""), 160)}
            for u in datos.get("datos_usados", [])
            if u.get("endpoint")
        ]
        if not usados:
            raise ValueError("el modelo no indicó datos_usados")
        fuente = "modelo"
    except Exception as e:
        logging.warning("chat con plantilla: %s", e)
        t0 = time.perf_counter()
        resumen = resumen_kpis(kpis)
        if resumen:
            respuesta = recortar(
                "No puedo generar una respuesta en lenguaje natural en este momento. "
                f"Estado actual de la flota: {resumen}.",
                600,
            )
            usados = [{"endpoint": "/kpis", "resumen": "estado general de la flota"}]
        else:
            respuesta = "No puedo responder ahora: la API de la operación no está disponible."
            usados = []
        ms = int((time.perf_counter() - t0) * 1000)
        fuente = "plantilla"

    return jsonify({"respuesta": respuesta, "datos_usados": usados, "fuente": fuente, "latencia_ms": ms})


@app.errorhandler(404)
def no_encontrado(_):
    return jsonify({"error": "ruta no encontrada"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

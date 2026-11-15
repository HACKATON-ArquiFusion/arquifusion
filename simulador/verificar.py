import argparse
import csv
import datetime as dt
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGOS_PATH = os.path.join(BASE_DIR, "contratos", "catalogos.json")
SALIDA_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salida")

CAMPOS_TELEMETRIA_CONTRATO = [
    "maquina_id", "ts", "modelo", "zona", "lat", "lon", "horas_op", "temp", "vib",
    "presion", "combustible_lph", "carga_t", "velocidad", "rpm", "estado",
    "contexto_clima", "contexto_terreno", "contexto_lote_combustible",
]

CAMPOS_ENTRENAMIENTO = [
    "etiqueta_falla_7d", "dias_a_falla", "componente_falla", "escenario", "grupo_id", "falla_id",
]

CAMPOS_TELEMETRIA = CAMPOS_TELEMETRIA_CONTRATO + CAMPOS_ENTRENAMIENTO

CAMPOS_FALLAS = [
    "falla_id", "maquina_id", "zona", "componente", "escenario",
    "ts_inicio_deriva", "ts_falla", "ts_reparacion", "grupo_id", "causa_verdadera",
]

CAMPOS_INCIDENTES = [
    "incidente_id", "maquina_id", "ts", "severidad", "estado", "descripcion",
]

CAMPOS_MANTENIMIENTOS = [
    "mantenimiento_id", "maquina_id", "ts", "tipo_mantenimiento", "estado", "componentes",
]

CAMPOS_CONSUMOS = [
    "consumo_id", "maquina_id", "ts", "componente", "cantidad", "unidad",
]

ESCENARIOS_FALLA = {"deriva_conjunta", "cluster_zona"}
ESCENARIOS_TELEMETRIA = {
    "sobreuso", "inactividad", "combustible_creciente", "anomalias_benignas",
    "deriva_conjunta", "cluster_zona",
}
SEVERIDADES = {"baja", "media", "alta", "critica"}
ESTADOS_INCIDENTE = {"abierto", "en_proceso", "cerrado"}
TIPOS_MANTENIMIENTO = {"preventivo", "correctivo"}
ESTADOS_MANTENIMIENTO = {"programado", "en_proceso", "completado"}
ESTADOS_MAQUINA = {"operativa", "detenida", "requiere_intervencion"}
UNIDADES = {"unidad", "litro", "kg"}

RANGOS_NUMERICOS = {
    "lat": (-90.0, 90.0),
    "lon": (-180.0, 180.0),
    "horas_op": (0.0, 60000.0),
    "temp": (-20.0, 150.0),
    "vib": (0.0, 30.0),
    "presion": (0.0, 400.0),
    "combustible_lph": (0.0, 150.0),
    "carga_t": (0.0, 200.0),
    "velocidad": (0.0, 100.0),
    "rpm": (0.0, 3000.0),
}


def cargar_catalogos():
    with open(CATALOGOS_PATH, "r", encoding="utf-8") as archivo:
        return json.load(archivo)


def _leer_csv(ruta):
    with open(ruta, newline="", encoding="utf-8") as archivo:
        lector = csv.DictReader(archivo)
        campos = lector.fieldnames or []
        filas = list(lector)
    return campos, filas


def _parse_ts(valor):
    if not valor:
        return None
    texto = valor.strip()
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        ts = dt.datetime.fromisoformat(texto)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return ts.astimezone(dt.timezone.utc)


def _num(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _maquina_valida(valor):
    return bool(
        isinstance(valor, str)
        and len(valor) == 5
        and valor.startswith("M-")
        and valor[2:].isdigit()
    )


def _faltantes(campos, requeridos):
    return [campo for campo in requeridos if campo not in campos]


def main():
    parser = argparse.ArgumentParser(description="Verifica la coherencia de los CSV generados")
    parser.add_argument("--salida", default=SALIDA_DEFECTO)
    args = parser.parse_args()

    salida = args.salida
    print("Verificacion de: %s" % salida)
    if not os.path.isdir(salida):
        print("ERROR: no existe el directorio de salida")
        return 1

    rutas = {
        "telemetria.csv": os.path.join(salida, "telemetria.csv"),
        "fallas.csv": os.path.join(salida, "fallas.csv"),
        "incidentes.csv": os.path.join(salida, "incidentes.csv"),
        "mantenimientos.csv": os.path.join(salida, "mantenimientos.csv"),
        "consumo_repuestos.csv": os.path.join(salida, "consumo_repuestos.csv"),
    }
    faltan_archivos = [nombre for nombre, ruta in rutas.items() if not os.path.exists(ruta)]
    if faltan_archivos:
        print("ERROR: faltan archivos: %s" % ", ".join(faltan_archivos))
        return 1

    catalogos = cargar_catalogos()
    zonas = {z["id"] for z in catalogos["zonas"]}
    modelos = {m["id"] for m in catalogos["modelos_maquina"]}
    componentes = set(catalogos["componentes"])
    climas = set(catalogos["clima"])
    terrenos = set(catalogos["terreno"])

    datos = {}
    campos = {}
    for nombre, ruta in rutas.items():
        campos[nombre], datos[nombre] = _leer_csv(ruta)

    errores = []

    def error(mensaje):
        errores.append(mensaje)

    for nombre, requeridos in [
        ("telemetria.csv", CAMPOS_TELEMETRIA),
        ("fallas.csv", CAMPOS_FALLAS),
        ("incidentes.csv", CAMPOS_INCIDENTES),
        ("mantenimientos.csv", CAMPOS_MANTENIMIENTOS),
        ("consumo_repuestos.csv", CAMPOS_CONSUMOS),
    ]:
        faltan = _faltantes(campos[nombre], requeridos)
        if faltan:
            error("%s: faltan campos %s" % (nombre, ", ".join(faltan)))

    telemetria = datos["telemetria.csv"]
    maquinas = set()
    timestamps = []
    claves_telemetria = set()
    positivos = 0
    escenarios_telemetria = {}

    if not _faltantes(campos["telemetria.csv"], CAMPOS_TELEMETRIA):
        for numero, fila in enumerate(telemetria, start=2):
            for campo in CAMPOS_TELEMETRIA_CONTRATO:
                if not (fila.get(campo) or "").strip():
                    error("telemetria.csv:%d: campo vacio %s" % (numero, campo))
            maquina_id = fila.get("maquina_id", "")
            if not _maquina_valida(maquina_id):
                error("telemetria.csv:%d: maquina_id invalido %s" % (numero, maquina_id))
            else:
                maquinas.add(maquina_id)

            ts = _parse_ts(fila.get("ts", ""))
            if ts is None:
                error("telemetria.csv:%d: ts invalido" % numero)
            else:
                timestamps.append(ts)

            if fila.get("modelo") not in modelos:
                error("telemetria.csv:%d: modelo fuera de catalogo" % numero)
            if fila.get("zona") not in zonas:
                error("telemetria.csv:%d: zona fuera de catalogo" % numero)
            if fila.get("contexto_clima") not in climas:
                error("telemetria.csv:%d: clima fuera de catalogo" % numero)
            if fila.get("contexto_terreno") not in terrenos:
                error("telemetria.csv:%d: terreno fuera de catalogo" % numero)
            if fila.get("estado") not in ESTADOS_MAQUINA:
                error("telemetria.csv:%d: estado invalido" % numero)

            etiqueta = fila.get("etiqueta_falla_7d", "")
            if etiqueta not in ("0", "1"):
                error("telemetria.csv:%d: etiqueta_falla_7d invalida" % numero)
            else:
                if etiqueta == "1":
                    positivos += 1
                    for campo in ("dias_a_falla", "componente_falla", "falla_id", "escenario"):
                        if not (fila.get(campo) or "").strip():
                            error("telemetria.csv:%d: etiqueta 1 sin %s" % (numero, campo))
                    dias = _num(fila.get("dias_a_falla"))
                    if dias is None or not 0.0 <= dias <= 7.0:
                        error("telemetria.csv:%d: dias_a_falla invalido" % numero)
                    if fila.get("componente_falla") not in componentes:
                        error("telemetria.csv:%d: componente_falla fuera de catalogo" % numero)
                    if fila.get("escenario") not in ESCENARIOS_FALLA:
                        error("telemetria.csv:%d: escenario de falla invalido" % numero)
                    if fila.get("escenario") == "cluster_zona" and not (fila.get("grupo_id") or "").strip():
                        error("telemetria.csv:%d: cluster sin grupo_id" % numero)

            escenario = fila.get("escenario", "")
            if escenario:
                if escenario not in ESCENARIOS_TELEMETRIA:
                    error("telemetria.csv:%d: escenario desconocido %s" % (numero, escenario))
                escenarios_telemetria[escenario] = escenarios_telemetria.get(escenario, 0) + 1
                if escenario == "anomalias_benignas" and etiqueta == "1":
                    error("telemetria.csv:%d: anomalia benigna con etiqueta 1" % numero)

            for campo, (minimo, maximo) in RANGOS_NUMERICOS.items():
                valor = _num(fila.get(campo))
                if valor is None:
                    error("telemetria.csv:%d: %s no numerico" % (numero, campo))
                elif not minimo <= valor <= maximo:
                    error("telemetria.csv:%d: %s fuera de rango" % (numero, campo))

            if ts is not None and maquina_id:
                clave = (maquina_id, fila.get("ts", ""))
                if clave in claves_telemetria:
                    error("telemetria.csv:%d: clave duplicada maquina_id+ts" % numero)
                else:
                    claves_telemetria.add(clave)

    fallas = datos["fallas.csv"]
    fallas_por_escenario = {}
    fallas_coherencia = []
    ids_falla = set()
    if not _faltantes(campos["fallas.csv"], CAMPOS_FALLAS):
        for numero, fila in enumerate(fallas, start=2):
            for campo in CAMPOS_FALLAS:
                if campo == "grupo_id" and fila.get("escenario") == "deriva_conjunta":
                    continue
                if not (fila.get(campo) or "").strip():
                    error("fallas.csv:%d: campo vacio %s" % (numero, campo))
            if fila.get("maquina_id") not in maquinas:
                error("fallas.csv:%d: maquina_id no existe en telemetria" % numero)
            if fila.get("zona") not in zonas:
                error("fallas.csv:%d: zona fuera de catalogo" % numero)
            if fila.get("componente") not in componentes:
                error("fallas.csv:%d: componente fuera de catalogo" % numero)
            escenario = fila.get("escenario", "")
            if escenario not in ESCENARIOS_FALLA:
                error("fallas.csv:%d: escenario invalido" % numero)
            else:
                fallas_por_escenario[escenario] = fallas_por_escenario.get(escenario, 0) + 1
            if escenario == "cluster_zona" and not (fila.get("grupo_id") or "").strip():
                error("fallas.csv:%d: cluster sin grupo_id" % numero)
            falla_id = fila.get("falla_id", "")
            if falla_id in ids_falla:
                error("fallas.csv:%d: falla_id duplicado" % numero)
            else:
                ids_falla.add(falla_id)
            inicio = _parse_ts(fila.get("ts_inicio_deriva", ""))
            falla = _parse_ts(fila.get("ts_falla", ""))
            reparacion = _parse_ts(fila.get("ts_reparacion", ""))
            if inicio is None or falla is None or reparacion is None:
                error("fallas.csv:%d: timestamps invalidos" % numero)
            elif not inicio <= falla <= reparacion:
                error("fallas.csv:%d: orden temporal invalido" % numero)
            fallas_coherencia.append((
                fila.get("maquina_id", ""),
                fila.get("componente", ""),
                fila.get("ts_falla", ""),
                fila.get("ts_reparacion", ""),
            ))

    incidentes = datos["incidentes.csv"]
    incidentes_clave = set()
    ids_incidente = set()
    if not _faltantes(campos["incidentes.csv"], CAMPOS_INCIDENTES):
        for numero, fila in enumerate(incidentes, start=2):
            for campo in CAMPOS_INCIDENTES:
                if not (fila.get(campo) or "").strip():
                    error("incidentes.csv:%d: campo vacio %s" % (numero, campo))
            if fila.get("maquina_id") not in maquinas:
                error("incidentes.csv:%d: maquina_id no existe en telemetria" % numero)
            if _parse_ts(fila.get("ts", "")) is None:
                error("incidentes.csv:%d: ts invalido" % numero)
            if fila.get("severidad") not in SEVERIDADES:
                error("incidentes.csv:%d: severidad invalida" % numero)
            if fila.get("estado") not in ESTADOS_INCIDENTE:
                error("incidentes.csv:%d: estado invalido" % numero)
            incidente_id = fila.get("incidente_id", "")
            if incidente_id in ids_incidente:
                error("incidentes.csv:%d: incidente_id duplicado" % numero)
            else:
                ids_incidente.add(incidente_id)
            incidentes_clave.add((fila.get("maquina_id", ""), fila.get("ts", "")))

    mantenimientos = datos["mantenimientos.csv"]
    mantenimientos_clave = set()
    ids_mantenimiento = set()
    preventivos = 0
    correctivos = 0
    if not _faltantes(campos["mantenimientos.csv"], CAMPOS_MANTENIMIENTOS):
        for numero, fila in enumerate(mantenimientos, start=2):
            for campo in CAMPOS_MANTENIMIENTOS:
                if not (fila.get(campo) or "").strip():
                    error("mantenimientos.csv:%d: campo vacio %s" % (numero, campo))
            if fila.get("maquina_id") not in maquinas:
                error("mantenimientos.csv:%d: maquina_id no existe en telemetria" % numero)
            if _parse_ts(fila.get("ts", "")) is None:
                error("mantenimientos.csv:%d: ts invalido" % numero)
            tipo = fila.get("tipo_mantenimiento", "")
            if tipo not in TIPOS_MANTENIMIENTO:
                error("mantenimientos.csv:%d: tipo_mantenimiento invalido" % numero)
            if fila.get("estado") not in ESTADOS_MANTENIMIENTO:
                error("mantenimientos.csv:%d: estado invalido" % numero)
            if tipo == "preventivo":
                preventivos += 1
            elif tipo == "correctivo":
                correctivos += 1
            componentes_mantenimiento = [c for c in (fila.get("componentes") or "").split(";") if c]
            if not componentes_mantenimiento:
                error("mantenimientos.csv:%d: componentes vacio" % numero)
            for componente in componentes_mantenimiento:
                if componente not in componentes:
                    error("mantenimientos.csv:%d: componente fuera de catalogo" % numero)
                mantenimientos_clave.add((
                    fila.get("maquina_id", ""),
                    fila.get("ts", ""),
                    tipo,
                    componente,
                ))
            mantenimiento_id = fila.get("mantenimiento_id", "")
            if mantenimiento_id in ids_mantenimiento:
                error("mantenimientos.csv:%d: mantenimiento_id duplicado" % numero)
            else:
                ids_mantenimiento.add(mantenimiento_id)

    consumos = datos["consumo_repuestos.csv"]
    consumos_clave = set()
    ids_consumo = set()
    picos_consumo = 0
    componentes_pico = set()
    zonas_pico = set()
    maquina_zona = {}
    for fila in telemetria:
        maquina_zona.setdefault(fila.get("maquina_id", ""), fila.get("zona", ""))
    if not _faltantes(campos["consumo_repuestos.csv"], CAMPOS_CONSUMOS):
        for numero, fila in enumerate(consumos, start=2):
            for campo in CAMPOS_CONSUMOS:
                if not (fila.get(campo) or "").strip():
                    error("consumo_repuestos.csv:%d: campo vacio %s" % (numero, campo))
            if fila.get("maquina_id") not in maquinas:
                error("consumo_repuestos.csv:%d: maquina_id no existe en telemetria" % numero)
            if _parse_ts(fila.get("ts", "")) is None:
                error("consumo_repuestos.csv:%d: ts invalido" % numero)
            componente = fila.get("componente", "")
            if componente not in componentes:
                error("consumo_repuestos.csv:%d: componente fuera de catalogo" % numero)
            cantidad = _num(fila.get("cantidad"))
            if cantidad is None or cantidad <= 0:
                error("consumo_repuestos.csv:%d: cantidad invalida" % numero)
            elif cantidad >= 3:
                picos_consumo += 1
                componentes_pico.add(componente)
                zonas_pico.add(maquina_zona.get(fila.get("maquina_id", ""), ""))
            if fila.get("unidad") not in UNIDADES:
                error("consumo_repuestos.csv:%d: unidad invalida" % numero)
            consumo_id = fila.get("consumo_id", "")
            if consumo_id in ids_consumo:
                error("consumo_repuestos.csv:%d: consumo_id duplicado" % numero)
            else:
                ids_consumo.add(consumo_id)
            consumos_clave.add((
                fila.get("maquina_id", ""),
                componente,
                fila.get("ts", ""),
            ))

    for maquina_id, componente, ts_falla, ts_reparacion in fallas_coherencia:
        if (maquina_id, ts_falla) not in incidentes_clave:
            error("coherencia: falla %s/%s sin incidente" % (maquina_id, ts_falla))
        if (maquina_id, ts_reparacion, "correctivo", componente) not in mantenimientos_clave:
            error("coherencia: falla %s/%s sin mantenimiento correctivo" % (maquina_id, ts_reparacion))
        if (maquina_id, componente, ts_reparacion) not in consumos_clave:
            error("coherencia: falla %s/%s sin consumo de repuesto" % (maquina_id, componente))

    if len(fallas) < 20:
        error("objetivo: se esperaban al menos 20 fallas y hay %d" % len(fallas))
    pct_positivos = (100.0 * positivos / len(telemetria)) if telemetria else 0.0
    if not 2.0 <= pct_positivos <= 8.0:
        error("objetivo: etiqueta_falla_7d=1 debe estar entre 2%% y 8%% y es %.2f%%" % pct_positivos)
    tamano_telemetria = os.path.getsize(rutas["telemetria.csv"])
    if tamano_telemetria >= 50 * 1024 * 1024:
        error("objetivo: telemetria.csv supera 50 MB")
    if preventivos == 0:
        error("objetivo: no hay mantenimientos preventivos")
    if picos_consumo == 0:
        error("objetivo: no hay picos de consumo de repuestos")

    print("")
    print("%-26s %10s" % ("Archivo", "Filas"))
    for nombre in rutas:
        print("%-26s %10d" % (nombre, len(datos[nombre])))
    if timestamps:
        print("Rango telemetria: %s a %s" % (
            min(timestamps).strftime("%Y-%m-%dT%H:%M:%SZ"),
            max(timestamps).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ))
    else:
        print("Rango telemetria: sin datos")
    print("Maquinas: %d" % len(maquinas))
    print("Fallas por escenario: %s" % (
        ", ".join("%s=%d" % (k, v) for k, v in sorted(fallas_por_escenario.items())) or "ninguna"
    ))
    print("Etiqueta_falla_7d=1: %d (%.2f%%)" % (positivos, pct_positivos))
    print("Escenarios en telemetria: %s" % (
        ", ".join("%s=%d" % (k, v) for k, v in sorted(escenarios_telemetria.items())) or "ninguno"
    ))
    print("Mantenimientos: preventivos=%d correctivos=%d" % (preventivos, correctivos))
    print("Picos de consumo (cantidad >= 3): %d | componentes=%d | zonas=%d" % (
        picos_consumo, len(componentes_pico), len(zonas_pico)
    ))
    print("Tamano telemetria: %.2f MB" % (tamano_telemetria / (1024.0 * 1024.0)))

    if errores:
        print("")
        print("ERRORES: %d" % len(errores))
        for mensaje in errores[:50]:
            print(" - %s" % mensaje)
        if len(errores) > 50:
            print(" - ...")
        return 1
    print("")
    print("OK: datos coherentes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

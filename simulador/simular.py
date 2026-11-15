import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modelo_flota import Flota, cargar_catalogos, generar_historico

SALIDA_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salida")


def _iso_utc(ts):
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cargar_env(ruta=".env"):
    if not os.path.exists(ruta):
        return
    with open(ruta, "r", encoding="utf-8") as archivo:
        for linea in archivo:
            texto = linea.strip()
            if not texto or texto.startswith("#") or "=" not in texto:
                continue
            clave, valor = texto.split("=", 1)
            clave = clave.strip()
            valor = valor.strip().strip('"').strip("'")
            if clave:
                os.environ.setdefault(clave, valor)


def _registro_contrato(fila):
    return {
        "maquina_id": fila["maquina_id"],
        "ts": fila["ts"],
        "modelo": fila["modelo"],
        "zona": fila["zona"],
        "lat": fila["lat"],
        "lon": fila["lon"],
        "horas_op": fila["horas_op"],
        "temp": fila["temp"],
        "vib": fila["vib"],
        "presion": fila["presion"],
        "combustible_lph": fila["combustible_lph"],
        "carga_t": fila["carga_t"],
        "velocidad": fila["velocidad"],
        "rpm": fila["rpm"],
        "estado": fila["estado"],
        "contexto": {
            "clima": fila["contexto_clima"],
            "terreno": fila["contexto_terreno"],
            "lote_combustible": fila["contexto_lote_combustible"],
        },
    }


def _enviar_lote(url, api_key, payload):
    cuerpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    espera = 1.0
    ultimo_error = ""
    for intento in range(4):
        solicitud = urllib.request.Request(url, data=cuerpo, method="POST")
        solicitud.add_header("Content-Type", "application/json")
        solicitud.add_header("x-api-key", api_key)
        try:
            with urllib.request.urlopen(solicitud, timeout=10) as respuesta:
                return respuesta.status, respuesta.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            ultimo_error = "HTTP %s" % exc.code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            ultimo_error = type(exc).__name__
        if intento < 3:
            time.sleep(espera)
            espera *= 2.0
    raise RuntimeError("envio fallido tras 3 reintentos: %s" % ultimo_error)


def _vivo(args):
    if args.n < 1:
        raise SystemExit("--n debe ser >= 1")
    if args.intervalo_seg < 0:
        raise SystemExit("--intervalo-seg debe ser >= 0")
    if args.max_ciclos < 1:
        raise SystemExit("--max-ciclos debe ser >= 1")
    if args.aceleracion <= 0:
        raise SystemExit("--aceleracion debe ser > 0")

    _cargar_env()
    url = None
    api_key = None
    if args.enviar:
        base = os.environ.get("AWS_API_URL", "").strip()
        api_key = os.environ.get("AWS_API_KEY", "").strip()
        if not base or not api_key:
            raise SystemExit("Faltan AWS_API_URL y/o AWS_API_KEY para usar --enviar")
        url = base.rstrip("/") + "/ingesta"

    catalogos = cargar_catalogos()
    ahora = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    flota = Flota(
        catalogos,
        n=args.n,
        dias=365,
        semilla=args.semilla,
        inicio=ahora,
        paso_min=1,
        tasa_falla=0.5,
    )
    flota.registrar_eventos = False
    if args.inyectar_falla:
        evento = flota.inyectar_deriva(args.inyectar_falla, ahora)
        if evento is None:
            raise SystemExit("--inyectar-falla: no existe la maquina %s" % args.inyectar_falla)

    print("Modo: %s" % ("envio real" if args.enviar else "dry-run (sin enviar)"))
    print("Maquinas: %d | ciclos: %d | intervalo: %.1fs | aceleracion: %gx" % (
        args.n, args.max_ciclos, args.intervalo_seg, args.aceleracion
    ))
    print("Reloj: 1s real = %g s simulados" % args.aceleracion)
    if args.enviar:
        print("Destino: AWS_API_URL/ingesta (oculto)")
    else:
        print("Destino: no se envia")
    print("")

    primer_payload = None
    total_registros = 0
    for ciclo in range(args.max_ciclos):
        if ciclo > 0:
            time.sleep(args.intervalo_seg)
        ts_actual = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        ts_sim = ahora + dt.timedelta(seconds=ciclo * args.intervalo_seg * args.aceleracion)
        if ts_sim >= flota.fin:
            ts_sim = flota.fin - flota.paso
        flota.paso_horas = (args.intervalo_seg * args.aceleracion) / 3600.0
        registros = []
        for maquina in flota.maquinas:
            fila = flota.leer(maquina, ts_sim)
            registro = _registro_contrato(fila)
            registro["ts"] = _iso_utc(ts_actual)
            registros.append(registro)
        total_registros += len(registros)
        lotes = [registros[i:i + 500] for i in range(0, len(registros), 500)]
        print("Ciclo %d/%d | ts=%s | sim=%s | registros=%d | lotes=%d" % (
            ciclo + 1, args.max_ciclos, _iso_utc(ts_actual), _iso_utc(ts_sim),
            len(registros), len(lotes)
        ))
        if args.inyectar_falla:
            muestra = next((r for r in registros if r["maquina_id"] == args.inyectar_falla), None)
            if muestra:
                print("  Inyectada %s: estado=%s temp=%.2f vib=%.2f" % (
                    args.inyectar_falla, muestra["estado"], muestra["temp"], muestra["vib"]
                ))
        if primer_payload is None and lotes:
            primer_payload = {"tipo": "telemetria", "registros": lotes[0]}
            print("Primer payload:")
            print(json.dumps(primer_payload, ensure_ascii=False, indent=2))
            print("")
        if args.enviar:
            for indice, lote in enumerate(lotes, start=1):
                payload = {"tipo": "telemetria", "registros": lote}
                print("Enviando lote %d/%d (%d registros)" % (indice, len(lotes), len(lote)))
                estado, cuerpo = _enviar_lote(url, api_key, payload)
                print("Respuesta HTTP %s" % estado)
                if cuerpo:
                    print("Respuesta: %s" % cuerpo[:500])
    print("Fin: ciclos=%d | registros generados=%d | modo=%s" % (
        args.max_ciclos, total_registros, "envio real" if args.enviar else "dry-run"
    ))
    return 0


def _historico(args):
    if args.n < 1:
        raise SystemExit("--n debe ser >= 1")
    if args.dias < 1:
        raise SystemExit("--dias debe ser >= 1")
    if args.paso_min < 1:
        raise SystemExit("--paso-min debe ser >= 1")
    if not 0.0 <= args.tasa_falla <= 1.0:
        raise SystemExit("--tasa-falla debe estar entre 0 y 1")
    catalogos = cargar_catalogos()
    resumen = generar_historico(
        catalogos,
        n=args.n,
        dias=args.dias,
        semilla=args.semilla,
        salida=args.salida,
        paso_min=args.paso_min,
        tasa_falla=args.tasa_falla,
        inicio=args.inicio,
    )
    print("Salida: %s" % resumen["salida"])
    print("Rango: %s a %s" % (resumen["fecha_inicio"], resumen["fecha_fin"]))
    print("Filas: %s" % ", ".join("%s=%d" % (k, v) for k, v in resumen["filas_por_archivo"].items()))
    if resumen["fallas_por_escenario"]:
        print("Fallas por escenario: %s" % ", ".join(
            "%s=%d" % (k, v) for k, v in sorted(resumen["fallas_por_escenario"].items())
        ))
    else:
        print("Fallas por escenario: ninguna")
    print("Maquinas con falla: %d | etiqueta_falla_7d=1: %.1f%%" % (
        resumen["maquinas_con_falla"], resumen["pct_positivos"]
    ))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Simulador de telemetria de flota ArquiFusion")
    sub = parser.add_subparsers(dest="comando", required=True)
    historico = sub.add_parser(
        "historico",
        help="Genera CSV historicos de telemetria, fallas, incidentes, mantenimientos y consumo de repuestos",
    )
    historico.add_argument("--n", type=int, default=50)
    historico.add_argument("--dias", type=int, default=90)
    historico.add_argument("--semilla", type=int, default=42)
    historico.add_argument("--salida", default=SALIDA_DEFECTO)
    historico.add_argument("--paso-min", type=int, default=60)
    historico.add_argument("--tasa-falla", type=float, default=0.5)
    historico.add_argument("--inicio", default=None)
    historico.set_defaults(func=_historico)

    vivo = sub.add_parser("vivo", help="Genera lecturas en vivo (dry-run por defecto)")
    vivo.add_argument("--n", type=int, default=50)
    vivo.add_argument("--intervalo-seg", type=float, default=5.0)
    vivo.add_argument("--max-ciclos", type=int, default=5)
    vivo.add_argument("--aceleracion", type=float, default=1.0)
    vivo.add_argument("--inyectar-falla", default=None)
    vivo.add_argument("--enviar", action="store_true")
    vivo.add_argument("--semilla", type=int, default=42)
    vivo.set_defaults(func=_vivo)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

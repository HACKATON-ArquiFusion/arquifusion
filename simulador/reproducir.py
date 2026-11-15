"""Reproduce una ventana real del historico (CSV) para la demo.

  python simulador/reproducir.py historial   --falla F-0065 --n 20   (carga previa para A)
  python simulador/reproducir.py reproducir  --falla F-0065 --n 20   (una hora por ciclo)

Sin --enviar es dry-run. Con --enviar usa AWS_API_URL y AWS_API_KEY del entorno
y hace POST a {AWS_API_URL}/ingesta con el header x-api-key.
Los ts son los ORIGINALES del historico: BigQuery los toma como duplicados y no se ensucia.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salida")
HORA = dt.timedelta(hours=1)
LOTE = 500
HISTORIAL_H = 336
NUMEROS = ["lat", "lon", "horas_op", "temp", "vib", "presion",
           "combustible_lph", "carga_t", "velocidad"]


def a_fecha(texto):
    d = dt.datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def hora_inferior(d):
    return d.replace(minute=0, second=0, microsecond=0)


def numero(valor):
    return None if valor in ("", None) else float(valor)


def registro(fila):
    """Fila del CSV -> registro del contrato (sin etiquetas de entrenamiento)."""
    reg = {
        "maquina_id": fila["maquina_id"],
        "ts": iso(a_fecha(fila["ts"])),
        "modelo": fila["modelo"],
        "zona": fila["zona"],
    }
    for campo in NUMEROS:
        reg[campo] = numero(fila[campo])
    reg["rpm"] = None if fila["rpm"] == "" else int(float(fila["rpm"]))
    reg["estado"] = fila["estado"]
    reg["contexto"] = {
        "clima": fila["contexto_clima"],
        "terreno": fila["contexto_terreno"],
        "lote_combustible": fila["contexto_lote_combustible"],
    }
    return reg


def preparar(args):
    with open(os.path.join(SALIDA, "fallas.csv"), encoding="utf-8-sig", newline="") as f:
        fallas = list(csv.DictReader(f))
    objetivo = next((x for x in fallas if x["falla_id"] == args.falla), None)
    if objetivo is None:
        sys.exit("No existe la falla %s en fallas.csv" % args.falla)

    ts_falla = a_fecha(objetivo["ts_falla"])
    t_fin = hora_inferior(ts_falla) + HORA
    t0 = hora_inferior(ts_falla) - dt.timedelta(hours=args.antes_h)
    t_hist = t0 - dt.timedelta(hours=HISTORIAL_H)

    # Maquinas con otra falla (o su deriva) en la ventana: se descartan para que el resto sea sano.
    ocupadas = set()
    for x in fallas:
        if x["maquina_id"] == objetivo["maquina_id"]:
            continue
        if a_fecha(x["ts_inicio_deriva"]) <= t_fin and a_fecha(x["ts_falla"]) >= t_hist:
            ocupadas.add(x["maquina_id"])

    filas = []
    with open(os.path.join(SALIDA, "telemetria.csv"), encoding="utf-8-sig", newline="") as f:
        for fila in csv.DictReader(f):
            d = a_fecha(fila["ts"])
            if t_hist <= d < t_fin:
                filas.append((d, fila))

    elegidas = [objetivo["maquina_id"]]
    for m in sorted({fila["maquina_id"] for _, fila in filas}):
        if len(elegidas) >= args.n:
            break
        if m not in ocupadas and m not in elegidas:
            elegidas.append(m)
    conjunto = set(elegidas)

    historial, ciclos = [], {}
    for d, fila in filas:
        if fila["maquina_id"] not in conjunto:
            continue
        if d < t0:
            historial.append((d, registro(fila)))
        else:
            ciclos.setdefault(d, []).append(registro(fila))
    historial.sort(key=lambda par: (par[0], par[1]["maquina_id"]))
    return objetivo, elegidas, t0, t_fin, [r for _, r in historial], dict(sorted(ciclos.items()))


def enviar(registros, url, clave):
    cuerpo = json.dumps({"registros": registros}).encode("utf-8")
    pedido = urllib.request.Request(
        url + "/ingesta", data=cuerpo, method="POST",
        headers={"Content-Type": "application/json", "x-api-key": clave},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=30) as r:
            return r.status, r.read().decode("utf-8")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")[:300]


def main():
    ap = argparse.ArgumentParser(description="Reproduce una ventana real del historico")
    ap.add_argument("modo", choices=["historial", "reproducir"])
    ap.add_argument("--falla", default="F-0065")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--antes-h", type=int, default=74, help="horas de reproduccion antes de la falla")
    ap.add_argument("--intervalo-seg", type=float, default=1.0)
    ap.add_argument("--enviar", action="store_true")
    args = ap.parse_args()

    url = os.environ.get("AWS_API_URL", "").strip().rstrip("/")
    clave = os.environ.get("AWS_API_KEY", "").strip()
    if args.enviar and not (url and clave):
        sys.exit("Faltan AWS_API_URL o AWS_API_KEY en el entorno")

    objetivo, elegidas, t0, t_fin, historial, ciclos = preparar(args)
    print("Modo: %s | %s" % (args.modo, "envio real" if args.enviar else "dry-run (sin enviar)"))
    print("Falla %s: %s (%s) falla el %s" % (
        objetivo["falla_id"], objetivo["maquina_id"], objetivo["componente"], objetivo["ts_falla"]))
    print("Maquinas: %d | historial: %d lecturas | ciclos: %d (de %s a %s)" % (
        len(elegidas), len(historial), len(ciclos), iso(t0), iso(t_fin - HORA)))
    print("")

    if args.modo == "historial":
        lotes = [historial[i:i + LOTE] for i in range(0, len(historial), LOTE)]
        for i, lote in enumerate(lotes, start=1):
            if args.enviar:
                estado, cuerpo = enviar(lote, url, clave)
                print("Lote %d/%d (%d registros) -> HTTP %s %s" % (i, len(lotes), len(lote), estado, cuerpo))
                if estado >= 300:
                    sys.exit("Se detiene: respuesta no exitosa")
            else:
                print("Lote %d/%d (%d registros)" % (i, len(lotes), len(lote)))
        if not args.enviar and historial:
            print("\nEjemplo de registro:")
            print(json.dumps(historial[-1], ensure_ascii=False, indent=2))
        return

    for i, (d, regs) in enumerate(ciclos.items(), start=1):
        obj = next((r for r in regs if r["maquina_id"] == objetivo["maquina_id"]), None)
        extra = ""
        if obj:
            extra = " | %s: temp=%s vib=%s estado=%s" % (obj["maquina_id"], obj["temp"], obj["vib"], obj["estado"])
        linea = "Ciclo %d/%d | ts=%s | registros=%d%s" % (i, len(ciclos), iso(d), len(regs), extra)
        if args.enviar:
            estado, cuerpo = enviar(regs, url, clave)
            print(linea + " -> HTTP %s" % estado)
            if estado >= 300:
                sys.exit("Se detiene: %s" % cuerpo)
            time.sleep(args.intervalo_seg)
        else:
            print(linea)
    print("\nFin de la reproduccion")


if __name__ == "__main__":
    main()
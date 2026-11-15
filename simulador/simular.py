import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modelo_flota import cargar_catalogos, generar_historico

SALIDA_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salida")


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
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

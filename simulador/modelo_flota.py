import csv
import datetime as dt
import json
import math
import os
import random

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGOS_PATH = os.path.join(BASE_DIR, "contratos", "catalogos.json")

BASE_MODELO = {
    "EXC-320": {"temp": 90.0, "vib": 5.2, "presion": 210.0, "combustible_lph": 30.0, "carga_t": 2.0, "velocidad": 3.0, "rpm": 1500.0},
    "CAM-730": {"temp": 94.0, "vib": 6.2, "presion": 185.0, "combustible_lph": 50.0, "carga_t": 82.0, "velocidad": 24.0, "rpm": 1850.0},
    "CAR-950": {"temp": 92.0, "vib": 5.8, "presion": 200.0, "combustible_lph": 40.0, "carga_t": 14.0, "velocidad": 11.0, "rpm": 1700.0},
    "TRC-D8": {"temp": 93.0, "vib": 6.6, "presion": 195.0, "combustible_lph": 44.0, "carga_t": 6.0, "velocidad": 7.0, "rpm": 1600.0},
    "PER-ROC": {"temp": 89.0, "vib": 5.0, "presion": 230.0, "combustible_lph": 26.0, "carga_t": 1.0, "velocidad": 2.0, "rpm": 1400.0},
}

CAMPOS_TELEMETRIA = [
    "maquina_id", "ts", "modelo", "zona", "lat", "lon", "horas_op", "temp", "vib",
    "presion", "combustible_lph", "carga_t", "velocidad", "rpm", "estado",
    "contexto_clima", "contexto_terreno", "contexto_lote_combustible",
    "etiqueta_falla_7d", "dias_a_falla", "componente_falla", "escenario", "grupo_id", "falla_id",
]

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

MANTENIMIENTO_CADA_H = 500.0

COMPONENTES_PREVENTIVO = {
    "EXC-320": ["filtros", "sistema_hidraulico", "motor"],
    "CAM-730": ["filtros", "frenos", "motor"],
    "CAR-950": ["filtros", "transmision", "frenos"],
    "TRC-D8": ["filtros", "tren_rodaje", "motor"],
    "PER-ROC": ["filtros", "refrigeracion", "motor"],
}

PROB_CAMBIO_CLIMA = 0.12
PROB_CAMBIO_TERRENO = 0.03


def cargar_catalogos():
    with open(CATALOGOS_PATH, "r", encoding="utf-8") as archivo:
        return json.load(archivo)


def _clamp(valor, minimo, maximo):
    return max(minimo, min(maximo, valor))


def _iso(ts):
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_inicio(valor, dias):
    if valor:
        texto = valor.strip()
        if texto.endswith("Z"):
            texto = texto[:-1] + "+00:00"
        try:
            ts = dt.datetime.fromisoformat(texto)
        except ValueError:
            raise SystemExit("--inicio invalido: use ISO 8601, por ejemplo 2026-11-01 o 2026-11-01T00:00:00Z")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone.utc)
        return ts.astimezone(dt.timezone.utc)
    ahora = dt.datetime.now(dt.timezone.utc)
    base = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    return base - dt.timedelta(days=dias)


def _progreso(inicio, fin, ts):
    total = (fin - inicio).total_seconds()
    if total <= 0:
        return 0.0
    return _clamp((ts - inicio).total_seconds() / total, 0.0, 1.0)


def _efecto_contexto(clima, terreno, lote, lotes_malos):
    temp = 0.0
    vib = 0.0
    fuel = 1.0
    if terreno == "pendiente":
        temp += 4.0
        fuel *= 1.15
    elif terreno == "barro":
        temp += 3.0
        fuel *= 1.18
        vib += 0.5
    elif terreno == "rocoso":
        temp += 2.0
        vib += 1.2
    if clima == "soleado":
        temp += 2.0
    elif clima == "lluvia":
        temp -= 3.0
        vib += 0.8
    elif clima == "polvo":
        temp += 1.0
        vib += 1.5
    elif clima == "frio":
        temp -= 6.0
        fuel *= 1.05
    if lote in lotes_malos:
        fuel *= 1.10
        vib += 0.8
    return temp, vib, fuel


def _degradacion_cluster(tipo, valor, p):
    temp = 0.0
    vib = 0.0
    fuel = 1.0
    if tipo == "clima":
        if valor == "polvo":
            temp += 1.0 * p
            vib += 3.5 * p
        elif valor == "lluvia":
            temp -= 2.0 * p
            vib += 3.0 * p
        elif valor == "frio":
            temp -= 4.0 * p
            fuel *= 1.0 + 0.05 * p
        elif valor == "soleado":
            temp += 3.0 * p
    elif tipo == "terreno":
        if valor == "rocoso":
            temp += 3.0 * p
            vib += 4.0 * p
        elif valor == "pendiente":
            temp += 5.0 * p
            fuel *= 1.0 + 0.22 * p
        elif valor == "barro":
            temp += 4.0 * p
            fuel *= 1.0 + 0.25 * p
            vib += 1.0 * p
    elif tipo == "lote_combustible":
        temp += 1.5 * p
        vib += 2.5 * p
        fuel *= 1.0 + 0.22 * p
    return temp, vib, fuel


class Maquina:
    def __init__(self, indice, modelo, zona, rng):
        self.id = "M-%03d" % (indice + 1)
        self.modelo = modelo
        self.zona = zona["id"]
        self.zona_lat = zona["lat"]
        self.zona_lon = zona["lon"]
        self.lat = zona["lat"] + rng.uniform(-0.0025, 0.0025)
        self.lon = zona["lon"] + rng.uniform(-0.0025, 0.0025)
        self.horas_op = rng.uniform(1500.0, 32000.0)
        self.turno_offset = rng.uniform(-1.0, 1.0)
        self.escenario_base = "normal"
        self.clima = rng.choice(["soleado", "soleado", "polvo", "lluvia", "frio"])
        self.terreno = rng.choice(["llano", "llano", "pendiente", "rocoso", "barro"])
        self.lote = None
        self.lote_cambio = None
        self.ultimo_dia = None
        self.temp = BASE_MODELO[modelo]["temp"] + rng.uniform(-3.0, 3.0)
        self.vib = BASE_MODELO[modelo]["vib"] + rng.uniform(-0.5, 0.5)
        self.fallas = []
        self.anomalias = []
        self.proximo_mantenimiento = None
        self.combustible_inicio = None
        self.combustible_tasa = 0.0


class Flota:
    def __init__(self, catalogos, n, dias, semilla, inicio, paso_min=60, tasa_falla=0.5):
        self.catalogos = catalogos
        self.rng = random.Random(semilla)
        self.n = n
        self.dias = dias
        self.inicio = inicio
        self.fin = inicio + dt.timedelta(days=dias)
        self.paso_min = paso_min
        self.paso = dt.timedelta(minutes=paso_min)
        self.paso_horas = paso_min / 60.0
        self.componentes = list(catalogos["componentes"])
        self.lotes_por_mes = {}
        self.lotes = []
        self.lotes_malos = set()
        self.fallas = []
        self.incidentes = []
        self.mantenimientos = []
        self.consumos_repuestos = []
        self.registrar_eventos = True
        self._falla_seq = 0
        self._grupo_seq = 0
        self._incidente_seq = 0
        self._mantenimiento_seq = 0
        self._consumo_seq = 0
        self._preparar_lotes()
        self.maquinas = self._crear_maquinas()
        self._planificar(tasa_falla)

    def _preparar_lotes(self):
        y = self.inicio.year
        m = self.inicio.month
        fin = self.fin - dt.timedelta(seconds=1)
        while (y, m) <= (fin.year, fin.month):
            self.lotes_por_mes[(y, m)] = ["LC-%04d-%02d-%s" % (y, m, c) for c in ["A", "B", "C"]]
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        for lista in self.lotes_por_mes.values():
            self.lotes.extend(lista)
        cantidad = 1 if len(self.lotes) <= 3 else 2
        self.lotes_malos = set(self.rng.sample(self.lotes, min(cantidad, len(self.lotes))))

    def _crear_maquinas(self):
        zonas = [z["id"] for z in self.catalogos["zonas"]]
        modelos = [m["id"] for m in self.catalogos["modelos_maquina"]]
        asignacion_zonas = [zonas[i % len(zonas)] for i in range(self.n)]
        asignacion_modelos = [modelos[i % len(modelos)] for i in range(self.n)]
        self.rng.shuffle(asignacion_zonas)
        self.rng.shuffle(asignacion_modelos)
        zonas_por_id = {z["id"]: z for z in self.catalogos["zonas"]}
        maquinas = []
        for i in range(self.n):
            maquinas.append(Maquina(i, asignacion_modelos[i], zonas_por_id[asignacion_zonas[i]], self.rng))
        for maquina in maquinas:
            self._elegir_lote(maquina, self.inicio)
            base = math.floor(maquina.horas_op / MANTENIMIENTO_CADA_H) * MANTENIMIENTO_CADA_H
            maquina.proximo_mantenimiento = base + MANTENIMIENTO_CADA_H
            while maquina.proximo_mantenimiento <= maquina.horas_op:
                maquina.proximo_mantenimiento += MANTENIMIENTO_CADA_H
        return maquinas

    def _elegir_lote(self, maquina, ts):
        opciones = self.lotes_por_mes.get((ts.year, ts.month), self.lotes)
        if maquina.lote is None or self.rng.random() < 0.30:
            maquina.lote = self.rng.choice(opciones)
        maquina.lote_cambio = ts + dt.timedelta(days=self.rng.uniform(8.0, 15.0))

    def _actualizar_contexto(self, maquina, ts):
        if ts >= maquina.lote_cambio:
            self._elegir_lote(maquina, ts)
        dia = ts.date()
        if maquina.ultimo_dia != dia:
            if self.rng.random() < PROB_CAMBIO_CLIMA:
                maquina.clima = self.rng.choice(self.catalogos["clima"])
            if self.rng.random() < PROB_CAMBIO_TERRENO:
                maquina.terreno = self.rng.choice(self.catalogos["terreno"])
            maquina.ultimo_dia = dia

    def _planificar(self, tasa_falla):
        indices = list(range(self.n))
        self.rng.shuffle(indices)
        cantidad_sobreuso = max(1, int(round(self.n * 0.15))) if self.n >= 3 else 0
        cantidad_inactividad = max(1, int(round(self.n * 0.10))) if self.n >= 5 else 0
        cantidad_combustible = max(1, int(round(self.n * 0.10))) if self.n >= 5 else 0
        pos = 0
        for i in indices[pos:pos + cantidad_sobreuso]:
            self.maquinas[i].escenario_base = "sobreuso"
        pos += cantidad_sobreuso
        for i in indices[pos:pos + cantidad_inactividad]:
            self.maquinas[i].escenario_base = "inactividad"
        pos += cantidad_inactividad
        for i in indices[pos:pos + cantidad_combustible]:
            maquina = self.maquinas[i]
            maquina.escenario_base = "combustible_creciente"
            maquina.combustible_inicio = self.inicio + dt.timedelta(
                days=self.rng.uniform(0.0, self.dias * 0.35),
                hours=self.rng.uniform(0.0, 24.0),
            )
            maquina.combustible_tasa = self.rng.uniform(0.004, 0.009)
        if tasa_falla > 0:
            self._planificar_clusters()
            self._planificar_derivas(tasa_falla)
        self._planificar_anomalias_benignas()
        self._registrar_eventos_fallas()
        self._generar_consumo_irregular()
        for maquina in self.maquinas:
            maquina.fallas.sort(key=lambda e: e["ts_falla"])
        self.fallas.sort(key=lambda e: (e["ts_falla"], e["maquina_id"]))
        self.incidentes.sort(key=lambda e: (e["ts"], e["incidente_id"]))
        self.mantenimientos.sort(key=lambda e: (e["ts"], e["mantenimiento_id"]))
        self.consumos_repuestos.sort(key=lambda e: (e["ts"], e["consumo_id"]))

    def _nueva_falla_id(self):
        self._falla_seq += 1
        return "F-%04d" % self._falla_seq

    def _crear_evento(self, maquina, escenario, componente, inicio_deriva, falla, reparacion, grupo_id="", causa="", cluster_causa=None):
        evento = {
            "falla_id": self._nueva_falla_id(),
            "maquina_id": maquina.id,
            "zona": maquina.zona,
            "componente": componente,
            "escenario": escenario,
            "ts_inicio_deriva": inicio_deriva,
            "ts_falla": falla,
            "ts_reparacion": reparacion,
            "grupo_id": grupo_id,
            "causa_verdadera": causa,
            "cluster_causa": cluster_causa,
        }
        maquina.fallas.append(evento)
        self.fallas.append(evento)
        return evento

    def _planificar_clusters(self):
        cantidad = max(1, self.n // 25)
        usadas = set()
        for _ in range(cantidad):
            por_zona = {}
            for maquina in self.maquinas:
                if maquina.id not in usadas:
                    por_zona.setdefault(maquina.zona, []).append(maquina)
            validas = [z for z, lista in por_zona.items() if len(lista) >= 3]
            if not validas:
                break
            zona_id = self.rng.choice(validas)
            disponibles = por_zona[zona_id]
            k = self.rng.randint(3, min(6, len(disponibles)))
            elegidas = self.rng.sample(disponibles, k)
            tipo = self.rng.choice(["clima", "terreno", "lote_combustible"])
            if tipo == "clima":
                valor = self.rng.choice(["polvo", "lluvia"])
                componente = self.rng.choice(["refrigeracion", "filtros"])
            elif tipo == "terreno":
                valor = self.rng.choice(["rocoso", "pendiente", "barro"])
                componente = self.rng.choice(["tren_rodaje", "transmision"])
            else:
                if not self.lotes_malos:
                    self.lotes_malos.add(self.rng.choice(self.lotes))
                valor = self.rng.choice(sorted(self.lotes_malos))
                componente = self.rng.choice(["inyeccion_combustible", "filtros"])
            duracion = self.rng.uniform(5.0, 10.0)
            ultimo_inicio = self.dias - duracion - 2.2
            if ultimo_inicio < 4.0:
                break
            inicio_dias = self.rng.uniform(4.0, ultimo_inicio)
            inicio_deriva = self.inicio + dt.timedelta(days=inicio_dias, hours=self.rng.uniform(0.0, 12.0))
            falla_base = inicio_deriva + dt.timedelta(days=duracion)
            self._grupo_seq += 1
            grupo_id = "G-%04d" % self._grupo_seq
            causa = "%s=%s" % (tipo, valor)
            for maquina in elegidas:
                falla = falla_base + dt.timedelta(hours=self.rng.uniform(-12.0, 12.0))
                limite = self.fin - dt.timedelta(hours=49.0)
                if falla > limite:
                    falla = limite
                minimo = inicio_deriva + dt.timedelta(hours=1.0)
                if falla < minimo:
                    falla = minimo
                reparacion = falla + dt.timedelta(hours=self.rng.uniform(4.0, 48.0))
                if reparacion >= self.fin:
                    reparacion = self.fin - dt.timedelta(minutes=1)
                self._crear_evento(maquina, "cluster_zona", componente, inicio_deriva, falla, reparacion, grupo_id, causa, (tipo, valor))
                usadas.add(maquina.id)

    def _planificar_derivas(self, tasa_falla):
        ya = set(evento["maquina_id"] for evento in self.fallas)
        objetivo_total = int(round(self.n * tasa_falla))
        objetivo = max(0, objetivo_total - len(ya))
        elegibles = [maquina for maquina in self.maquinas if maquina.id not in ya]
        self.rng.shuffle(elegibles)
        for maquina in elegibles[:objetivo]:
            evento = self._programar_deriva(maquina)
            if evento is None:
                continue
            if self.rng.random() < 0.15:
                self._programar_reincidencia(maquina, evento)

    def _programar_deriva(self, maquina):
        deriva_dias = self.rng.uniform(3.0, 10.0)
        minimo = deriva_dias + 1.0
        maximo = self.dias - 2.2
        if maximo <= minimo:
            return None
        falla_dias = self.rng.uniform(minimo, maximo)
        falla = self.inicio + dt.timedelta(days=falla_dias, hours=self.rng.uniform(0.0, 24.0))
        inicio_deriva = falla - dt.timedelta(days=deriva_dias)
        if inicio_deriva < self.inicio:
            inicio_deriva = self.inicio
        reparacion = falla + dt.timedelta(hours=self.rng.uniform(4.0, 48.0))
        if reparacion >= self.fin:
            return None
        componente = self.rng.choice(self.componentes)
        return self._crear_evento(maquina, "deriva_conjunta", componente, inicio_deriva, falla, reparacion, "", "desgaste")

    def _programar_reincidencia(self, maquina, evento):
        deriva_dias = self.rng.uniform(3.0, 10.0)
        minimo = evento["ts_reparacion"] + dt.timedelta(days=deriva_dias + 1.0)
        maximo = min(evento["ts_reparacion"] + dt.timedelta(days=30.0), self.fin - dt.timedelta(days=2.2))
        if minimo >= maximo:
            return None
        segundos = (maximo - minimo).total_seconds()
        falla = minimo + dt.timedelta(seconds=self.rng.uniform(0.0, segundos))
        inicio_deriva = falla - dt.timedelta(days=deriva_dias)
        reparacion = falla + dt.timedelta(hours=self.rng.uniform(4.0, 48.0))
        if reparacion >= self.fin:
            return None
        return self._crear_evento(maquina, "deriva_conjunta", evento["componente"], inicio_deriva, falla, reparacion, "", "desgaste")

    def _planificar_anomalias_benignas(self):
        for maquina in self.maquinas:
            cantidad = self.rng.randint(1, 3)
            intentos = 0
            while len(maquina.anomalias) < cantidad and intentos < 50:
                intentos += 1
                dia = self.rng.uniform(1.0, max(1.1, self.dias - 1.5))
                hora = self.rng.uniform(7.0, 17.0)
                inicio = self.inicio + dt.timedelta(days=dia, hours=hora)
                duracion = self.rng.uniform(2.0, 8.0)
                fin = inicio + dt.timedelta(hours=duracion)
                if fin >= self.fin:
                    continue
                if self._solapa_prohibido(maquina, inicio, fin):
                    continue
                if any(not (fin <= a["inicio"] or inicio >= a["fin"]) for a in maquina.anomalias):
                    continue
                variable = self.rng.choice(["temp", "vib", "combustible_lph"])
                if variable == "temp":
                    magnitud = self.rng.uniform(8.0, 18.0)
                elif variable == "vib":
                    magnitud = self.rng.uniform(3.0, 6.0)
                else:
                    magnitud = self.rng.uniform(0.15, 0.35)
                maquina.anomalias.append({
                    "variable": variable,
                    "inicio": inicio,
                    "fin": fin,
                    "magnitud": magnitud,
                })

    def _solapa_prohibido(self, maquina, inicio, fin):
        for evento in maquina.fallas:
            prohibido_inicio = min(
                evento["ts_inicio_deriva"],
                evento["ts_falla"] - dt.timedelta(days=7.0),
            )
            prohibido_fin = evento["ts_reparacion"]
            if not (fin <= prohibido_inicio or inicio >= prohibido_fin):
                return True
        return False

    def _anomalia_activa(self, maquina, ts):
        for anomalia in maquina.anomalias:
            if anomalia["inicio"] <= ts < anomalia["fin"]:
                return anomalia
        return None

    def _combustible_mult(self, maquina, ts):
        if maquina.escenario_base != "combustible_creciente" or maquina.combustible_inicio is None:
            return 1.0
        if ts < maquina.combustible_inicio:
            return 1.0
        dias = (ts - maquina.combustible_inicio).total_seconds() / 86400.0
        return min(1.45, 1.0 + maquina.combustible_tasa * dias)

    def _registrar_eventos_fallas(self):
        for evento in self.fallas:
            self._registrar_incidente(evento)
            self._registrar_correctivo(evento)

    def _registrar_incidente(self, evento):
        self._incidente_seq += 1
        if evento["escenario"] == "cluster_zona":
            severidad = "alta"
        elif evento["componente"] == "motor":
            severidad = "critica"
        elif evento["componente"] in ("transmision", "sistema_hidraulico"):
            severidad = "alta"
        else:
            severidad = "media"
        estado = "cerrado" if evento["ts_reparacion"] < self.fin - dt.timedelta(hours=1.0) else "en_proceso"
        if evento["escenario"] == "cluster_zona":
            descripcion = "Falla de %s por %s" % (evento["componente"], evento["causa_verdadera"])
        else:
            descripcion = "Falla de %s por desgaste" % evento["componente"]
        self.incidentes.append({
            "incidente_id": "INC-%04d" % self._incidente_seq,
            "maquina_id": evento["maquina_id"],
            "ts": _iso(evento["ts_falla"]),
            "severidad": severidad,
            "estado": estado,
            "descripcion": descripcion,
        })

    def _registrar_correctivo(self, evento):
        self._mantenimiento_seq += 1
        self.mantenimientos.append({
            "mantenimiento_id": "MT-%04d" % self._mantenimiento_seq,
            "maquina_id": evento["maquina_id"],
            "ts": _iso(evento["ts_reparacion"]),
            "tipo_mantenimiento": "correctivo",
            "estado": "completado",
            "componentes": [evento["componente"]],
        })
        self._registrar_consumo(evento["maquina_id"], evento["ts_reparacion"], evento["componente"], 1)

    def _registrar_consumo(self, maquina_id, ts, componente, cantidad):
        self._consumo_seq += 1
        self.consumos_repuestos.append({
            "consumo_id": "CR-%04d" % self._consumo_seq,
            "maquina_id": maquina_id,
            "ts": _iso(ts),
            "componente": componente,
            "cantidad": cantidad,
            "unidad": "unidad",
        })

    def _registrar_preventivo(self, maquina, ts):
        if not self.registrar_eventos or maquina.proximo_mantenimiento is None:
            return
        while maquina.horas_op >= maquina.proximo_mantenimiento:
            self._mantenimiento_seq += 1
            componentes = COMPONENTES_PREVENTIVO.get(maquina.modelo, ["filtros", "motor"])
            self.mantenimientos.append({
                "mantenimiento_id": "MT-%04d" % self._mantenimiento_seq,
                "maquina_id": maquina.id,
                "ts": _iso(ts),
                "tipo_mantenimiento": "preventivo",
                "estado": "completado",
                "componentes": list(componentes),
            })
            for componente in componentes:
                cantidad = 2 if componente == "filtros" else 1
                self._registrar_consumo(maquina.id, ts, componente, cantidad)
            maquina.proximo_mantenimiento += MANTENIMIENTO_CADA_H

    def _generar_consumo_irregular(self):
        if not self.componentes:
            return
        zonas = sorted(set(m.zona for m in self.maquinas))
        if not zonas:
            return
        componentes = self.rng.sample(self.componentes, min(2, len(self.componentes)))
        zonas_sel = self.rng.sample(zonas, min(2, len(zonas)))
        for componente in componentes:
            for zona in zonas_sel:
                disponibles = [m for m in self.maquinas if m.zona == zona]
                if not disponibles:
                    continue
                for _ in range(self.rng.randint(5, 9)):
                    maquina = self.rng.choice(disponibles)
                    dia = self.rng.uniform(1.0, max(1.1, self.dias - 1.0))
                    hora = self.rng.uniform(0.0, 24.0)
                    ts = self.inicio + dt.timedelta(days=dia, hours=hora)
                    if ts >= self.fin:
                        continue
                    self._registrar_consumo(maquina.id, ts, componente, self.rng.randint(3, 8))

    def _factor_actividad(self, maquina, ts):
        hora = (ts.hour + ts.minute / 60.0 + maquina.turno_offset) % 24.0
        if maquina.escenario_base == "sobreuso":
            return 1.0 if 4.0 <= hora < 24.0 else 0.0
        if maquina.escenario_base == "inactividad":
            return 1.0 if 9.0 <= hora < 12.0 else 0.0
        if 6.0 <= hora < 18.0:
            return 1.0
        if 18.0 <= hora < 22.0:
            return 0.55
        return 0.0

    def _evento_activo(self, maquina, ts):
        for evento in maquina.fallas:
            if evento["ts_inicio_deriva"] <= ts < evento["ts_reparacion"]:
                return evento
        return None

    def _contexto_efectivo(self, maquina, evento):
        clima = maquina.clima
        terreno = maquina.terreno
        lote = maquina.lote
        if evento and evento.get("cluster_causa"):
            tipo, valor = evento["cluster_causa"]
            if tipo == "clima":
                clima = valor
            elif tipo == "terreno":
                terreno = valor
            elif tipo == "lote_combustible":
                lote = valor
        return clima, terreno, lote

    def _etiquetas(self, maquina, ts, evento_activo, anomalia_activa):
        proximo = None
        for evento in maquina.fallas:
            if evento["ts_falla"] >= ts:
                proximo = evento
                break
        etiqueta = 0
        dias = ""
        componente = ""
        falla_id = ""
        grupo_id = ""
        escenario = ""
        if evento_activo:
            componente = evento_activo["componente"]
            falla_id = evento_activo["falla_id"]
            grupo_id = evento_activo["grupo_id"]
            escenario = evento_activo["escenario"]
        elif maquina.escenario_base != "normal":
            escenario = maquina.escenario_base
        if proximo:
            delta = (proximo["ts_falla"] - ts).total_seconds() / 86400.0
            if 0.0 <= delta <= 7.0:
                etiqueta = 1
                dias = round(delta, 2)
                componente = proximo["componente"]
                falla_id = proximo["falla_id"]
                grupo_id = proximo["grupo_id"]
                escenario = proximo["escenario"]
        if anomalia_activa and not evento_activo and not etiqueta:
            escenario = "anomalias_benignas"
        return etiqueta, dias, componente, escenario, grupo_id, falla_id

    def leer(self, maquina, ts):
        self._actualizar_contexto(maquina, ts)
        evento = self._evento_activo(maquina, ts)
        anomalia = self._anomalia_activa(maquina, ts)
        en_falla = bool(evento and evento["ts_falla"] <= ts < evento["ts_reparacion"])
        if en_falla:
            horas = (ts - evento["ts_falla"]).total_seconds() / 3600.0
            duracion = (evento["ts_reparacion"] - evento["ts_falla"]).total_seconds() / 3600.0
            fase = max(1.0, duracion * 0.25)
            estado = "requiere_intervencion" if horas < fase else "detenida"
            factor = 0.0
        else:
            factor = self._factor_actividad(maquina, ts)
            estado = "operativa" if factor > 0 else "detenida"

        clima, terreno, lote = self._contexto_efectivo(maquina, evento)
        ctx_temp, ctx_vib, ctx_fuel = _efecto_contexto(clima, terreno, lote, self.lotes_malos)

        der_temp = 0.0
        der_vib = 0.0
        der_fuel = 1.0
        if evento and not en_falla and evento["ts_inicio_deriva"] <= ts < evento["ts_falla"]:
            p = _progreso(evento["ts_inicio_deriva"], evento["ts_falla"], ts)
            if evento["escenario"] == "deriva_conjunta":
                q = p ** 1.15
                der_temp = 9.5 * q
                der_vib = 4.5 * q
                der_fuel = 1.0 + 0.20 * q
            elif evento["escenario"] == "cluster_zona" and evento.get("cluster_causa"):
                tipo, valor = evento["cluster_causa"]
                c_temp, c_vib, c_fuel = _degradacion_cluster(tipo, valor, p)
                der_temp = c_temp
                der_vib = c_vib
                der_fuel = c_fuel

        anom_temp = 0.0
        anom_vib = 0.0
        anom_fuel = 1.0
        if anomalia:
            p = _progreso(anomalia["inicio"], anomalia["fin"], ts)
            bump = math.sin(math.pi * p)
            if anomalia["variable"] == "temp":
                anom_temp = anomalia["magnitud"] * bump
            elif anomalia["variable"] == "vib":
                anom_vib = anomalia["magnitud"] * bump
            else:
                anom_fuel = 1.0 + anomalia["magnitud"] * bump

        base = BASE_MODELO[maquina.modelo]
        if en_falla:
            if estado == "requiere_intervencion":
                temp_obj = base["temp"] + 20.0
                vib_obj = base["vib"] + 8.0
                pres_obj = 35.0
                fuel_obj = base["combustible_lph"] * 1.25
                alpha_temp = 0.60
                alpha_vib = 0.60
            else:
                temp_obj = 32.0
                vib_obj = 0.35
                pres_obj = 10.0
                fuel_obj = 0.8
                alpha_temp = 0.30
                alpha_vib = 0.40
            carga = 0.0
            velocidad = 0.0
            rpm = 0.0
        elif factor > 0:
            temp_obj = base["temp"] + ctx_temp + der_temp + anom_temp
            vib_obj = base["vib"] * (0.55 + 0.45 * factor) + ctx_vib + der_vib + anom_vib
            pres_obj = base["presion"] * (0.85 + 0.15 * factor)
            fuel_obj = base["combustible_lph"] * (0.55 + 0.45 * factor) * ctx_fuel * der_fuel
            fuel_obj *= self._combustible_mult(maquina, ts) * anom_fuel
            carga = base["carga_t"] * (0.55 + 0.45 * factor) * self.rng.uniform(0.75, 1.20)
            velocidad = base["velocidad"] * factor * self.rng.uniform(0.80, 1.20)
            rpm = base["rpm"] * (0.65 + 0.35 * factor)
            alpha_temp = 0.45
            alpha_vib = 0.55
            maquina.horas_op += self.paso_horas * factor
            self._registrar_preventivo(maquina, ts)
        else:
            temp_obj = 28.0 + anom_temp
            vib_obj = 0.15 + anom_vib
            pres_obj = 12.0
            fuel_obj = 0.7
            carga = 0.0
            velocidad = 0.0
            rpm = 0.0
            alpha_temp = 0.35
            alpha_vib = 0.50

        maquina.temp += (temp_obj - maquina.temp) * alpha_temp + self.rng.gauss(0.0, 0.6)
        maquina.vib += (vib_obj - maquina.vib) * alpha_vib + self.rng.gauss(0.0, 0.20)
        maquina.temp = _clamp(maquina.temp, 18.0, 125.0)
        maquina.vib = _clamp(maquina.vib, 0.0, 20.0)
        presion = _clamp(pres_obj + self.rng.gauss(0.0, 3.0), 0.0, 320.0)
        fuel = _clamp(fuel_obj + self.rng.gauss(0.0, 1.2), 0.0, 100.0)
        if factor > 0 and not en_falla:
            carga = _clamp(carga + self.rng.gauss(0.0, 1.5), 0.0, 150.0)
            velocidad = _clamp(velocidad + self.rng.gauss(0.0, 0.8), 0.0, 70.0)
            rpm = _clamp(rpm + self.rng.gauss(0.0, 30.0), 0.0, 2600.0)
        else:
            carga = 0.0
            velocidad = 0.0
            rpm = 0.0

        etiqueta, dias, componente, escenario, grupo_id, falla_id = self._etiquetas(maquina, ts, evento, anomalia)
        return {
            "maquina_id": maquina.id,
            "ts": _iso(ts),
            "modelo": maquina.modelo,
            "zona": maquina.zona,
            "lat": round(maquina.lat, 6),
            "lon": round(maquina.lon, 6),
            "horas_op": round(maquina.horas_op, 2),
            "temp": round(maquina.temp, 2),
            "vib": round(maquina.vib, 3),
            "presion": round(presion, 1),
            "combustible_lph": round(fuel, 2),
            "carga_t": round(carga, 2),
            "velocidad": round(velocidad, 2),
            "rpm": int(round(rpm)),
            "estado": estado,
            "contexto_clima": clima,
            "contexto_terreno": terreno,
            "contexto_lote_combustible": lote,
            "etiqueta_falla_7d": etiqueta,
            "dias_a_falla": dias,
            "componente_falla": componente,
            "escenario": escenario,
            "grupo_id": grupo_id,
            "falla_id": falla_id,
        }

    def iterar(self):
        ts = self.inicio
        while ts < self.fin:
            for maquina in self.maquinas:
                yield self.leer(maquina, ts)
            ts += self.paso


def generar_historico(catalogos, n, dias, semilla, salida, paso_min=60, tasa_falla=0.5, inicio=None):
    if n < 1:
        raise ValueError("n debe ser >= 1")
    if dias < 1:
        raise ValueError("dias debe ser >= 1")
    if paso_min < 1:
        raise ValueError("paso-min debe ser >= 1")
    if not 0.0 <= tasa_falla <= 1.0:
        raise ValueError("tasa-falla debe estar entre 0 y 1")
    inicio_ts = _parse_inicio(inicio, dias)
    flota = Flota(catalogos, n, dias, semilla, inicio_ts, paso_min, tasa_falla)
    os.makedirs(salida, exist_ok=True)
    ruta_telemetria = os.path.join(salida, "telemetria.csv")
    ruta_fallas = os.path.join(salida, "fallas.csv")
    ruta_incidentes = os.path.join(salida, "incidentes.csv")
    ruta_mantenimientos = os.path.join(salida, "mantenimientos.csv")
    ruta_consumos = os.path.join(salida, "consumo_repuestos.csv")
    filas = 0
    positivos = 0
    with open(ruta_telemetria, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=CAMPOS_TELEMETRIA)
        escritor.writeheader()
        for fila in flota.iterar():
            escritor.writerow(fila)
            filas += 1
            if fila["etiqueta_falla_7d"] == 1:
                positivos += 1
    with open(ruta_fallas, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=CAMPOS_FALLAS)
        escritor.writeheader()
        for evento in flota.fallas:
            escritor.writerow({
                "falla_id": evento["falla_id"],
                "maquina_id": evento["maquina_id"],
                "zona": evento["zona"],
                "componente": evento["componente"],
                "escenario": evento["escenario"],
                "ts_inicio_deriva": _iso(evento["ts_inicio_deriva"]),
                "ts_falla": _iso(evento["ts_falla"]),
                "ts_reparacion": _iso(evento["ts_reparacion"]),
                "grupo_id": evento["grupo_id"],
                "causa_verdadera": evento["causa_verdadera"],
            })
    with open(ruta_incidentes, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=CAMPOS_INCIDENTES)
        escritor.writeheader()
        for evento in sorted(flota.incidentes, key=lambda e: (e["ts"], e["incidente_id"])):
            escritor.writerow(evento)
    with open(ruta_mantenimientos, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=CAMPOS_MANTENIMIENTOS)
        escritor.writeheader()
        for evento in sorted(flota.mantenimientos, key=lambda e: (e["ts"], e["mantenimiento_id"])):
            escritor.writerow({
                "mantenimiento_id": evento["mantenimiento_id"],
                "maquina_id": evento["maquina_id"],
                "ts": evento["ts"],
                "tipo_mantenimiento": evento["tipo_mantenimiento"],
                "estado": evento["estado"],
                "componentes": ";".join(evento["componentes"]),
            })
    with open(ruta_consumos, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=CAMPOS_CONSUMOS)
        escritor.writeheader()
        for evento in sorted(flota.consumos_repuestos, key=lambda e: (e["ts"], e["consumo_id"])):
            escritor.writerow(evento)
    conteo = {}
    for evento in flota.fallas:
        conteo[evento["escenario"]] = conteo.get(evento["escenario"], 0) + 1
    return {
        "salida": salida,
        "ruta_telemetria": ruta_telemetria,
        "ruta_fallas": ruta_fallas,
        "ruta_incidentes": ruta_incidentes,
        "ruta_mantenimientos": ruta_mantenimientos,
        "ruta_consumos": ruta_consumos,
        "filas_por_archivo": {
            "telemetria.csv": filas,
            "fallas.csv": len(flota.fallas),
            "incidentes.csv": len(flota.incidentes),
            "mantenimientos.csv": len(flota.mantenimientos),
            "consumo_repuestos.csv": len(flota.consumos_repuestos),
        },
        "fallas_por_escenario": conteo,
        "maquinas_con_falla": len(set(evento["maquina_id"] for evento in flota.fallas)),
        "positivos": positivos,
        "pct_positivos": (100.0 * positivos / filas) if filas else 0.0,
        "fecha_inicio": _iso(flota.inicio),
        "fecha_fin": _iso(flota.inicio + dt.timedelta(days=dias) - flota.paso),
    }

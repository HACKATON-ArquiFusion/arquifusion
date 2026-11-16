const API = window.ARQUIFUSION_API || "https://ia-1073552578418.us-central1.run.app";

const EST_API = { operativa: "op", detenida: "det", requiere_intervencion: "int" };
const SEV_API = { critica: "Crítica", alta: "Alta", media: "Media", baja: "Baja" };
const FASE_API = {
  detectado: "nuevo", abierto: "nuevo", explicado: "nuevo", reservado: "nuevo",
  asignado: "asig", en_camino: "camino", en_sitio: "sitio", cerrado: "cerrado",
};
const ROL_API = { operador: "op", tecnico: "tec", supervisor: "sup", gerente: "ger" };
const CENTRO = {
  Z1: [-15.48, -71.52], Z2: [-15.52, -71.52], Z3: [-15.5, -71.48],
  Z4: [-15.5, -71.56], Z5: [-15.505, -71.535],
};

const estadoApi = { conectado: false, kpis: null, detalle: {}, error: "" };

async function traer(ruta) {
  const r = await fetch(API + ruta);
  if (!r.ok) throw new Error(ruta + " HTTP " + r.status);
  return r.json();
}

function posicion(z, lat, lon) {
  const c = CENTRO[z];
  if (!c || typeof lat !== "number" || typeof lon !== "number") return [50, 50];
  const lim = (v) => Math.max(12, Math.min(88, v));
  return [lim(50 + ((lon - c[1]) / 0.03) * 70), lim(50 - ((lat - c[0]) / 0.03) * 70)];
}

function hora(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d) ? String(ts).slice(11, 16) : d.toTimeString().slice(0, 5);
}

function aMaquina(m) {
  const [x, y] = posicion(m.zona, m.lat, m.lon);
  return {
    id: m.maquina_id, mod: m.modelo || "—", z: CENTRO[m.zona] ? m.zona : "Z1",
    x, y, e: EST_API[m.estado] || "det", r: Math.round(m.riesgo ?? 0),
    t: 0, v: 0, p: 0, c: 0, h: 0, u: null, $: null,
    inc: m.incidente_abierto || null, horas_op: m.horas_op ?? 0,
  };
}

function aTecnico(t) {
  return {
    id: t.tecnico_id, n: t.nombre, es: (t.especialidades || [])[0] || "general",
    z: t.zona, f: t.disponibilidad === "disponible" ? 1 : 0,
    todas: t.especialidades || [],
  };
}

const ETIQUETA_JUST = {
  especialidad: "especialidad", cercania: "cercanía",
  disponibilidad: "disponibilidad", tiempo_estimado: "tiempo estimado",
};

function textoJustificacion(j) {
  if (!j || typeof j !== "object") return typeof j === "string" ? j : "";
  const partes = Object.entries(j)
    .filter(([k]) => k !== "score" && ETIQUETA_JUST[k])
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${ETIQUETA_JUST[k]} ${Math.round(v * 100)}%`);
  if (!partes.length) return "";
  const total = typeof j.score === "number" ? ` · puntaje ${Math.round(j.score * 100)}` : "";
  return "Asignado por " + partes.join(", ") + total;
}

const COMP_POR_VAR = {
  temp: "refrigeracion", vib: "tren_rodaje", presion: "sistema_hidraulico",
  combustible_lph: "inyeccion_combustible", rpm: "motor",
};

function aIncidente(i) {
  const reservado = (i.repuestos_reservados || [])[0];
  const comp = i.componente_probable || reservado?.componente
    || COMP_POR_VAR[(i.variables_desvio || [])[0]] || "general";
  return {
    id: i.incidente_id, m: i.maquina_id, s: SEV_API[i.severidad] || "Media",
    es: comp,
    c: [i.causa_probable, i.accion_recomendada].filter(Boolean).join(" ") || "Sin descripción.",
    t: i.tecnico_id || null, st: FASE_API[i.fase] || FASE_API[i.estado] || "nuevo",
    h: hora(i.abierto_ts || i.ts || i.inicio),
    just: textoJustificacion(i.justificacion_asignacion), eta: i.eta_min ?? null,
    prob: i.prob_falla ?? null, reinc: !!i.reincidente,
    repuesto: reservado ? `${reservado.componente} (${reservado.zona})` : "",
    vars: i.variables_desvio || [],
  };
}

function aRepuesto(s) {
  const nivel = s.estado === "faltante_proyectado" ? "bajo"
    : s.estado === "sobre_stock" || s.estado === "ok" ? "ok" : "justo";
  return [s.descripcion || s.componente, s.stock, s.demanda_proyectada_7d ?? 0, nivel, s.zona];
}

function reemplazar(arr, nuevos) {
  arr.length = 0;
  nuevos.forEach((x) => arr.push(x));
}

async function enriquecer(ids) {
  for (const id of ids) {
    if (estadoApi.detalle[id]) continue;
    try {
      const d = await traer("/api/maquinas/" + id);
      estadoApi.detalle[id] = d;
      const m = M.find((x) => x.id === id);
      const u = d.ultima_lectura || {};
      if (m) {
        m.t = u.temp ?? 0; m.v = u.vib ?? 0; m.p = u.presion ?? 0; m.c = u.combustible_lph ?? 0;
        m.z_scores = d.z_scores || {}; m.contexto = d.contexto || {};
      }
    } catch (e) {
      console.warn("detalle", id, e.message);
    }
  }
}

function avisoApi(texto, malo) {
  let e = document.getElementById("api-aviso");
  if (!e) {
    e = document.createElement("div");
    e.id = "api-aviso";
    e.style.cssText = "position:fixed;left:50%;transform:translateX(-50%);bottom:14px;z-index:99;" +
      "padding:9px 16px;border-radius:999px;font-size:13px;font-weight:600;box-shadow:0 4px 14px rgba(0,0,0,.2)";
    document.body.appendChild(e);
  }
  e.textContent = texto;
  e.style.background = malo ? "var(--rd)" : "var(--gn)";
  e.style.color = "#fff";
  if (!malo) setTimeout(() => e.remove(), 4000);
}

async function cargarReal() {
  try {
    const flota = await traer("/api/flota");
    const maquinas = (flota.maquinas || []).map(aMaquina);
    if (!maquinas.length) throw new Error("la flota está vacía");
    reemplazar(M, maquinas);

    const tec = await traer("/api/tecnicos");
    if ((tec.tecnicos || []).length) reemplazar(T, tec.tecnicos.map(aTecnico));

    const inc = await traer("/api/incidentes");
    reemplazar(I, (inc.incidentes || []).map(aIncidente));

    const rep = await traer("/api/repuestos");
    if ((rep.stock || []).length) reemplazar(R, rep.stock.map(aRepuesto));

    estadoApi.kpis = await traer("/api/kpis");

    const notis = await traer("/api/notificaciones");
    N.length = 0;
    (notis.notificaciones || []).forEach((n) => {
      const r = ROL_API[n.rol];
      if (r) N.push({ rs: [r], txt: n.titulo + ": " + n.mensaje, h: hora(n.ts) });
    });

    const conIncidente = [...new Set(I.map((i) => i.m))];
    const noOperativas = M.filter((m) => m.e !== "op").map((m) => m.id);
    const foco = [...new Set([...conIncidente, ...noOperativas])].slice(0, 6);

    U.op.m = conIncidente[0] || M[0].id;
    if (T.length) { U.tec.t = T[0].id; U.tec.n = T[0].n; }

    await enriquecer([...new Set([...foco, U.op.m])]);

    estadoApi.conectado = true;
    avisoApi(`Datos reales · ${M.length} máquinas · ${I.length} incidentes`, false);
  } catch (e) {
    estadoApi.error = e.message;
    avisoApi("Sin conexión con la API: datos de ejemplo (" + e.message + ")", true);
    console.warn("API no disponible:", e);
  }
  if (typeof role !== "undefined" && role) render();
}

function preguntarIA(q) {
  say(q, 1);
  const burbuja = document.createElement("div");
  burbuja.className = "m";
  burbuja.textContent = "Pensando…";
  document.querySelector("#msgs").append(burbuja);
  const rolApi = Object.keys(ROL_API).find((k) => ROL_API[k] === role) || "supervisor";
  fetch(API + "/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mensaje: q, rol: rolApi }),
  })
    .then((r) => r.json())
    .then((d) => {
      if (!d.respuesta) throw new Error(d.error || "sin respuesta");
      burbuja.textContent = d.respuesta;
      const pie = document.createElement("div");
      pie.style.cssText = "margin-top:6px;font-size:11px;opacity:.7";
      const orig = d.fuente === "modelo" ? "Gemini" : "plantilla";
      const usados = (d.datos_usados || []).map((u) => u.endpoint).join(", ");
      pie.textContent = orig + " · " + d.latencia_ms + " ms" + (usados ? " · " + usados : "");
      burbuja.append(pie);
      document.querySelector("#msgs").scrollTop = 1e9;
    })
    .catch((e) => {
      burbuja.textContent = "No pude responder ahora mismo (" + e.message + ").";
    });
}

window.addEventListener("load", () => {
  window.ask = preguntarIA;
  cargarReal();
});

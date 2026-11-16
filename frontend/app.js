const API = window.ARQUIFUSION_API || "https://ia-1073552578418.us-central1.run.app";

const VISTAS = [
  ["flota", "Flota y mapa"],
  ["maquina", "Máquina"],
  ["incidentes", "Incidentes"],
  ["preventivo", "Preventivo y repuestos"],
  ["bandeja", "Bandeja"],
  ["chat", "Asistente"],
];

const ZONAS = {
  Z1: { nombre: "Tajo Norte", lat: -15.48, lon: -71.52 },
  Z2: { nombre: "Tajo Sur", lat: -15.52, lon: -71.52 },
  Z3: { nombre: "Botadero", lat: -15.5, lon: -71.48 },
  Z4: { nombre: "Planta", lat: -15.5, lon: -71.56 },
  Z5: { nombre: "Taller", lat: -15.505, lon: -71.535 },
};

const estado = { flota: [], kpis: null, historial: [] };

const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = (v, d = 1) => (typeof v === "number" ? v.toFixed(d) : "—");

let modoDemo = false;

function respuestaDemo(ruta) {
  const d = window.ARQUIFUSION_DEMO;
  if (!d) return null;
  const limpia = ruta.split("?")[0];
  return d[limpia] ?? d[ruta] ?? null;
}

async function pedir(ruta, opciones) {
  try {
    const r = await fetch(API + ruta, opciones);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  } catch (e) {
    const demo = (!opciones || opciones.method !== "POST") && respuestaDemo(ruta);
    if (demo) {
      modoDemo = true;
      return JSON.parse(JSON.stringify(demo));
    }
    throw e;
  }
}

function avisar(texto) {
  $("#aviso").innerHTML = texto ? `<div class="aviso">${esc(texto)}</div>` : "";
}

function vacio(titulo, detalle) {
  return `<div class="vacio"><strong>${esc(titulo)}</strong>${esc(detalle)}</div>`;
}

function colorRiesgo(r) {
  if (r >= 80) return "var(--rojo)";
  if (r >= 60) return "var(--ambar)";
  if (r >= 40) return "var(--azul)";
  return "var(--verde)";
}

function montarNav() {
  $("#nav").innerHTML = VISTAS.map(
    ([id, t], i) => `<button data-ir="${id}" class="${i === 0 ? "activo" : ""}">${t}</button>`
  ).join("");
  $("#nav").onclick = (e) => {
    const id = e.target.dataset.ir;
    if (!id) return;
    document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("activo", b.dataset.ir === id));
    document.querySelectorAll(".vista").forEach((v) => v.classList.toggle("activa", v.dataset.vista === id));
  };
}

function irA(vista) {
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("activo", b.dataset.ir === vista));
  document.querySelectorAll(".vista").forEach((v) => v.classList.toggle("activa", v.dataset.vista === vista));
}

function pintarKpis(k) {
  if (!k) return;
  const e = k.por_estado || {};
  const tarjetas = [
    ["Máquinas", k.maquinas_total, "en la operación"],
    ["Operativas", e.operativa ?? "—", "trabajando ahora"],
    ["Detenidas", e.detenida ?? "—", "fuera de servicio"],
    ["Requieren intervención", e.requiere_intervencion ?? "—", "con anomalía"],
    ["Incidentes abiertos", k.incidentes_abiertos, "sin cerrar"],
    ["Reparación media", num(k.tiempo_medio_reparacion_h) + " h", "por incidente"],
    ["Reincidencia", num(k.reincidencia_pct) + " %", "vuelven a fallar"],
    ["Costo diario", "S/ " + num(k.costo_por_maquina_dia_pen), "por máquina"],
  ];
  $("#kpis").innerHTML = tarjetas
    .map(([l, v, p]) => `<div class="tarjeta"><div class="etiqueta">${l}</div><div class="valor">${esc(v)}</div><div class="pie">${p}</div></div>`)
    .join("");
}

function pintarMapa(maquinas) {
  const lats = Object.values(ZONAS).map((z) => z.lat);
  const lons = Object.values(ZONAS).map((z) => z.lon);
  const minLat = Math.min(...lats) - 0.015, maxLat = Math.max(...lats) + 0.015;
  const minLon = Math.min(...lons) - 0.02, maxLon = Math.max(...lons) + 0.02;
  const x = (lon) => ((lon - minLon) / (maxLon - minLon)) * 100;
  const y = (lat) => ((maxLat - lat) / (maxLat - minLat)) * 100;

  const zonas = Object.entries(ZONAS)
    .map(([id, z]) => `<div class="zona" style="left:${x(z.lon) - 9}%;top:${y(z.lat) - 11}%;width:18%;height:22%">${id} · ${z.nombre}</div>`)
    .join("");

  const puntos = maquinas
    .map((m) => {
      const lat = m.lat ?? ZONAS[m.zona]?.lat, lon = m.lon ?? ZONAS[m.zona]?.lon;
      if (lat == null) return "";
      const t = `${m.maquina_id} · ${m.modelo || ""} · ${m.estado} · riesgo ${m.riesgo ?? "—"}`;
      return `<div class="maq ${esc(m.estado)}" style="left:${x(lon)}%;top:${y(lat)}%" title="${esc(t)}" data-maq="${esc(m.maquina_id)}"></div>`;
    })
    .join("");

  $("#mapa").innerHTML = zonas + puntos;
  $("#mapa").onclick = (e) => {
    const id = e.target.dataset.maq;
    if (id) { $("#sel-maquina").value = id; irA("maquina"); verMaquina(); }
  };
}

function pintarFlota(maquinas) {
  if (!maquinas.length) { $("#tabla-flota").innerHTML = ""; return; }
  const filas = maquinas
    .slice()
    .sort((a, b) => (b.riesgo ?? 0) - (a.riesgo ?? 0))
    .map(
      (m) => `<tr data-maq="${esc(m.maquina_id)}">
        <td><strong>${esc(m.maquina_id)}</strong></td>
        <td>${esc(m.modelo)}</td>
        <td>${esc(m.zona)} · ${esc(ZONAS[m.zona]?.nombre || "")}</td>
        <td><span class="chip ${esc(m.estado)}">${esc(String(m.estado).replace(/_/g, " "))}</span></td>
        <td>${m.severidad ? `<span class="chip ${esc(m.severidad)}">${esc(m.severidad)}</span>` : "—"}</td>
        <td style="min-width:120px">
          <div class="barra"><i style="width:${Math.min(100, m.riesgo ?? 0)}%;background:${colorRiesgo(m.riesgo ?? 0)}"></i></div>
          <span class="sutil">${m.riesgo ?? "—"}</span>
        </td>
        <td>${num(m.horas_op, 0)} h</td>
        <td>${m.incidente_abierto ? esc(m.incidente_abierto) : "—"}</td>
      </tr>`
    )
    .join("");
  $("#tabla-flota").innerHTML =
    `<thead><tr><th>ID</th><th>Modelo</th><th>Zona</th><th>Estado</th><th>Severidad</th><th>Riesgo</th><th>Horas</th><th>Incidente</th></tr></thead><tbody>${filas}</tbody>`;
  $("#tabla-flota").onclick = (e) => {
    const tr = e.target.closest("tr[data-maq]");
    if (tr) { $("#sel-maquina").value = tr.dataset.maq; irA("maquina"); verMaquina(); }
  };
}

function grafica(serie, etiqueta, unidad, color) {
  const vals = serie.filter((v) => typeof v === "number");
  if (vals.length < 2) return "";
  const min = Math.min(...vals), max = Math.max(...vals), rango = max - min || 1;
  const n = vals.length;
  const pts = vals.map((v, i) => `${(i / (n - 1)) * 100},${30 - ((v - min) / rango) * 28}`).join(" ");
  return `<div>
    <div class="grafica-tit"><span>${esc(etiqueta)}</span><span>${num(vals[vals.length - 1], 2)} ${esc(unidad)} · min ${num(min, 1)} / max ${num(max, 1)}</span></div>
    <svg class="grafica" viewBox="0 0 100 32" preserveAspectRatio="none">
      <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="0.7" vector-effect="non-scaling-stroke"/>
    </svg>
  </div>`;
}

async function verMaquina() {
  const id = $("#sel-maquina").value;
  if (!id) return;
  $("#detalle").innerHTML = `<div class="panel"><span class="sutil">Cargando ${esc(id)}…</span></div>`;
  const [tele, riesgo] = await Promise.all([
    pedir(`/api/telemetria/${id}`).catch((e) => ({ error: e.message })),
    pedir(`/api/riesgo/${id}`).catch((e) => ({ error: e.message })),
  ]);

  let html = "";

  if (riesgo && !riesgo.error) {
    const pct = Math.round((riesgo.prob_falla ?? 0) * 100);
    const umb = Math.round((riesgo.umbral ?? 0) * 100);
    html += `<div class="panel">
      <h2>Riesgo calculado</h2>
      <div class="tarjetas">
        <div class="tarjeta"><div class="etiqueta">Probabilidad de falla</div><div class="valor" style="color:${colorRiesgo(pct)}">${pct}%</div><div class="pie">umbral ${umb}%</div></div>
        <div class="tarjeta"><div class="etiqueta">Supera umbral</div><div class="valor">${riesgo.supera_umbral ? "Sí" : "No"}</div><div class="pie">${esc(riesgo.metodo || "")}</div></div>
        <div class="tarjeta"><div class="etiqueta">Lecturas usadas</div><div class="valor">${esc(riesgo.lecturas_usadas ?? "—")}</div><div class="pie">ventana del modelo</div></div>
        <div class="tarjeta"><div class="etiqueta">Variables en desvío</div><div class="valor" style="font-size:16px">${esc((riesgo.variables_desvio || []).join(", ") || "ninguna")}</div><div class="pie">respecto al historial</div></div>
      </div>
    </div>`;

    const vals = riesgo.valores || {};
    if (Object.keys(vals).length) {
      html += `<div class="panel"><h2>Variables derivadas del modelo</h2><div class="scroll"><table>
        <thead><tr><th>Variable</th><th>Valor</th></tr></thead><tbody>
        ${Object.entries(vals).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${num(v, 3)}</td></tr>`).join("")}
        </tbody></table></div></div>`;
    }
  } else {
    html += `<div class="panel">${vacio("Sin riesgo calculado", riesgo?.error || "La API no devolvió datos de riesgo.")}</div>`;
  }

  const lecturas = tele?.lecturas || [];
  if (lecturas.length) {
    const orden = lecturas.slice().sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
    const ult = orden[orden.length - 1];
    html += `<div class="panel">
      <h2>Telemetría real · ${orden.length} lecturas</h2>
      <div class="dos">
        ${grafica(orden.map((r) => r.temp), "Temperatura", "°C", "#f85149")}
        ${grafica(orden.map((r) => r.vib), "Vibración", "mm/s", "#d29922")}
        ${grafica(orden.map((r) => r.combustible_lph), "Combustible", "L/h", "#4a9eff")}
        ${grafica(orden.map((r) => r.rpm), "RPM", "rpm", "#a371f7")}
      </div>
      <p class="sutil" style="margin-top:12px">
        Última lectura ${esc(ult.ts || "")} · zona ${esc(ult.zona || "")} ·
        estado ${esc(ult.estado || "")} · carga ${num(ult.carga_t)} t ·
        clima ${esc(ult.contexto?.clima || "—")} · terreno ${esc(ult.contexto?.terreno || "—")} ·
        lote ${esc(ult.contexto?.lote_combustible || "—")}
      </p>
    </div>`;
  } else {
    html += `<div class="panel">${vacio("Sin telemetría", tele?.error || "Esta máquina todavía no tiene lecturas cargadas.")}</div>`;
  }

  $("#detalle").innerHTML = html;
}

function pintarIncidentes(d) {
  const inc = d?.incidentes || [];
  if (!inc.length) {
    $("#lista-incidentes").innerHTML = vacio(
      "No hay incidentes abiertos",
      "La telemetría está cargada pero todavía no se ha disparado ningún incidente. Se llena solo en cuanto la detección abra el primero."
    );
    return;
  }
  $("#lista-incidentes").innerHTML = `<div class="scroll"><table>
    <thead><tr><th>Incidente</th><th>Máquina</th><th>Zona</th><th>Severidad</th><th>Estado</th><th>Técnico</th><th>Causa</th></tr></thead>
    <tbody>${inc.map((i) => `<tr>
      <td><strong>${esc(i.incidente_id)}</strong></td>
      <td>${esc(i.maquina_id)}</td>
      <td>${esc(i.zona)}</td>
      <td><span class="chip ${esc(i.severidad)}">${esc(i.severidad)}</span></td>
      <td>${esc(i.estado)}</td>
      <td>${esc(i.tecnico_id || "sin asignar")}</td>
      <td>${esc(i.causa_probable || "—")}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

function pintarTecnicos(d) {
  const t = d?.tecnicos || [];
  if (!t.length) {
    $("#lista-tecnicos").innerHTML = vacio("No hay técnicos registrados", "Falta ejecutar la semilla de datos en la API de AWS.");
    return;
  }
  $("#lista-tecnicos").innerHTML = `<div class="scroll"><table>
    <thead><tr><th>ID</th><th>Nombre</th><th>Especialidades</th><th>Zona</th><th>Disponibilidad</th></tr></thead>
    <tbody>${t.map((x) => `<tr>
      <td><strong>${esc(x.tecnico_id)}</strong></td><td>${esc(x.nombre)}</td>
      <td>${esc((x.especialidades || []).join(", "))}</td>
      <td>${esc(x.zona)}</td><td>${esc(x.disponibilidad)}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

function pintarPreventivo(d) {
  const o = d?.ordenes || [];
  if (!o.length) { $("#lista-preventivo").innerHTML = vacio("Sin órdenes", "No hay plan preventivo disponible."); return; }
  $("#lista-preventivo").innerHTML = `<div class="scroll"><table>
    <thead><tr><th>Máquina</th><th>Fecha</th><th>Horas</th><th>Estado</th><th>Motivo</th></tr></thead>
    <tbody>${o.map((x) => `<tr>
      <td><strong>${esc(x.maquina_id)}</strong></td><td>${esc(x.fecha_recomendada)}</td>
      <td>${num(x.horas_proyectadas, 0)}</td><td>${esc(x.estado)}</td><td>${esc(x.motivo)}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

function pintarRepuestos(d) {
  const s = d?.stock || [];
  if (!s.length) { $("#lista-repuestos").innerHTML = vacio("Sin stock", "No hay datos de repuestos."); return; }
  $("#lista-repuestos").innerHTML = `<div class="scroll"><table>
    <thead><tr><th>Zona</th><th>Componente</th><th>Stock</th><th>Mín.</th><th>Demanda 7d</th><th>Estado</th></tr></thead>
    <tbody>${s.map((x) => `<tr>
      <td>${esc(x.zona)}</td><td>${esc(x.componente)}</td><td>${esc(x.stock)}</td>
      <td>${esc(x.minimo)}</td><td>${esc(x.demanda_proyectada_7d)}</td>
      <td><span class="chip ${x.estado === "faltante_proyectado" ? "critica" : x.estado === "sobre_stock" ? "media" : "operativa"}">${esc(String(x.estado).replace(/_/g, " "))}</span></td>
    </tr>`).join("")}</tbody></table></div>`;
}

function pintarNotificaciones(d) {
  const n = d?.notificaciones || [];
  if (!n.length) {
    $("#lista-notificaciones").innerHTML = vacio("Bandeja vacía", "No hay notificaciones para este rol todavía. Se llenan cuando el flujo de incidentes notifica.");
    return;
  }
  $("#lista-notificaciones").innerHTML = n
    .map((x) => `<div class="panel" style="background:var(--panel2);margin-bottom:10px">
      <div style="display:flex;gap:10px;align-items:center;margin-bottom:6px">
        <span class="chip ${esc(x.severidad)}">${esc(x.severidad)}</span>
        <strong>${esc(x.titulo)}</strong>
        <span class="sutil" style="margin-left:auto">${esc(x.ts)}</span>
      </div>
      <div>${esc(x.mensaje)}</div>
      <div class="sutil" style="margin-top:6px">${esc(x.incidente_id || "")}</div>
    </div>`)
    .join("");
}

function burbuja(clase, texto, meta) {
  const d = document.createElement("div");
  d.className = "burbuja " + clase;
  d.textContent = texto;
  if (meta) { const s = document.createElement("span"); s.className = "meta"; s.textContent = meta; d.appendChild(s); }
  $("#hilo").appendChild(d);
  $("#hilo").scrollTop = $("#hilo").scrollHeight;
  return d;
}

async function preguntar(e) {
  e.preventDefault();
  const txt = $("#pregunta").value.trim();
  if (!txt) return;
  $("#pregunta").value = "";
  burbuja("usuario", txt);
  const esperando = burbuja("asistente", "Pensando…");
  try {
    const d = await pedir("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mensaje: txt, rol: $("#rol").value, historial: estado.historial.slice(-10) }),
    });
    const origen = d.fuente === "modelo" ? "Gemini" : "plantilla";
    const fuentes = (d.datos_usados || []).map((u) => u.endpoint).join(", ");
    esperando.textContent = d.respuesta;
    const s = document.createElement("span");
    s.className = "meta";
    s.textContent = `${origen} · ${d.latencia_ms} ms${fuentes ? " · datos: " + fuentes : ""}`;
    esperando.appendChild(s);
    estado.historial.push({ autor: "usuario", texto: txt }, { autor: "asistente", texto: d.respuesta });
  } catch (err) {
    esperando.textContent = "No pude responder: " + err.message;
    esperando.classList.add("err");
  }
}

async function cargar() {
  avisar("");
  modoDemo = false;
  const fallos = [];
  const una = async (ruta, pintar) => {
    try { pintar(await pedir(ruta)); } catch (e) { fallos.push(ruta); pintar(null); }
  };

  try {
    const k = await pedir("/api/kpis");
    estado.kpis = k;
    pintarKpis(k);
  } catch (e) { fallos.push("/api/kpis"); }

  try {
    const f = await pedir("/api/flota");
    estado.flota = f.maquinas || [];
    pintarMapa(estado.flota);
    pintarFlota(estado.flota);
    const sel = $("#sel-maquina");
    const ids = [...new Set(estado.flota.map((m) => m.maquina_id))].sort();
    const extra = Array.from({ length: 20 }, (_, i) => "M-" + String(i + 1).padStart(3, "0"));
    const todas = [...new Set([...ids, ...extra])].sort();
    sel.innerHTML = todas.map((id) => `<option value="${id}">${id}</option>`).join("");
  } catch (e) { fallos.push("/api/flota"); }

  await una("/api/incidentes", pintarIncidentes);
  await una("/api/tecnicos", pintarTecnicos);
  await una("/api/preventivo", pintarPreventivo);
  await una("/api/repuestos", pintarRepuestos);
  await una(`/api/notificaciones?rol=${$("#rol").value}`, pintarNotificaciones);

  if (modoDemo) {
    avisar(
      "DATOS DE DEMOSTRACIÓN — la API de operaciones no responde. Lo que ves es una captura " +
      "guardada del 15/11, no el estado actual de la flota. No usar así en la demo en vivo."
    );
  } else if (fallos.length) {
    avisar("No respondieron: " + fallos.join(", "));
  }
}

montarNav();
$("#recargar").onclick = cargar;
$("#rol").onchange = cargar;
$("#ver-maquina").onclick = verMaquina;
$("#form-chat").onsubmit = preguntar;
cargar();

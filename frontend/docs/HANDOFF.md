# ArquiFusion · Entrega para UX/UI y desarrollo

Prototipo navegable: abrir `index.html` (no requiere servidor). Datos simulados.

## Estructura
- `index.html`: marcado de login, app, panel de detalle, asistente y avisos.
- `css/styles.css`: todos los estilos. Los tokens están al inicio, en `:root`.
- `js/data.js`: datos de ejemplo (máquinas, técnicos, incidentes, repuestos, usuarios y menú por rol).
- `js/app.js`: estado, vistas por rol y eventos.

## Login y roles
Contraseña de prueba `demo`. Correos: `operador@`, `tecnico@`, `supervisor@`, `gerente@arquifusion.pe`.
Cada rol entra a su propia vista. En producción: Firebase Auth con un campo `rol` por usuario, y el backend filtra por rol (no solo el frontend).

## Diseño (para UX/UI)
**Dirección:** industrial y legible a la intemperie. Amarillo de seguridad sobre grises neutros, tipografía condensada para cifras.
**Tipografía:** Barlow Condensed (títulos y cifras, 600/700) y Barlow (texto, 400/500/600). Fallback: system-ui.
**Colores** (claro / oscuro):
| Token | Claro | Oscuro | Uso |
|---|---|---|---|
| `--bg` | #f3f1ec | #14171c | fondo |
| `--pn` | #ffffff | #1c2027 | tarjetas |
| `--sh` | #ece9e1 | #232831 | fondos secundarios |
| `--ln` | #dcd8cf | #2d333d | bordes |
| `--tx` / `--mu` | #1d2026 / #6b7280 | #e8e6e1 / #98a0ab | texto / texto suave |
| `--amb` | #f2b01e | #f2b01e | acción principal, foco |
| `--gn` | #1f8f5f | #4cc38a | operativa, bien |
| `--rd` | #d23c2d | #ef5b4c | requiere intervención, crítico |
| `--gy` | #8a919c | #7c8591 | detenida |
**Estados:** verde operativa, gris detenida, rojo requiere intervención, ámbar atención. Nunca solo color: siempre acompañado de texto.
**Componentes:** tarjeta KPI (borde izquierdo por estado), mapa por zonas con puntos, tarjeta de incidente (borde por severidad), barra de nivel, tabla, panel lateral, burbuja de asistente, aviso temporal.
**Accesibilidad:** foco visible, botones de 40 px mínimo, `aria-label` en puntos del mapa, respeta `prefers-reduced-motion` y `prefers-color-scheme`.
**Pendientes de diseño sugeridos:** pestaña de combustible (RF-06), plan semanal (RF-12), pantalla de pares inusuales (RF-08), estados vacíos y de error con datos reales, versión de impresión para el gerente.

## KPIs por rol
- **Operador:** estado de mi máquina, horas en turno, consumo L/h, avisos. Acción: reportar problema.
- **Técnico:** asignados a mí, respuesta media, reincidencia propia, cerrados hoy. Acciones: voy en camino, llegué, cerrar con intervención. Pestaña de repuestos.
- **Supervisor:** operativas, detenidas, requieren intervención, incidentes abiertos, reparación media, técnicos libres. Mapa, cola con técnico sugerido, causa común, preventivo y repuestos.
- **Gerente:** disponibilidad de flota, costo diario por máquina, utilización, reincidencia, paradas evitadas, repuestos en riesgo. Costo por zona, precisión y sensibilidad del modelo, máquinas con baja utilización o costo alto.

## Para desarrollo: de datos de ejemplo a API
| Variable en `data.js` | Endpoint del plan |
|---|---|
| `M` (máquinas) | `GET /flota`, `GET /maquinas/{id}` |
| `I` (incidentes) | `GET /incidentes`; `POST /incidentes/{id}/cierre` |
| `T` (técnicos) | `GET /tecnicos` |
| `R` (repuestos) | `GET /repuestos` |
| avisos (`N`, función `notify`) | `GET /notificaciones?rol=` |
| riesgo `r` y preventivo | `GET /preventivo`, resultados de `POST /scores` |
| KPIs del gerente y supervisor | `GET /kpis` (hoy se calculan en `view()`) |
| asistente (`ask`) | `POST /ia/chat` |
| puntaje de técnico (`score`) | RF-05, pesos en SSM, calculado en backend |
Campos de máquina: `id, mod, z (zona), x/y (posición en el mapa), e (op/det/int), r (riesgo %), t (temp), v (vibración), p (presión), c (combustible), h (horas turno), u (utilización %), $ (costo diario)`.
Mapear lat/lon reales a x/y de cada zona.
Valores fijos a reemplazar: reparación media, respuesta media, reincidencia, paradas evitadas, precisión y sensibilidad.

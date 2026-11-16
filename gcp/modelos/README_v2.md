# Modelo de falla v2

Mismo problema y misma particion temporal que el v1 (train / hueco / val / hueco / test).
Lo unico que cambia son las variables y la capacidad del arbol.

## Por que el v1 se quedaba en 0.59 de precision

El v1 usaba 3 variables crudas (temp, vib, combustible) en 9 features. Con eso, la
probabilidad maxima que llegaba a emitir era 0.79, asi que **subir el umbral no servia**:
por encima de 0.8 no predecia nada y la precision no pasaba de 0.594. No era cuestion de
calibrar, faltaba senal.

## Que anade el v2

La tabla `telemetria` ya traia presion, carga_t, velocidad, rpm, horas_op, modelo, zona y
el contexto, y nada de eso se usaba. El v2 los incorpora y ademas deriva:

| Familia | Idea |
|---|---|
| `*_sd24` | Volatilidad a 24 h. Una maquina que se degrada no solo sube de media, empieza a oscilar. |
| `*_vol_rel` | Volatilidad de 24 h dividida entre la de 14 dias. Detecta el cambio aunque la maquina sea ruidosa de base. |
| `*_z_propio` | Desviacion contra su propio historial, no contra la flota. Es lo que pide el enunciado. |
| `comb_por_carga` | Litros por tonelada. Sube cuando el motor pierde eficiencia. |
| `temp_por_rpm` | Se calienta mas para el mismo regimen: senal clasica de refrigeracion. |
| `*_max24` | Picos que no mueven la media pero importan. |

`ML.FEATURE_IMPORTANCE` confirma que sirven: 7 de las 12 variables mas usadas son nuevas,
encabezadas por `comb_d14`, `horas_op` y `vib_vol_rel`.

## Resultados en test (nadie entreno con esta particion)

| Modelo | Umbral | Precision | Sensibilidad |
|---|---|---|---|
| boosted v1 | 0.70 | 0.594 | 0.472 |
| **boosted v2** | 0.60 | **0.641** | **0.490** |
| **boosted v2** | 0.74 | **0.945** | 0.344 |
| **boosted v2** | 0.76 | **0.987** | 0.255 |

El v2 gana al v1 en precision y sensibilidad a la vez en el punto equilibrado, y ademas
abre un rango de alta precision que el v1 no podia alcanzar.

## Elegir el umbral

El umbral se elige **en validacion**, nunca en test. En validacion:

| Umbral | Precision val | Sensibilidad val |
|---|---|---|
| 0.60 | 0.614 | 0.465 |
| 0.74 | 0.841 | 0.324 |
| 0.76 | 0.908 | 0.270 |

Mas precision significa menos fallas detectadas. A 0.76 se aciertan casi todas las alarmas
pero se escapan 3 de cada 4 fallas. Hay que decir cual se eligio y por que, no solo ensenar
el numero bonito.

## Lo que NO cambia

El `pesos_modelo` que consume la Lambda de A sigue saliendo del **logit v1**, porque la
Lambda solo sabe calcular esas 9 variables. Para que la inferencia en linea mejore, A tiene
que anadir las variables nuevas. Mientras tanto: v2 para los scores por lote, logit v1 para
el riesgo en linea.

## Orden

1. `12_features_v2.sql`
2. `13_modelo_boosted_v2.sql` (tarda unos 5 min)
3. `15_metricas_v2.sql`
4. `14_umbrales_v2.sql` para ver la curva completa

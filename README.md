Routing 2025
=============

Resumen
------
Proyecto para construir una red de troncales a partir de datos de paradas y tiempos, y asignar orígenes-destinos de usuarios a secuencias de troncales (con posibilidad de transfers). Se valida disponibilidad temporal por arista y se usa búsqueda por coste (Dijkstra) con penalización por transferencias y coste a pie entre transfer-stops.

Objetivo
-------
- Generar una "red" de troncales exportable en `data/outputs/red.csv`.
- Encontrar rutas troncalizadas para cada usuario en `data/outputs/OxD_assignment.csv` (root / no_root, transfer stops, notas).
- Mantener los datos sensibles/large CSV locales fuera del repositorio.

Estructura del repositorio
-------------------------
- `routing/src/` : código fuente (io_utils, network, assign, cli)
- `routing/data/input/` : inputs (NO subir al repo, ver abajo formatos)
- `routing/data/outputs/` : resultados generados por la ejecución
- `tests/` : pruebas unitarias

Formatos de entrada (CSV)
------------------------
Se espera que los CSV estén en `routing/data/input/` y tengan el siguiente esquema (encabezados exactos):

1) `paradas.csv` (paradas / stops)
- stop_id: identificador textual (ej. "07101")
- name: nombre de la parada
- lat: latitud decimal (WGS84)
- lon: longitud decimal (WGS84)

Ejemplo:
stop_id,name,lat,lon
07101,Portal A,4.7100,-74.0700

2) `tiempos_reco.csv` (aristas / recorridos con ventanas)
- from_id: stop_id de origen (string)
- to_id: stop_id destino (string)
- start_time: hora inicio en formato HH:MM:SS (puede estar vacía si siempre disponible)
- end_time: hora fin en formato HH:MM:SS (puede cruzar medianoche, p.ej. 23:00:00 -> 03:00:00)
- day_type: opcional, p.ej. "habitual" o "sabado" (si está vacío se asume 'habitual')
- mean_minutes: tiempo medio en minutos (decimal)
- mode: etiqueta del modo (ej. "bus:brt", "a_pie")

Notas:
- `start_time` y `end_time` pueden ser vacíos. Si `start_time > end_time` se interpreta como ventana que cruza la medianoche.
- Múltiples filas para la misma arista y distintos `day_type` están permitidas; el algoritmo preferirá coincidencia exacta de `day_type`.

Ejemplo:
from_id,to_id,start_time,end_time,day_type,mean_minutes,mode
07101,07102,05:00:00,23:59:59,habitual,2.5,bus:brt

3) `usuarios.csv` (origen-destino)
- user_id: identificador del usuario
- orig_lat, orig_lon: coordenadas de origen (decimal)
- dest_lat, dest_lon: coordenadas de destino (decimal)
- depart_time: hora de salida preferida en HH:MM:SS (opcional)
- day_type: tipo de día (opcional: "habitual", "sabado", ...)
- max_transfers: entero opcional (máximo transfers aceptados)
- mode_prefs: opcional; lista separada por ';' de modos preferidos (ej. "bus:brt;a_pie")

Ejemplo:
user_id,orig_lat,orig_lon,dest_lat,dest_lon,depart_time,day_type,max_transfers,mode_prefs
U00001,4.7101,-74.0701,4.7201,-74.0001,08:30:00,habitual,2,bus:brt

Qué genera el pipeline
---------------------
Al ejecutar el CLI se producen como mínimo:

- `routing/data/outputs/red.csv`: lista de troncales (troncal_id, stop_sequence, stops, modes).
- `routing/data/outputs/troncal_intersections.csv`: pares de troncales que comparten paradas.
- `routing/data/outputs/OxD_assignment.csv`: asignaciones por usuario. Columnas clave:
  - user_id
  - origin_stop_id/name/dist (parada inferida más cercana)
  - dest_stop_id/name/dist
  - root: lista/sequence de `troncal_id` asignados (vacío si no se encontró)
  - transfers: número entero
  - transfers_stops: lista de paradas de transferencia
  - no_root: posibles pares evaluados si no se encontró camino
  - notes: mensajes adicionales (ej. excede max_transfers)

Ejemplos de salida (filas ejemplo)

red.csv (una fila):
```
troncal_1,07101->07102->07103,07101|07102|07103,bus:brt
```

troncal_intersections.csv (una fila):
```
troncal_1,troncal_5,07102|07103
```

OxD_assignment.csv (cabecera y fila de ejemplo):
```
user_id,origin_stop_id,origin_stop_name,origin_dist_m,dest_stop_id,dest_stop_name,dest_dist_m,root,transfers,transfers_stops,no_root,notes
U00001,07101,"Portal A",120.3,07111,"Portal B",850.0,"['troncal_1']",0,,,
```

Instrucciones de uso (local)
---------------------------
1) Asegúrate de tener un entorno Python 3.10+ e instalar deps:
```bash
python -m venv .venv
.venv/bin/python -m pip install -r routing/requirements.txt
```
2) Coloca tus CSV locales en `routing/data/input/` (asegúrate de que están ignorados por `.gitignore`).
3) Ejecuta la pipeline (opciones para tunear costes):
```bash
PYTHONPATH=routing python3 -m src.cli --transfer-penalty 1.0 --walk-km-factor 0.001 --mode-penalty 0.5
```
4) Resultados en `routing/data/outputs/`.

Privacidad y datos
-----------------
- Este repo no incluye tus CSV de entrada. Añade cualquier dato sensible en `routing/data/input/` localmente.

Antes de hacer push
-------------------
- Asegúrate de que `routing/data/input/*.csv` no estén trackeados por git (ya están en `.gitignore`).
- Revisa `git status` y confirma que no hay archivos CSV en la lista de cambios.

Afinado de parámetros
---------------------
- `--transfer-penalty`: penalización base por cada transfer (default 1.0). Aumentar favorece rutas con menos transfers.
- `--walk-km-factor`: coste agregado proporcional a la distancia a pie entre transfer-stops (metros * factor).
- `--mode-penalty`: penaliza troncales que no coincidan con `mode_prefs` del usuario.

Para experimentar con parámetros automáticamente, usa un script que lance la CLI con distintos valores y compare métricas (por ejemplo: nº usuarios con `root`, distribución de transfers).

Notas de desarrollo
------------------
- Tests rápidos: `pytest -q` (usa el venv configurado).
- Puntos de mejora: afinado de pesos, logging por niveles, pruebas más exhaustivas.

Licencia
-------
- Añade aquí la licencia que prefieras antes de publicar en GitHub.
Suggested: MIT / Apache-2.0. Replace this line with your chosen license text or a `LICENSE` file.
# route_2025

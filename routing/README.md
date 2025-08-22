Proyecto de ejemplo para construir la "red" de troncales a partir de archivos CSV y asignar usuarios.

Estructura:
- data/input: archivos CSV fuente (paradas.csv, tiempos_reco.csv, usuarios.csv, modes_catalog.csv)
- data/outputs: salidas (red.csv, OxD_assignment.csv) y logs/
- src/: código Python

Uso rápido (desde la carpeta `routing`):

1) Ejecutar el script principal:

```bash
python -m src.cli
```

Resultados:
- `data/outputs/red.csv` : troncales detectadas
- `data/outputs/OxD_assignment.csv` : asignaciones básicas de usuarios a troncales
- `data/outputs/logs/app.log` : log de ejecución

Notas:
- Implementación inicial simple, pensada para ser extendida. Se respetan los tipos solicitados al leer los CSVs.

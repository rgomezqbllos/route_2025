# Changelog

## v0.2.0 — Connected-stop mapping, near transfers, temporal checks

Highlights:
- Prefer connected stops: origin/destination now snap to the nearest stop that belongs to some troncal, improving coverage and avoiding dead stops.
- Multiple O/D candidates: seed routing with the K nearest troncal stops (default K=3).
- Near-stop transfers: allow transfers between troncales whose stops are within a configurable walking threshold (default 250–400 m), with cost proportional to walking distance.
- Segment-level temporal validation: validate availability per segment using `depart_time`, `day_type`, and `mean_minutes`; annotate infeasible cases as `temporal_infeasible`.
- CLI tuning flags: `--near-transfer-m`, `--k-nearest`, `--max-nearest-m`, `--max-users`, `--quiet`.

Files changed:
- `routing/src/assign.py`: connected-stop selection, K-nearest seeding, near-transfer neighbors, edge weights, segment temporal checks, improved notes.
- `routing/src/io_utils.py`: propagate user `day_type` when present.
- `routing/src/cli.py`: new flags, quiet mode, max-users, and wiring of new parameters.
- `routing/scripts/tune_params.py`: grid search over parameters on a user sample with summary metrics.

Suggested run:
```
PYTHONPATH=routing .venv/bin/python -m src.cli \
  --transfer-penalty 1.0 \
  --walk-km-factor 0.001 \
  --mode-penalty 0.5 \
  --near-transfer-m 400 \
  --k-nearest 3 \
  --max-nearest-m 2000 \
  --quiet
```

Metrics snapshot (sample 2k users):
- Assigned: up to 1,782/2,000 with `near_transfer_m=400`, `k_nearest=3`, `transfer_penalty=1.0`.
- Not assigned (no path): ~218.
- Temporal infeasible flagged: ~545.

Tag: `v0.2.0` has been created and pushed to origin.

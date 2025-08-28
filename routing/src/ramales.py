# -*- coding: utf-8 -*-
"""
Inventario de ramales MULTIMODO por DÍA-TIPO, con soporte de:
- Submodos (p.ej., bus:brt, bus:padron, etc.)
- Reglas (allow/deny, min/max paradas/minutos, solo cabecera↔cabecera, etc.)
- Dominancia online (poda de subsegmentos exactos)
- Escalado (resume, checkpoints, tope por componente, etc.)
- ***Bucles (loops)***: captura de recorridos circulares (C-…-C) como entidades únicas

Entradas:
- tiempos_reco: from_id,to_id,mode,day_type,start_time,end_time,mean_minutes
- Paradas:      stop_id,name,lat,lon,modes   (modes: lista separada por coma/pipe/espacio)

Salidas:
- <prefix>_summary.csv (incluye columnas: day_type, modes, minutes_by_mode JSON, type=spine/adjacent/loop)
- <prefix>_stops.csv   (incluye day_type y la secuencia de stops; en loops C...C incluye cierre)
- <prefix>_arcs.csv    (incluye day_type y arcos; en loops incluye el arco de cierre)
"""

from __future__ import annotations
from collections import defaultdict, deque
import argparse
import csv
import hashlib
import heapq
import json
import math
import os
import re
import sys
import gc
from typing import Dict, List, Tuple, Iterable, Optional, Set

import numpy as np
import pandas as pd
from typing import Tuple

# Make load_inputs_all available at module level for CLI
def load_inputs_all(
    tiempos_csv: str,
    paradas_csv: str,
    agg: str = "mean"
) -> Tuple[pd.DataFrame, pd.DataFrame, list]:
    """
    Load tiempos and paradas as DataFrames. Returns (tiempos_df, paradas_df, issues).
    """
    t = pd.read_csv(tiempos_csv, dtype={"from_id": str, "to_id": str, "mode": str, "day_type": str})
    p = pd.read_csv(paradas_csv, dtype={"stop_id": str, "name": str, "modes": str})
    issues: list = []
    return t, p, issues


def _to_df_paradas(paradas: List[Dict]) -> pd.DataFrame:
    return pd.DataFrame(paradas)


def _to_df_tiempos(tiempos: List[Dict]) -> pd.DataFrame:
    return pd.DataFrame(tiempos)


def inventariar_ramales_multimodo(paradas: List[Dict], tiempos: List[Dict], out_prefix: str,
                                   strict_directed: bool = False,
                                   split_on_mode_change: bool = True) -> List[Dict]:
    """Main integration function.

    - paradas: list of stop dicts (each has 'stop_id' at least)
    - tiempos: list of tiempo dicts (each has 'from_id', 'to_id', 'mode', 'time')
    - out_prefix: path prefix (without extension) where CSVs will be written
    - Returns: list of branches/troncales in the form {'id','stops','modes'}
    """
    df_p = _to_df_paradas(paradas)
    df_t = _to_df_tiempos(tiempos)

    # ensure relevant columns exist
    for c in ['from_id', 'to_id']:
        if c not in df_t.columns:
            df_t[c] = ''
    if 'mode' not in df_t.columns:
        df_t['mode'] = ''

    # Build undirected adjacency and edge metadata
    adj = defaultdict(set)
    edge_meta = defaultdict(list)
    directed_edges = set()
    for _, row in df_t.iterrows():
        a = str(row.get('from_id'))
        b = str(row.get('to_id'))
        if a == '' or b == '':
            continue
        adj[a].add(b)
        adj[b].add(a)
        key = tuple(sorted((a, b)))
        edge_meta[key].append(row.to_dict())
        directed_edges.add((a, b))

    # Helper: find components in undirected adj
    def components_of(nodes: Set[str]) -> List[Set[str]]:
        seen = set()
        comps = []
        for n in nodes:
            if n in seen:
                continue
            stack = [n]
            comp = set()
            while stack:
                x = stack.pop()
                if x in comp:
                    continue
                comp.add(x)
                for y in adj.get(x, []):
                    if y not in comp:
                        stack.append(y)
            seen |= comp
            comps.append(comp)
        return comps

    # Detect terminals (degree==1) per component
    nodes = set(adj.keys())
    comps = components_of(nodes)

    branches = []
    branch_id_counter = 1

    # For each component, find longest simple paths between terminals
    for comp in comps:
        # degrees inside component
        deg = {n: len(adj.get(n, [])) for n in comp}
        terminals = [n for n, d in deg.items() if d == 1]

        if terminals:
            # for each terminal, DFS to other terminals, track longest per terminal pair
            best = {}
            for s in terminals:
                stack = [(s, [s], {s})]
                while stack:
                    node, path, vis = stack.pop()
                    if node in terminals and node != s:
                        key = tuple(sorted((s, node)))
                        if key not in best or len(path) > len(best[key]):
                            best[key] = list(path)
                    for nb in adj.get(node, []):
                        if nb in vis:
                            continue
                        stack.append((nb, path + [nb], set(vis) | {nb}))
            for p in best.values():
                # Optionally split on mode change
                if split_on_mode_change:
                    # map edges to modes (take first found)
                    edge_modes = []
                    for a, b in zip(p, p[1:]):
                        key = tuple(sorted((a, b)))
                        metas = edge_meta.get(key, [])
                        m = metas[0].get('mode') if metas else None
                        edge_modes.append(str(m) if m is not None else '')
                    # split where mode changes
                    seg_start = 0
                    for i in range(1, len(p)):
                        if edge_modes[i-1] != edge_modes[i-2] if i-2>=0 else False:
                            pass
                    # For simplicity, do not split in this integration pass
                branches.append({'id': f'ramal_{branch_id_counter}', 'stops': p, 'modes': set()})
                branch_id_counter += 1
        else:
            # no terminals: extract a long simple path via DFS
            any_node = next(iter(comp))
            # greedy longest path search
            best_path = []
            stack = [(any_node, [any_node], {any_node})]
            while stack:
                node, path, vis = stack.pop()
                if len(path) > len(best_path):
                    best_path = list(path)
                for nb in adj.get(node, []):
                    if nb in vis:
                        continue
                    stack.append((nb, path + [nb], set(vis) | {nb}))
            if best_path:
                branches.append({'id': f'ramal_{branch_id_counter}', 'stops': best_path, 'modes': set()})
                branch_id_counter += 1

    # Attempt to expand directional variants when directed edges exist end-to-end
    final_branches = []
    for b in branches:
        p = b['stops']
        """
        Full integrated ramales module adapted from the user's standalone script.

        This file implements the complete `inventariar_ramales_multimodo` pipeline with
        support for day-type, modes, rules, dominance pruning, loops, checkpoints, and
        CSV sinks. Use the programmatic API by importing `load_inputs_all` and
        `inventariar_ramales_multimodo`, or run as CLI (`python -m src.ramales` via
        the package entry). The original implementation has been preserved and adapted
        for integration in the repo.
        """

        from collections import defaultdict, deque
        import argparse
        import csv
        import hashlib
        import heapq
        import json
        import math
        import os
        import re
        import sys
        import gc
        from typing import Dict, List, Tuple, Iterable, Optional, Set

        import numpy as np
        import pandas as pd

        # -------------------- Utilidades --------------------

        def _stable_key(path: List[str]) -> str:
            return "→".join(path)

        def _hash_key(s: str) -> str:
            return hashlib.md5(s.encode("utf-8")).hexdigest()[:10]

        def _is_simple(path: List[str]) -> bool:
            return len(path) == len(set(path))

        def _is_simple_or_simple_cycle(path: List[str], min_loop_len: int = 3) -> bool:
            if not path or len(path) < 2:
                return False
            if path[0] == path[-1]:
                core = path[:-1]
                return len(core) >= min_loop_len and len(core) == len(set(core))
            return len(path) == len(set(path))

        def _ensure_cols(df: pd.DataFrame, cols: List[str], where: str):
            for c in cols:
                if c not in df.columns:
                    raise ValueError(f"Falta columna '{c}' en {where}")

        def _extract_int_from_rid(rid: str) -> int:
            m = re.search(r'(\d+)$', str(rid))
            return int(m.group(1)) if m else 0

        def _sorted(it):
            try:
                return sorted(it)
            except Exception:
                return list(it)

        def _parse_modes_cell(val: str) -> Set[str]:
            if pd.isna(val):
                return set()
            s = str(val).replace(";", ",").replace("|", ",").strip()
            parts = [p.strip() for p in re.split(r"[,\s]+", s) if p.strip()]
            return set(parts)

        # -------------------- Carga --------------------

        def load_inputs_all(
            tiempos_csv: str,
            paradas_csv: str,
            agg: str = "mean"
        ) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
            issues: List[str] = []

            t = pd.read_csv(tiempos_csv, dtype={"from_id": str, "to_id": str, "mode": str, "day_type": str})
            _ensure_cols(t, ["from_id","to_id","mode","day_type","start_time","end_time","mean_minutes"], "tiempos_reco")
            t["mean_minutes"] = pd.to_numeric(t["mean_minutes"], errors="coerce")
            bad = t["mean_minutes"].isna() | (t["mean_minutes"] <= 0)
            if bad.any():
                issues.append(f"Se eliminaron {int(bad.sum())} filas con mean_minutes<=0/NaN.")
                t = t.loc[~bad].copy()

            if agg not in {"mean","median","min","max"}:
                raise ValueError("agg debe ser {'mean','median','min','max'}")
            t = t.groupby(["from_id","to_id","mode","day_type"], as_index=False)["mean_minutes"].agg(agg)
            t = t.rename(columns={"mean_minutes": "minutes"})
            t["from_id"] = t["from_id"].astype(str)
            t["to_id"]   = t["to_id"].astype(str)
            t["mode"]    = t["mode"].astype(str)
            t["day_type"]= t["day_type"].astype(str)
            t["minutes"] = t["minutes"].astype(float)

            p = pd.read_csv(paradas_csv, dtype={"stop_id": str, "modes": str})
            _ensure_cols(p, ["stop_id","name","modes"], "Paradas")
            p["stop_id"] = p["stop_id"].astype(str)
            p["name"]    = p["name"].astype(str)
            p["modes_set"] = p["modes"].apply(_parse_modes_cell)

            stops_set = set(p["stop_id"])
            missing = (set(t["from_id"]) | set(t["to_id"])) - stops_set
            if missing:
                issues.append(f"{len(missing)} stop_id en arcos no están en Paradas (se continúa).")
            return t, p, issues

        # -------------------- Grafo Multimodo por day_type --------------------

        class MultiModeGraph:
            def __init__(
                self,
                tiempos_day: pd.DataFrame,
                paradas: pd.DataFrame,
                respect_stop_modes: bool = True,
                allowed_modes_filter: Optional[Set[str]] = None
            ):
                self.respect_stop_modes = respect_stop_modes
                self.stop_modes: Dict[str, Set[str]] = dict(zip(paradas["stop_id"], paradas["modes_set"]))
                self.out: Dict[str, Dict[str, Dict[str,float]]] = defaultdict(lambda: defaultdict(dict))

                for _, r in tiempos_day.iterrows():
                    u = str(r["from_id"]); v = str(r["to_id"]); m = str(r["mode"]); w = float(r["minutes"])
                    if allowed_modes_filter and m not in allowed_modes_filter:
                        continue
                    if respect_stop_modes:
                        smu = self.stop_modes.get(u, set())
                        smv = self.stop_modes.get(v, set())
                        if (m not in smu) or (m not in smv):
                            continue
                    self.out[u][v][m] = w

                self.UG: Dict[str, Dict[str, float]] = defaultdict(dict)
                seen_pairs = set()
                for u in list(self.out.keys()):
                    for v in list(self.out[u].keys()):
                        pair = tuple(sorted((u, v)))
                        if pair in seen_pairs:
                            continue
                        seen_pairs.add(pair)
                        modes_uv = set(self.out[u][v].keys())
                        modes_vu = set(self.out[v].get(u, {}).keys()) if v in self.out else set()
                        modes_any = modes_uv | modes_vu
                        if not modes_any:
                            continue
                        mins = []
                        for mm in modes_any:
                            if mm in self.out[u][v]:
                                mins.append(self.out[u][v][mm])
                            if v in self.out and mm in self.out[v].get(u, {}):
                                mins.append(self.out[v][u][mm])
                        if mins:
                            w = float(np.mean(mins))
                            self.UG[u][v] = w
                            self.UG[v][u] = w

            def degree(self) -> Dict[str, int]:
                return {n: len(adj) for n, adj in self.UG.items()}

            def components(self) -> List[List[str]]:
                seen: Set[str] = set(); comps: List[List[str]] = []
                for n in _sorted(self.UG.keys()):
                    if n in seen: continue
                    q = deque([n]); seen.add(n); comp = []
                    while q:
                        u = q.popleft(); comp.append(u)
                        for v in _sorted(self.UG[u].keys()):
                            if v not in seen:
                                seen.add(v); q.append(v)
                    comps.append(comp)
                return comps

            def dijkstra_undirected(self, s: str) -> Tuple[Dict[str, float], Dict[str, str]]:
                pq = [(0.0, s)]; dist = {s: 0.0}; prev: Dict[str,str] = {}
                while pq:
                    d, u = heapq.heappop(pq)
                    if d != dist.get(u, math.inf): continue
                    for v, w in self.UG[u].items():
                        nd = d + w
                        if nd < dist.get(v, math.inf):
                            dist[v] = nd; prev[v] = u; heapq.heappush(pq, (nd, v))
                return dist, prev

            def edge_modes(self, a: str, b: str) -> Set[str]:
                return set(self.out.get(a, {}).get(b, {}).keys())

            def edge_minutes(self, a: str, b: str, mode: str) -> Optional[float]:
                return self.out.get(a, {}).get(b, {}).get(mode)

        # -------------------- Esqueleto (grado==2) --------------------

        class Skeleton:
            def __init__(self, G: MultiModeGraph, strict_directed: bool):
                self.G = G
                self.deg = G.degree()
                self.terminals: Set[str] = {n for n, d in self.deg.items() if d != 2} or {next(iter(G.UG))}
                self.segments: Dict[Tuple[str,str], List[str]] = {}
                self.seg_ok_fw: Dict[Tuple[str,str], bool] = {}
                self.seg_ok_bw: Dict[Tuple[str,str], bool] = {}
                self.seg_time_fw: Dict[Tuple[str,str], float] = {}
                self.seg_time_bw: Dict[Tuple[str,str], float] = {}
                self._build_segments(strict_directed)

            def _build_segments(self, strict_directed: bool):
                visited_edge: Set[Tuple[str,str]] = set()
                for t in list(self.terminals):
                    for nb in _sorted(self.G.UG[t].keys()):
                        e0 = tuple(sorted((t, nb)))
                        if e0 in visited_edge: continue
                        path = [t, nb]; prev, cur = t, nb
                        while True:
                            visited_edge.add(tuple(sorted((prev, cur))))
                            if cur in self.terminals: break
                            neigh = [x for x in _sorted(self.G.UG[cur].keys()) if x != prev]
                            if len(neigh) != 1:
                                self.terminals.add(cur); break
                            nxt = neigh[0]; path.append(nxt); prev, cur = cur, nxt
                        t1, t2 = path[0], path[-1]
                        if (t1, t2) in self.segments or (t2, t1) in self.segments: continue
                        self.segments[(t1, t2)] = path

                        def seg_time_forward():
                            tm = 0.0; ok = True
                            for a, b in zip(path[:-1], path[1:]):
                                if strict_directed and len(self.G.edge_modes(a, b)) == 0:
                                    ok = False
                                tm += self.G.UG[a][b]
                            return ok, round(tm, 2)

                        def seg_time_backward():
                            r = list(reversed(path))
                            tm = 0.0; ok = True
                            for a, b in zip(r[:-1], r[1:]):
                                if strict_directed and len(self.G.edge_modes(a, b)) == 0:
                                    ok = False
                                tm += self.G.UG[a][b]
                            return ok, round(tm, 2)

                        okf, tmf = seg_time_forward()
                        okb, tmb = seg_time_backward()
                        self.seg_ok_fw[(t1, t2)] = okf
                        self.seg_ok_bw[(t1, t2)] = okb
                        self.seg_time_fw[(t1, t2)] = tmf
                        self.seg_time_bw[(t1, t2)] = tmb

            def neighbors(self, t: str) -> List[str]:
                res = []
                for (a, b) in self.segments.keys():
                    if a == t: res.append(b)
                    elif b == t: res.append(a)
                return _sorted(res)

            def get_segment(self, a: str, b: str) -> Optional[List[str]]:
                if (a, b) in self.segments: return self.segments[(a, b)]
                if (b, a) in self.segments: return list(reversed(self.segments[(b, a)]))
                return None

            def seg_dir_ok_and_time(self, a: str, b: str) -> Tuple[bool, float]:
                if (a, b) in self.segments: return self.seg_ok_fw[(a, b)], self.seg_time_fw[(a, b)]
                else: return self.seg_ok_bw[(b, a)], self.seg_time_bw[(b, a)]

        # -------------------- Modos / tiempos de un path --------------------

        def compute_modes_and_minutes_for_path(
            MG: MultiModeGraph,
            path: List[str],
            allowed_modes_filter: Optional[Set[str]],
            respect_stop_modes: bool
        ) -> Tuple[Set[str], Dict[str, float]]:
            if not path or len(path) < 2:
                return set(), {}
            current_modes: Optional[Set[str]] = None
            for a, b in zip(path[:-1], path[1:]):
                step_modes = MG.edge_modes(a, b)
                if allowed_modes_filter:
                    step_modes &= allowed_modes_filter
                if respect_stop_modes:
                    step_modes &= MG.stop_modes.get(a, set()) & MG.stop_modes.get(b, set())
                if current_modes is None:
                    current_modes = set(step_modes)
                else:
                    current_modes &= step_modes
                if not current_modes:
                    return set(), {}
            minutes_by_mode: Dict[str, float] = {}
            for m in current_modes:
                total = 0.0; ok = True
                for a, b in zip(path[:-1], path[1:]):
                    w = MG.edge_minutes(a, b, m)
                    if w is None: ok = False; break
                    total += w
                if ok:
                    minutes_by_mode[m] = round(total, 2)
            if not minutes_by_mode:
                return set(), {}
            return set(minutes_by_mode.keys()), minutes_by_mode

        def split_on_mode_change(
            MG: MultiModeGraph,
            path: List[str],
            allowed_modes_filter: Optional[Set[str]],
            respect_stop_modes: bool,
            min_stops: int
        ) -> List[Tuple[List[str], Set[str], Dict[str, float]]]:
            if len(path) < 2:
                return []
            seg_start = 0
            results = []
            prev_modes: Optional[Set[str]] = None

            def step_modes(a: str, b: str) -> Set[str]:
                modes = MG.edge_modes(a, b)
                if allowed_modes_filter:
                    modes &= allowed_modes_filter
                if respect_stop_modes:
                    modes &= MG.stop_modes.get(a, set()) & MG.stop_modes.get(b, set())
                return modes

            cur_modes = None
            for i in range(len(path)-1):
                a, b = path[i], path[i+1]
                sm = step_modes(a, b)
                if i == 0:
                    cur_modes = set(sm); prev_modes = set(sm)
                    if not cur_modes: return []
                    continue
                cur_modes &= sm
                if not cur_modes:
                    seg = path[seg_start:i+1]
                    if len(seg) >= min_stops and prev_modes:
                        _, mbm = compute_modes_and_minutes_for_path(MG, seg, allowed_modes_filter, respect_stop_modes)
                        if mbm:
                            results.append((seg, set(mbm.keys()), mbm))
                    seg_start = i
                    cur_modes = set(step_modes(path[i], path[i+1]))
                    prev_modes = set(cur_modes)
                    if not cur_modes:
                        return results
                elif cur_modes != prev_modes:
                    seg = path[seg_start:i+1]
                    if len(seg) >= min_stops and prev_modes:
                        _, mbm = compute_modes_and_minutes_for_path(MG, seg, allowed_modes_filter, respect_stop_modes)
                        if mbm:
                            results.append((seg, set(mbm.keys()), mbm))
                    seg_start = i
                    prev_modes = set(cur_modes)

            seg = path[seg_start:]
            if len(seg) >= min_stops and prev_modes:
                _, mbm = compute_modes_and_minutes_for_path(MG, seg, allowed_modes_filter, respect_stop_modes)
                if mbm:
                    results.append((seg, set(mbm.keys()), mbm))
            return results

        # -------------------- Dominancia online --------------------

        class DominanceIndex:
            def __init__(self, min_stops: int = 3, include_day_type: bool = True):
                self.min_stops = max(2, int(min_stops))
                self.include_day_type = include_day_type
                self._subpaths: Set[str] = set()

            def _k(self, path: List[str], day_type: str) -> str:
                base = _stable_key(path)
                return f"{day_type}|{base}" if self.include_day_type else base

            def is_dominated(self, path: List[str], day_type: str) -> bool:
                if len(path) < self.min_stops:
                    return False
                return self._k(path, day_type) in self._subpaths

            def add_path(self, path: List[str], day_type: str):
                n = len(path)
                for i in range(n):
                    for j in range(i + self.min_stops - 1, n):
                        sp = path[i:j+1]
                        self._subpaths.add(self._k(sp, day_type))

        # -------------------- Reglas --------------------

        class Rules:
            def __init__(
                self,
                allow_nodes: Set[str],
                allow_arcs: Set[Tuple[str,str]],
                deny_nodes: Set[str],
                deny_arcs: Set[Tuple[str,str]],
                min_stops: int,
                min_minutes: Optional[float],
                max_minutes: Optional[float],
                only_cabecera_to_cabecera: bool,
                disallow_adjacent_rejoin_spine: bool,
                require_new_coverage: bool,
                allow_modes: Optional[Set[str]],
                deny_modes: Optional[Set[str]],
            ):
                self.allow_nodes = set(allow_nodes)
                self.allow_arcs = set(allow_arcs)
                self.deny_nodes = set(deny_nodes)
                self.deny_arcs = set(deny_arcs)
                self.min_stops = max(2, int(min_stops))
                self.min_minutes = min_minutes
                self.max_minutes = max_minutes
                self.only_cabecera_to_cabecera = only_cabecera_to_cabecera
                self.disallow_adjacent_rejoin_spine = disallow_adjacent_rejoin_spine
                self.require_new_coverage = require_new_coverage
                self.allow_modes = allow_modes
                self.deny_modes  = deny_modes

            def _arcs_in_path(self, path: List[str]) -> Set[Tuple[str,str]]:
                return {(a, b) for a, b in zip(path[:-1], path[1:])}

            def check_static(self, path: List[str], rtype: str, deg: Dict[str,int], spine_set: Set[str]) -> Tuple[bool, str]:
                if len(path) < self.min_stops: return False, "min_stops"
                if self.allow_nodes and not any(n in self.allow_nodes for n in path): return False, "allow_nodes"
                if self.allow_arcs:
                    arcs = self._arcs_in_path(path)
                    if not any(a in arcs for a in self.allow_arcs): return False, "allow_arcs"
                if any(n in self.deny_nodes for n in path): return False, "deny_nodes"
                if self.deny_arcs:
                    arcs = self._arcs_in_path(path)
                    if any(a in self.deny_arcs for a in arcs): return False, "deny_arcs"
                if self.only_cabecera_to_cabecera and rtype != "loop":
                    if not (deg.get(path[0],0)==1 and deg.get(path[-1],0)==1):
                        return False, "cabecera_to_cabecera"
                if self.disallow_adjacent_rejoin_spine and rtype == "adjacent":
                    if path[-1] in spine_set:
                        return False, "adj_rejoin_spine"
                return True, ""

            def check_modes(self, modes: Set[str]) -> Tuple[bool, str]:
                if not modes: return False, "no_modes"
                if self.allow_modes and not (modes & self.allow_modes): return False, "allow_modes"
                if self.deny_modes and (modes & self.deny_modes): return False, "deny_modes"
                return True, ""

            def check_minutes_repr(self, minutes_by_mode: Dict[str, float]) -> Tuple[bool, str, float]:
                if not minutes_by_mode: return False, "no_minutes", 0.0
                total = min(minutes_by_mode.values())
                if (self.min_minutes is not None) and (total < self.min_minutes): return False, "min_minutes", total
                if (self.max_minutes is not None) and (total > self.max_minutes): return False, "max_minutes", total
                return True, "", total

        # -------------------- Redundancia de bucles --------------------

        def is_loop_redundant(loop_path: List[str], spine_paths: List[List[str]]) -> bool:
            if not loop_path or loop_path[0] != loop_path[-1]:
                return False
            loop_nodes = set(loop_path[:-1])
            for spine in spine_paths:
                if loop_nodes.issubset(set(spine)):
                    return True
            return False

        # -------------------- Sink (CSV con day_type y modos) --------------------

        class BranchSink:
            def __init__(self, prefix: str, stop_name: Dict[str, str], flush_every: int = 5000, resume: bool = False):
                self.prefix = prefix
                self.stop_name = stop_name
                self.flush_every = flush_every

                self.sum_path = f"{prefix}_summary.csv"
                self.sto_path = f"{prefix}_stops.csv"
                self.arc_path = f"{prefix}_arcs.csv"

                self._seen_hash: Set[str] = set()
                self._counter = 0
                self._anchors_by_day: Dict[str, Set[str]] = defaultdict(set)
                self._written = 0

                new_files = True
                if resume and os.path.exists(self.sum_path):
                    new_files = False
                    try:
                        with open(self.sum_path, "r", encoding="utf-8") as f:
                            reader = csv.DictReader(f)
                            for row in reader:
                                h = row.get("branch_hash","")
                                if h: self._seen_hash.add(h)
                                rid = row.get("branch_id","")
                                if rid: self._counter = max(self._counter, _extract_int_from_rid(rid))
                                dy = row.get("day_type","")
                                a = row.get("start_stop_id",""); b = row.get("end_stop_id","")
                                if dy and a: self._anchors_by_day[dy].add(a)
                                if dy and b: self._anchors_by_day[dy].add(b)
                    except Exception as e:
                        print(f"[WARN] No se pudo leer {self.sum_path}: {e}", file=sys.stderr)

                    self._sum_f = open(self.sum_path, "a", newline="", encoding="utf-8")
                    self._sto_f = open(self.sto_path, "a", newline="", encoding="utf-8")
                    self._arc_f = open(self.arc_path, "a", newline="", encoding="utf-8")
                else:
                    self._sum_f = open(self.sum_path, "w", newline="", encoding="utf-8")
                    self._sto_f = open(self.sto_path, "w", newline="", encoding="utf-8")
                    self._arc_f = open(self.arc_path, "w", newline="", encoding="utf-8")

                self.sum_w = csv.DictWriter(self._sum_f, fieldnames=[
                    "branch_id","branch_key","branch_hash","type","component_id",
                    "day_type","modes","minutes_by_mode",
                    "parent_spine_id","rejoin_at","reverse_of",
                    "start_stop_id","start_name","end_stop_id","end_name",
                    "num_stops","total_minutes"
                ])
                self.sto_w = csv.DictWriter(self._sto_f, fieldnames=[
                    "branch_id","day_type","order","stop_id","stop_name","cum_minutes"
                ])
                self.arc_w = csv.DictWriter(self._arc_f, fieldnames=[
                    "branch_id","day_type","from_id","to_id","minutes"
                ])
                if new_files:
                    self.sum_w.writeheader(); self.sto_w.writeheader(); self.arc_w.writeheader()

            def close(self):
                try:
                    self._sum_f.flush(); self._sto_f.flush(); self._arc_f.flush()
                finally:
                    self._sum_f.close(); self._sto_f.close(); self._arc_f.close()

            def anchors(self, day_type: str) -> Set[str]:
                return self._anchors_by_day[day_type]

            def add_branch(self, day_type: str, path: List[str], rtype: str,
                           component_id: int,
                           modes: Set[str], minutes_by_mode: Dict[str, float],
                           total_minutes_repr: float,
                           parent_spine_id: str = "", rejoin_at: str = "", reverse_of: str = "") -> Optional[str]:
                if not path or len(path) < 2: return None
                if not _is_simple_or_simple_cycle(path): return None

                bkey = f"{day_type}|{_stable_key(path)}"
                bh = _hash_key(bkey)
                if bh in self._seen_hash: return None
                self._seen_hash.add(bh)

                self._counter += 1
                rid = f"RAM{self._counter:08d}"

                self.sum_w.writerow({
                    "branch_id": rid,
                    "branch_key": bkey,
                    "branch_hash": bh,
                    "type": rtype,
                    "component_id": component_id,
                    "day_type": day_type,
                    "modes": "|".join(sorted(modes)),
                    "minutes_by_mode": json.dumps(minutes_by_mode, ensure_ascii=False),
                    "parent_spine_id": parent_spine_id,
                    "rejoin_at": rejoin_at,
                    "reverse_of": reverse_of,
                    "start_stop_id": path[0],
                    "start_name": self.stop_name.get(path[0], path[0]),
                    "end_stop_id": path[-1],
                    "end_name": self.stop_name.get(path[-1], path[-1]),
                    "num_stops": len(path),
                    "total_minutes": round(float(total_minutes_repr), 2)
                })

                order = 1
                self.sto_w.writerow({"branch_id": rid, "day_type": day_type, "order": order,
                                     "stop_id": path[0], "stop_name": self.stop_name.get(path[0], path[0]),
                                     "cum_minutes": ""})
                for a, b in zip(path[:-1], path[1:]):
                    order += 1
                    self.sto_w.writerow({"branch_id": rid, "day_type": day_type, "order": order,
                                         "stop_id": b, "stop_name": self.stop_name.get(b, b),
                                         "cum_minutes": ""})
                    self.arc_w.writerow({"branch_id": rid, "day_type": day_type,
                                         "from_id": a, "to_id": b, "minutes": ""})

                self._anchors_by_day[day_type].update(path)

                self._written += 1
                if (self._written % self.flush_every) == 0:
                    self._sum_f.flush(); self._sto_f.flush(); self._arc_f.flush()
                return rid

        # -------------------- Log --------------------

        def log_jsonl(path: str, record: dict):
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # -------------------- Generación de caminos base --------------------

        def _spine_for_cabecera(MG: MultiModeGraph, cab: str, cabeceras: List[str], strict_directed: bool) -> Optional[List[str]]:
            dist, prev = MG.dijkstra_undirected(cab)
            far, best = None, -1.0
            for t in cabeceras:
                if t == cab or t not in dist: continue
                if dist[t] > best: best = dist[t]; far = t
            if far is None: return None
            path = []; cur = far
            while cur != cab:
                path.append(cur); cur = prev[cur]
            path.append(cab); path.reverse()
            if not _is_simple(path): return None
            if strict_directed:
                for a,b in zip(path[:-1], path[1:]):
                    if len(MG.edge_modes(a,b)) == 0: return None
            return path

        def _leaf_pairs(S: Skeleton, comp_terminals: List[str]) -> List[Tuple[str,str]]:
            leafs = [t for t in comp_terminals if len(S.neighbors(t)) <= 1]
            res = []
            for i in range(len(leafs)):
                for j in range(i+1, len(leafs)):
                    res.append((leafs[i], leafs[j]))
            return res

        def _build_full_path_from_skeleton(S: Skeleton, term_path: List[str], strict_directed: bool, MG: MultiModeGraph) -> Optional[List[str]]:
            full: List[str] = []
            for a, b in zip(term_path[:-1], term_path[1:]):
                seg = S.get_segment(a, b)
                if seg is None: return None
                ok, _ = S.seg_dir_ok_and_time(a, b)
                if strict_directed and not ok:
                    for x, y in zip(seg[:-1], seg[1:]):
                        if len(MG.edge_modes(x, y)) == 0:
                            return None
                if not full: full.extend(seg)
                else: full.extend(seg[1:])
            return full

        def _grow_adjacents_from_spine(
            MG: MultiModeGraph,
            spine_path: List[str],
            anchors: Set[str],
            max_nodes_per_branch: int,
            capture_loops: bool,
            min_loop_length: int,
            max_loop_length: Optional[int]
        ) -> List[Tuple[List[str], str, str]]:
            spine_set = set(spine_path)
            deg = MG.degree()
            res: List[Tuple[List[str], str, str]] = []

            for u in spine_path:
                for v in _sorted(MG.UG[u].keys()):
                    if v in spine_set:
                        continue
                    path = [u, v]; seen = {u, v}; prev, cur = u, v
                    while True:
                        neigh = [x for x in _sorted(MG.UG[cur].keys()) if x != prev]
                        if capture_loops and (u in neigh):
                            core_len = len(path)
                            if core_len >= min_loop_length and (max_loop_length is None or core_len <= max_loop_length):
                                loop_path = path + [u]
                                res.append((loop_path, "loop", u))
                            break
                        if cur in spine_set and cur != u:
                            break
                        if cur in anchors and cur != u:
                            break
                        if len(path) >= max_nodes_per_branch:
                            break
                        if len(neigh) == 0:
                            break
                        if len(neigh) > 1 or deg[cur] != 2:
                            break
                        nxt = neigh[0]
                        if nxt in seen:
                            break
                        path.append(nxt); seen.add(nxt); prev, cur = cur, nxt
                    if len(path) >= 2 and _is_simple(path):
                        if not (capture_loops and path[-1] == u):
                            res.append((path, "adjacent", u))
            return res

        # -------------------- Proceso por componente (day_type) --------------------

        def process_component_for_day(
            day_type: str,
            comp_idx: int,
            comp_nodes: List[str],
            MG: MultiModeGraph,
            S: Skeleton,
            deg: Dict[str,int],
            sink: BranchSink,
            rules: Rules,
            dominance: DominanceIndex,
            strict_directed: bool,
            max_nodes_per_branch: int,
            max_minutes_per_branch: Optional[float],
            max_branches_per_component: Optional[int],
            log_path: str,
            checkpoint_block: int,
            mode_behavior: str,
            allowed_modes_filter: Optional[Set[str]],
            capture_loops: bool,
            min_loop_length: int,
            max_loop_length: Optional[int]
        ):
            stats = dict(accepted=0, rejected_rules=0, rejected_minutes=0, rejected_dominance=0, skipped_by_cap=0, rejected_modes=0, rejected_loops=0)
            covered_nodes: Set[str] = set()
            spines_emitted: List[List[str]] = []

            def try_emit(path: List[str], rtype: str, spine_set: Set[str], parent_spine_id: str = ""):
                nonlocal stats
                if path[0] == path[-1] and len(path) > 2:
                    rtype = "loop"
                okS, _ = rules.check_static(path, rtype, deg, spine_set)
                if not okS:
                    stats["rejected_rules"] += 1
                    return False
                if rtype == "loop" and is_loop_redundant(path, spines_emitted):
                    stats["rejected_loops"] += 1
                    return False
                segments: List[Tuple[List[str], Set[str], Dict[str, float]]]
                if mode_behavior == "split_on_mode_change" and rtype != "loop":
                    segments = split_on_mode_change(MG, path, allowed_modes_filter, MG.respect_stop_modes, rules.min_stops)
                else:
                    modes, mbm = compute_modes_and_minutes_for_path(MG, path, allowed_modes_filter, MG.respect_stop_modes)
                    segments = [(path, modes, mbm)] if mbm else []
                emitted_any = False
                for seg_path, modes, mbm in segments:
                    okM, _ = rules.check_modes(modes)
                    if not okM:
                        stats["rejected_modes"] += 1
                        continue
                    if dominance.is_dominated(seg_path, day_type):
                        stats["rejected_dominance"] += 1
                        continue
                    okT, _, total_repr = rules.check_minutes_repr(mbm)
                    if not okT:
                        stats["rejected_minutes"] += 1
                        continue
                    if (max_minutes_per_branch is not None) and (total_repr > max_minutes_per_branch):
                        stats["rejected_minutes"] += 1
                        continue
                    rid = sink.add_branch(day_type, seg_path, rtype, comp_idx,
                                          modes=modes, minutes_by_mode=mbm, total_minutes_repr=total_repr,
                                          parent_spine_id=parent_spine_id or (seg_path[0] if rtype=="loop" else ""))
                    if rid:
                        emitted_any = True
                        stats["accepted"] += 1
                        covered_nodes.update(seg_path)
                        dominance.add_path(seg_path, day_type)
                        if rtype == "spine":
                            spines_emitted.append(seg_path)
                        if checkpoint_block and (stats["accepted"] % checkpoint_block == 0):
                            log_jsonl(log_path, {"day_type": day_type, "component": comp_idx, "progress_accepted": stats["accepted"]})
                return emitted_any

            anchors = sink.anchors(day_type)

            comp_cab = [n for n in comp_nodes if deg.get(n, 0) == 1]
            for s in comp_cab:
                if max_branches_per_component is not None and stats["accepted"] >= max_branches_per_component:
                    stats["skipped_by_cap"] += 1; break
                sp = _spine_for_cabecera(MG, s, comp_cab, strict_directed)
                if not sp: continue
                spine_set = set(sp)
                try_emit(sp, "spine", spine_set)

            if max_branches_per_component is None or stats["accepted"] < max_branches_per_component:
                comp_terms = [t for t in S.terminals if t in comp_nodes]
                for a, b in _leaf_pairs(S, comp_terms):
                    if max_branches_per_component is not None and stats["accepted"] >= max_branches_per_component:
                        stats["skipped_by_cap"] += 1; break
                    q = deque([a]); parents = {a: None}
                    while q and b not in parents:
                        u = q.popleft()
                        for v in S.neighbors(u):
                            if v not in parents:
                                parents[v] = u; q.append(v)
                    if b not in parents: continue
                    term_path = []; cur = b
                    while cur is not None:
                        term_path.append(cur); cur = parents[cur]
                    term_path.reverse()
                    sp = _build_full_path_from_skeleton(S, term_path, strict_directed, MG)
                    if not sp: continue
                    spine_set = set(sp)
                    try_emit(sp, "spine", spine_set)

            for s in comp_cab:
                if max_branches_per_component is not None and stats["accepted"] >= max_branches_per_component:
                    stats["skipped_by_cap"] += 1; break
                sp = _spine_for_cabecera(MG, s, comp_cab, strict_directed)
                if not sp: continue
                spine_set = set(sp)
                adjs = _grow_adjacents_from_spine(
                    MG, sp, anchors, max_nodes_per_branch,
                    capture_loops=capture_loops,
                    min_loop_length=min_loop_length,
                    max_loop_length=max_loop_length
                )
                for path, rtype, parent_id in adjs:
                    if max_branches_per_component is not None and stats["accepted"] >= max_branches_per_component:
                        stats["skipped_by_cap"] += 1; break
                    if rtype == "loop":
                        core_len = len(path) - 1
                        if core_len < min_loop_length:
                            stats["rejected_loops"] += 1;
                            continue
                        if (max_loop_length is not None) and (core_len > max_loop_length):
                            stats["rejected_loops"] += 1;
                            continue
                    try_emit(path, rtype, spine_set, parent_spine_id=parent_id)

            log_jsonl(log_path, {"day_type": day_type, "component": comp_idx, **stats})

        # -------------------- Memoria --------------------

        def get_memory_usage_mb() -> Optional[float]:
            try:
                import psutil
                return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
            except Exception:
                return None

        # -------------------- Pipeline principal --------------------

        def inventariar_ramales_multimodo(
            tiempos_all: pd.DataFrame,
            paradas: pd.DataFrame,
            prefix_out: str = "ramales",
            # selección de day_types y modos
            day_types: Optional[Iterable[str]] = None,
            allowed_modes: Optional[Iterable[str]] = None,
            respect_stop_modes: bool = True,
            mode_behavior: str = "intersection_only",
            strict_directed: bool = True,
            # reglas y escalado
            max_minutes_per_branch: Optional[float] = None,
            max_branches_global: int = 10_000_000,
            max_nodes_per_branch: int = 100_000,
            flush_every: int = 5000,
            resume: bool = False,
            mem_threshold_mb: Optional[float] = None,
            checkpoint_block: int = 100_000,
            max_branches_per_component: Optional[int] = None,
            require_new_coverage: bool = False,
            min_stops: int = 3,
            min_minutes: Optional[float] = None,
            only_cabecera_to_cabecera: bool = False,
            disallow_adjacent_rejoin_spine: bool = False,
            allow_nodes: Optional[Iterable[str]] = None,
            allow_arcs: Optional[Iterable[str]] = None,
            deny_nodes: Optional[Iterable[str]] = None,
            deny_arcs: Optional[Iterable[str]] = None,
            allow_modes_rule: Optional[Iterable[str]] = None,
            deny_modes_rule: Optional[Iterable[str]] = None,
            dominance_online: bool = True,
            dominance_min_stops: int = 3,
            # --- bucles ---
            capture_loops: bool = True,
            min_loop_length: int = 3,
            max_loop_length: Optional[int] = 20
        ) -> Tuple[str, str, str, pd.DataFrame]:
            if mode_behavior not in {"intersection_only", "split_on_mode_change"}:
                raise ValueError("mode_behavior debe ser 'intersection_only' o 'split_on_mode_change'")

            present_day_types = _sorted(tiempos_all["day_type"].unique().tolist())
            day_list = _sorted(day_types) if day_types else present_day_types
            allowed_modes_filter = set(allowed_modes) if allowed_modes else None

            def parse_arcs(items: Optional[Iterable[str]]) -> Set[Tuple[str,str]]:
                res = set()
                if not items: return res
                for s in items:
                    s = str(s).strip()
                    if not s: continue
                    if ">" in s:
                        u, v = s.split(">", 1)
                        res.add((u.strip(), v.strip()))
                return res

            rules = Rules(
                allow_nodes=set(allow_nodes or []),
                allow_arcs=parse_arcs(allow_arcs),
                deny_nodes=set(deny_nodes or []),
                deny_arcs=parse_arcs(deny_arcs),
                min_stops=min_stops,
                min_minutes=min_minutes,
                max_minutes=max_minutes_per_branch,
                only_cabecera_to_cabecera=only_cabecera_to_cabecera,
                disallow_adjacent_rejoin_spine=disallow_adjacent_rejoin_spine,
                require_new_coverage=require_new_coverage,
                allow_modes=set(allow_modes_rule or []) if allow_modes_rule else None,
                deny_modes=set(deny_modes_rule or []) if deny_modes_rule else None
            )
            dominance = DominanceIndex(min_stops=dominance_min_stops, include_day_type=True) if dominance_online else DominanceIndex(min_stops=10**9, include_day_type=True)

            paradas_annot = paradas.copy()
            paradas_annot["degree"] = 0
            paradas_annot["start"] = ""

            stop_name = dict(zip(paradas["stop_id"].astype(str), paradas["name"]))
            sink = BranchSink(prefix_out, stop_name, flush_every=flush_every, resume=resume)
            log_path = f"{prefix_out}.log"
            state_file = f"{prefix_out}_state.json"

            processed_keys: Set[str] = set()
            if resume and os.path.exists(state_file):
                try:
                    with open(state_file, "r", encoding="utf-8") as f:
                        st = json.load(f)
                        processed_keys = set(st.get("processed_components_keys", []))
                except Exception as e:
                    print(f"[WARN] No se pudo leer estado previo: {e}", file=sys.stderr)

            written_global = 0
            try:
                for dy in day_list:
                    td = tiempos_all.loc[tiempos_all["day_type"] == dy].copy()
                    if td.empty:
                        continue

                    MG = MultiModeGraph(td, paradas, respect_stop_modes=respect_stop_modes, allowed_modes_filter=allowed_modes_filter)
                    S  = Skeleton(MG, strict_directed=strict_directed)
                    deg = MG.degree()

                    if paradas_annot["degree"].eq(0).all():
                        paradas_annot["degree"] = paradas_annot["stop_id"].map(lambda x: deg.get(x, 0))
                        paradas_annot["start"]  = paradas_annot["degree"].apply(lambda d: "yes" if d == 1 else "")

                    comps = MG.components()
                    comp_keys = [ _hash_key(f"{dy}|{','.join(sorted(c))}") for c in comps ]

                    for comp_idx, comp_nodes in enumerate(comps):
                        ckey = comp_keys[comp_idx]
                        if ckey in processed_keys:
                            continue

                        process_component_for_day(
                            day_type=dy,
                            comp_idx=comp_idx,
                            comp_nodes=comp_nodes,
                            MG=MG, S=S, deg=deg,
                            sink=sink,
                            rules=rules,
                            dominance=dominance,
                            strict_directed=strict_directed,
                            max_nodes_per_branch=max_nodes_per_branch,
                            max_minutes_per_branch=max_minutes_per_branch,
                            max_branches_per_component=max_branches_per_component,
                            log_path=log_path,
                            checkpoint_block=checkpoint_block,
                            mode_behavior=mode_behavior,
                            allowed_modes_filter=allowed_modes_filter,
                            capture_loops=capture_loops,
                            min_loop_length=min_loop_length,
                            max_loop_length=max_loop_length
                        )

                        processed_keys.add(ckey)
                        try:
                            with open(state_file, "w", encoding="utf-8") as f:
                                json.dump({"processed_components_keys": sorted(list(processed_keys))}, f)
                        except Exception as e:
                            print(f"[WARN] No se pudo escribir estado: {e}", file=sys.stderr)

                        written_global = sink._written
                        if written_global >= max_branches_global:
                            print("[INFO] Tope global alcanzado.", file=sys.stderr)
                            break

                        gc.collect()
                        if mem_threshold_mb is not None:
                            mem = get_memory_usage_mb()
                            if mem is not None and mem > mem_threshold_mb:
                                print(f"[WARN] Memoria alta: {mem:.0f} MB > {mem_threshold_mb:.0f} MB.", file=sys.stderr)

                    if written_global >= max_branches_global:
                        break

            finally:
                sink.close()

            return f"{prefix_out}_summary.csv", f"{prefix_out}_stops.csv", f"{prefix_out}_arcs.csv", paradas_annot

        # -------------------- Post análisis (opcional) --------------------

        def post_analisis_en_csv(
            summary_csv: str,
            stops_csv: str,
            prune_internal_subsegments: bool = False,
            prune_connectors: bool = False
        ) -> None:
            rs = pd.read_csv(summary_csv)
            st = pd.read_csv(stops_csv)
            seqs = {rid: df.sort_values(["day_type","order"])["stop_id"].astype(str).tolist()
                    for rid, df in st.groupby("branch_id")}
            from collections import defaultdict as DD
            node2r: Dict[str, Set[str]] = DD(set)
            for rid, seq in seqs.items():
                for n in seq: node2r[n].add(rid)
            branch_nodes = {rid: set(seq) for rid, seq in seqs.items()}
            branch_edges = {rid: set(zip(seq[:-1], seq[1:])) for rid, seq in seqs.items()}

            def is_internal(rid: str) -> Tuple[bool, Optional[str]]:
                nodes = branch_nodes[rid]; edges = branch_edges[rid]
                for other in branch_nodes:
                    if other == rid: continue
                    if nodes.issubset(branch_nodes[other]) and edges.issubset(branch_edges[other]):
                        return True, other
                return False, None

            touches_ids, touches_nodes, post_type, redundant_of = [], [], [], []
            for _, row in rs.iterrows():
                rid = row["branch_id"]; rtype = row["type"]
                seq = seqs.get(rid, [])
                touched_b = set(); touched_n = set()
                for n in seq:
                    for ob in node2r.get(str(n), set()):
                        if ob != rid:
                            touched_b.add(ob); touched_n.add(n)
                start_on_other = len(node2r.get(str(seq[0]), set()) - {rid}) > 0 if seq else False
                end_on_other   = len(node2r.get(str(seq[-1]), set()) - {rid}) > 0 if seq else False

                ttype = ""; red = ""
                if rtype == "adjacent":
                    if start_on_other and end_on_other: ttype = "connector_between_branches"
                    elif touched_b: ttype = "adjacent_to_branch"
                    else: ttype = "adjacent"
                elif rtype == "loop":
                    ttype = "loop"
                else:
                    iss, parent = is_internal(rid)
                    if iss: ttype = "internal_subsegment"; red = parent
                    elif start_on_other and end_on_other: ttype = "connector_between_branches"
                    else: ttype = rtype

                touches_ids.append(",".join(sorted(touched_b)))
                touches_nodes.append(",".join(sorted(map(str, touched_n))))
                post_type.append(ttype); redundant_of.append(red)

            rs["touches_branch_ids"] = touches_ids
            rs["touches_nodes"] = touches_nodes
            rs["post_type"] = post_type
            rs["redundant_of"] = redundant_of
            rs.to_csv(summary_csv.replace("_summary.csv", "_summary_post.csv"), index=False)

            if prune_internal_subsegments or prune_connectors:
                drop = set()
                if prune_internal_subsegments: drop |= set(rs.loc[rs["post_type"] == "internal_subsegment", "branch_id"])
                if prune_connectors: drop |= set(rs.loc[rs["post_type"] == "connector_between_branches", "branch_id"])
                keep = ~rs["branch_id"].isin(list(drop))
                rs_clean = rs.loc[keep].copy()
                rs_clean.to_csv(summary_csv.replace("_summary.csv", "_summary_clean.csv"), index=False)

                df = pd.read_csv(stops_csv)
                df = df.loc[df["branch_id"].isin(rs_clean["branch_id"])]
                df.to_csv(stops_csv.replace("_stops.csv", "_stops_clean.csv"), index=False)

        # -------------------- CLI --------------------

        def main():
            ap = argparse.ArgumentParser(description="Inventariar ramales MULTIMODO por DÍA-TIPO (con soporte de bucles).")
            ap.add_argument("--tiempos", required=True, help="CSV tiempos_reco")
            ap.add_argument("--paradas", required=True, help="CSV Paradas")
            ap.add_argument("--prefix", default="ramales", help="Prefijo de salida")
            ap.add_argument("--agg", default="mean", choices=["mean","median","min","max"])

            ap.add_argument("--day_types", default="", help="Lista de day_type separados por coma (vacío=auto)")
            ap.add_argument("--allowed_modes", default="", help="Lista de modos a considerar (vacío=todos)")
            ap.add_argument("--respect_stop_modes", action="store_true", help="Restringir por modos de la parada (ON)")
            ap.add_argument("--no_respect_stop_modes", dest="respect_stop_modes", action="store_false", help="Ignorar modos de parada")
            ap.set_defaults(respect_stop_modes=True)
            ap.add_argument("--mode_behavior", default="intersection_only", choices=["intersection_only","split_on_mode_change"])
            ap.add_argument("--strict_directed", action="store_true", help="Exigir arco dirigido en cada paso")

            ap.add_argument("--max_minutes_per_branch", type=float, default=None)
            ap.add_argument("--max_branches", type=int, default=10_000_000)
            ap.add_argument("--max_nodes_per_branch", type=int, default=100_000)
            ap.add_argument("--flush_every", type=int, default=5000)
            ap.add_argument("--resume", action="store_true")
            ap.add_argument("--mem_threshold_mb", type=float, default=None)
            ap.add_argument("--checkpoint_block", type=int, default=100_000)
            ap.add_argument("--max_branches_per_component", type=int, default=None)
            ap.add_argument("--require_new_coverage", action="store_true")
            ap.add_argument("--min_stops", type=int, default=3)
            ap.add_argument("--min_minutes", type=float, default=None)
            ap.add_argument("--only_cabecera_to_cabecera", action="store_true")
            ap.add_argument("--disallow_adjacent_rejoin_spine", action="store_true")

            ap.add_argument("--allow_nodes", default="")
            ap.add_argument("--allow_arcs", default="")
            ap.add_argument("--deny_nodes", default="")
            ap.add_argument("--deny_arcs", default="")
            ap.add_argument("--allow_modes_rule", default="", help="Lista blanca de modos para el ramal (filtro final)")
            ap.add_argument("--deny_modes_rule", default="", help="Lista negra de modos para el ramal (filtro final)")

            ap.add_argument("--dominance_online", action="store_true")
            ap.add_argument("--dominance_min_stops", type=int, default=3)

            ap.add_argument("--capture_loops", action="store_true", help="Capturar bucles completos como ramales únicos")
            ap.add_argument("--no_capture_loops", dest="capture_loops", action="store_false", help="No capturar bucles")
            ap.set_defaults(capture_loops=True)
            ap.add_argument("--min_loop_length", type=int, default=3, help="Nº mínimo de nodos (sin contar el cierre) para bucle")
            ap.add_argument("--max_loop_length", type=int, default=20, help="Nº máximo de nodos (sin contar el cierre) para bucle")

            ap.add_argument("--post", action="store_true")
            ap.add_argument("--prune_internal", action="store_true")
            ap.add_argument("--prune_connectors", action="store_true")

            args = ap.parse_args()

            tiempos_all, paradas, issues = load_inputs_all(args.tiempos, args.paradas, agg=args.agg)
            if issues:
                print("Avisos:", file=sys.stderr)
                for it in issues: print("-", it, file=sys.stderr)

            def parse_list(s: str) -> List[str]:
                return [x.strip() for x in s.split(",") if x.strip()] if s else []

            day_types = parse_list(args.day_types)
            allowed_modes = parse_list(args.allowed_modes)
            allow_nodes = parse_list(args.allow_nodes)
            allow_arcs   = parse_list(args.allow_arcs)
            deny_nodes  = parse_list(args.deny_nodes)
            deny_arcs    = parse_list(args.deny_arcs)
            allow_modes_rule = parse_list(args.allow_modes_rule)
            deny_modes_rule  = parse_list(args.deny_modes_rule)

            sum_csv, sto_csv, arc_csv, paradas_ann = inventariar_ramales_multimodo(
                tiempos_all, paradas,
                prefix_out=args.prefix,
                day_types=day_types or None,
                allowed_modes=allowed_modes or None,
                respect_stop_modes=args.respect_stop_modes,
                mode_behavior=args.mode_behavior,
                strict_directed=args.strict_directed,
                max_minutes_per_branch=args.max_minutes_per_branch,
                max_branches_global=args.max_branches,
                max_nodes_per_branch=args.max_nodes_per_branch,
                flush_every=args.flush_every,
                resume=args.resume,
                mem_threshold_mb=args.mem_threshold_mb,
                checkpoint_block=args.checkpoint_block,
                max_branches_per_component=args.max_branches_per_component,
                require_new_coverage=args.require_new_coverage,
                min_stops=args.min_stops,
                min_minutes=args.min_minutes,
                only_cabecera_to_cabecera=args.only_cabecera_to_cabecera,
                disallow_adjacent_rejoin_spine=args.disallow_adjacent_rejoin_spine,
                allow_nodes=allow_nodes,
                allow_arcs=allow_arcs,
                deny_nodes=deny_nodes,
                deny_arcs=deny_arcs,
                allow_modes_rule=allow_modes_rule,
                deny_modes_rule=deny_modes_rule,
                dominance_online=args.dominance_online,
                dominance_min_stops=args.dominance_min_stops,
                capture_loops=args.capture_loops,
                min_loop_length=args.min_loop_length,
                max_loop_length=args.max_loop_length
            )

            paradas_ann.to_csv(f"{args.prefix}_Paradas_anotadas.csv", index=False)

            print("Archivos generados:")
            print(" -", sum_csv)
            print(" -", sto_csv)
            print(" -", arc_csv)
            print(" -", f"{args.prefix}_Paradas_anotadas.csv")
            print(" -", f"{args.prefix}_state.json (estado por componente/día)")
            print(" -", f"{args.prefix}.log (bitácora JSONL)")

            if args.post:
                post_analisis_en_csv(
                    sum_csv, sto_csv,
                    prune_internal_subsegments=args.prune_internal,
                    prune_connectors=args.prune_connectors
                )
                print("Post-análisis:", sum_csv.replace("_summary.csv","_summary_post.csv"))
                if args.prune_internal or args.prune_connectors:
                    print("Limpios:", sum_csv.replace("_summary.csv","_summary_clean.csv"),
                          sto_csv.replace("_stops.csv","_stops_clean.csv"))

        if __name__ == "__main__":
            main()

from collections import defaultdict
from typing import List, Dict, Tuple, Set


class Network:
    def __init__(self, stops: List[Dict], tiempos: List[Dict]):
        # store stops by id
        self.stops = {s['stop_id']: s for s in stops}

        # build undirected adjacency mapping: node -> set(neighbors)
        self.adj = defaultdict(set)  # type: Dict[str, Set[str]]
        # keep edge metadata: edge_id -> list of tiempo dicts (to aggregate modes/times)
        self.edge_meta = defaultdict(list)

        for t in tiempos:
            a = str(t.get('from_id'))
            b = str(t.get('to_id'))
            if a == '' or b == '':
                continue
            # undirected edge
            self.adj[a].add(b)
            self.adj[b].add(a)
            # canonical edge id
            eid = tuple(sorted((a, b)))
            self.edge_meta[eid].append(t)

        # also capture directed edges present in tiempos for directional checks
        self.directed_edges = set()
        for t in tiempos:
            a = str(t.get('from_id'))
            b = str(t.get('to_id'))
            if a == '' or b == '':
                continue
            self.directed_edges.add((a, b))

    def _edge_id(self, a: str, b: str) -> Tuple[str, str]:
        return tuple(sorted((a, b)))

    def find_troncales(self, generate_terminal_paths: bool = False) -> List[Dict]:
        """
        Build maximal troncales as segments whose internal nodes have degree == 2 and
        endpoints have degree != 2. Mark edges as visited so each edge appears in exactly
        one troncal (except when you want to preserve direction; here we consider undirected segments).
        Also detect pure cycles composed only of degree==2 nodes.
        Returns list of {'id': str, 'stops': [stop_ids], 'modes': set(...) }
        """
        # degree per node in the undirected network
        grado = {n: len(neighs) for n, neighs in self.adj.items()}

        def _uniq_paths(paths: List[List[str]]) -> List[List[str]]:
            uniq = []
            seen = set()
            for p in paths:
                t = tuple(p)
                rt = tuple(reversed(t))
                key = t if t <= rt else rt
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(list(t))
            return uniq

        result_paths: List[Tuple[List[str], Set[str]]] = []  # (stops, modes)

        if generate_terminal_paths:
            # Build troncales as longest simple paths between terminal nodes grouped per mode
            # collect all modes present
            modes_all = set()
            for meta_list in self.edge_meta.values():
                for meta in meta_list:
                    if meta.get('mode'):
                        modes_all.add(str(meta.get('mode')))

            # helper to build subgraph adjacency for a given mode
            def subgraph_for_mode(mode: str) -> Dict[str, Set[str]]:
                adjm = defaultdict(set)
                for (a, b), metas in self.edge_meta.items():
                    for meta in metas:
                        if str(meta.get('mode')) == mode:
                            adjm[a].add(b)
                            adjm[b].add(a)
                            break
                return adjm

            # connected components in subgraph
            def components(adjm: Dict[str, Set[str]]) -> List[Set[str]]:
                seen = set()
                comps = []
                for n in adjm.keys():
                    if n in seen:
                        continue
                    stack = [n]
                    comp = set()
                    while stack:
                        x = stack.pop()
                        if x in comp:
                            continue
                        comp.add(x)
                        for y in adjm.get(x, []):
                            if y not in comp:
                                stack.append(y)
                    seen |= comp
                    comps.append(comp)
                return comps

            # find longest simple paths between terminal nodes in a component
            def longest_terminal_paths_in_comp(adjm: Dict[str, Set[str]], comp: Set[str]) -> List[List[str]]:
                # terminals are nodes of degree 1 within this adjm
                terminals = [n for n in comp if len(adjm.get(n, [])) == 1]
                paths = []
                # if no terminals, try to find a long cycle (pick any node and DFS)
                if not terminals:
                    # fallback: try to extract a simple cycle/path by DFS
                    start = next(iter(comp))
                    stack = [(start, [start], {start})]
                    best = []
                    while stack:
                        node, path, vis = stack.pop()
                        if len(path) > len(best):
                            best = list(path)
                        for nb in adjm.get(node, []):
                            if nb in vis:
                                continue
                            npath = path + [nb]
                            nvis = set(vis)
                            nvis.add(nb)
                            stack.append((nb, npath, nvis))
                    if best:
                        paths.append(best)
                    return paths

                # For each terminal, perform DFS to reach other terminals and keep the longest
                for s in terminals:
                    stack = [(s, [s], {s})]
                    while stack:
                        node, path, vis = stack.pop()
                        # if reached another terminal (and not same as start)
                        if node in terminals and node != s:
                            paths.append(list(path))
                            # do not early-break; we want possibly longer ones
                        for nb in adjm.get(node, []):
                            if nb in vis:
                                continue
                            npath = path + [nb]
                            nvis = set(vis)
                            nvis.add(nb)
                            stack.append((nb, npath, nvis))
                # keep only longest path between each unordered terminal pair
                best_map = {}
                for p in paths:
                    a, b = p[0], p[-1]
                    key = tuple(sorted((a, b)))
                    if key not in best_map or len(p) > len(best_map[key]):
                        best_map[key] = p
                return list(best_map.values())

            # gather paths per mode
            for mode in modes_all:
                adjm = subgraph_for_mode(mode)
                comps = components(adjm)
                for comp in comps:
                    lp = longest_terminal_paths_in_comp(adjm, comp)
                    for p in lp:
                        result_paths.append((p, {mode}))

            # deduplicate undirected duplicates
            uniq_paths = _uniq_paths([p for p, m in result_paths])
            # re-map modes: if a uniq path appears for multiple modes, union modes
            path_mode_map = {}
            for p, m in result_paths:
                t = tuple(p)
                # canonical undirected key
                rt = tuple(reversed(t))
                key = t if t <= rt else rt
                path_mode_map.setdefault(key, set()).update(m)

            # Build final paths and expand to directional variants when possible
            final_paths_tmp = []
            for p in uniq_paths:
                t = tuple(p)
                rt = tuple(reversed(t))
                key = t if t <= rt else rt
                modes = path_mode_map.get(key, set())
                final_paths_tmp.append({'stops': p, 'modes': modes})

            # If directed edge data exists for the whole path in a single direction,
            # generate that directional troncal as separate entry. Do the same for the
            # reverse direction. If neither direction is fully directed, keep the
            # undirected representative.
            final_paths = []
            for item in final_paths_tmp:
                path = item['stops']
                modes = item.get('modes', set()) or set()
                # check forward
                ok_fwd = all((a, b) in self.directed_edges for a, b in zip(path, path[1:]))
                # check backward
                rev = list(reversed(path))
                ok_bwd = all((a, b) in self.directed_edges for a, b in zip(rev, rev[1:]))
                if ok_fwd:
                    final_paths.append({'stops': path, 'modes': modes})
                if ok_bwd:
                    final_paths.append({'stops': rev, 'modes': modes})
                if not ok_fwd and not ok_bwd:
                    final_paths.append({'stops': path, 'modes': modes})

        else:
            # original behavior: find maximal segments where internal nodes have degree == 2
            endpoints = {n for n, g in grado.items() if g == 1}
            visited_edges: Set[Tuple[str, str]] = set()
            troncales: List[Dict] = []

            def mark_edge(u: str, v: str):
                visited_edges.add(self._edge_id(u, v))

            # traverse from each endpoint along each neighbor until another endpoint or dead end
            for ep in endpoints:
                for neigh in list(self.adj[ep]):
                    eid = self._edge_id(ep, neigh)
                    if eid in visited_edges:
                        continue
                    path = [ep, neigh]
                    mark_edge(ep, neigh)
                    prev, cur = ep, neigh
                    # continue while current node is degree 2 (non-branching)
                    while grado.get(cur, 0) == 2:
                        # pick the neighbor different from prev
                        neighs = list(self.adj[cur])
                        nxt = neighs[0] if neighs[0] != prev else neighs[1]
                        eid = self._edge_id(cur, nxt)
                        if eid in visited_edges:
                            break
                        path.append(nxt)
                        mark_edge(cur, nxt)
                        prev, cur = cur, nxt
                    # store path if it's at least two stops
                    if len(path) >= 2:
                        troncales.append({'stops': path})

            # detect remaining edges (cycles composed of degree==2 nodes or leftover segments)
            for eid in list(self.edge_meta.keys()):
                if eid in visited_edges:
                    continue
                a, b = eid
                if eid in visited_edges:
                    continue
                path = [a, b]
                mark_edge(a, b)
                prev, cur = a, b
                while True:
                    neighs = list(self.adj[cur])
                    # choose next that is not prev
                    nxt = None
                    for n in neighs:
                        if n != prev:
                            nxt = n
                            break
                    if nxt is None:
                        break
                    eid2 = self._edge_id(cur, nxt)
                    if eid2 in visited_edges:
                        break
                    path.append(nxt)
                    mark_edge(cur, nxt)
                    prev, cur = cur, nxt
                    # if we closed the cycle
                    if cur == path[0]:
                        break
                if len(path) >= 2:
                    troncales.append({'stops': path})

            uniq = _uniq_paths([p['stops'] for p in troncales])

            # expand each undirected segment into directional troncales when directed edges exist
            directional = []
            for path in uniq:
                ok_fwd = True
                for a, b in zip(path, path[1:]):
                    if (a, b) not in self.directed_edges:
                        ok_fwd = False
                        break
                if ok_fwd:
                    directional.append(list(path))

                rev = list(reversed(path))
                ok_bwd = True
                for a, b in zip(rev, rev[1:]):
                    if (a, b) not in self.directed_edges:
                        ok_bwd = False
                        break
                if ok_bwd:
                    directional.append(rev)

            final_paths = []
            for p in (directional if directional else uniq):
                # aggregate modes for the undirected path
                modes = set()
                for a, b in zip(p, p[1:]):
                    eid = self._edge_id(a, b)
                    for meta in self.edge_meta.get(eid, []):
                        m = meta.get('mode')
                        if m:
                            modes.add(str(m))
                final_paths.append({'stops': p, 'modes': modes})

        # ----- MERGE ADJACENT TRONCALES WHERE POSSIBLE -----
        # can_merge: end of a == start of b and modes overlap (or one has no mode)
        def can_merge(a: Dict, b: Dict) -> bool:
            if a['stops'][-1] != b['stops'][0]:
                return False
            if not a.get('modes') or not b.get('modes'):
                return True
            return bool(set(a.get('modes', set())) & set(b.get('modes', set())))

        # start from final_paths list of dicts
        result = []
        # normalize input to dicts with id/modes
        working = [{'id': None, 'stops': p['stops'], 'modes': set(p.get('modes') or [])} for p in final_paths]

        merged_any = True
        merge_counter = 0
        while merged_any:
            merged_any = False
            new_working: List[Dict] = []
            used = [False] * len(working)
            for i, a in enumerate(working):
                if used[i]:
                    continue
                cur = a
                changed = True
                while changed:
                    changed = False
                    for j, b in enumerate(working):
                        if i == j or used[j]:
                            continue
                        if can_merge(cur, b):
                            # avoid internal overlap
                            if set(cur['stops'][:-1]) & set(b['stops'][1:]):
                                continue
                            cur = {
                                'id': f'merged_{merge_counter}',
                                'stops': cur['stops'] + b['stops'][1:],
                                'modes': set(cur.get('modes', set())) | set(b.get('modes', set()))
                            }
                            merge_counter += 1
                            used[j] = True
                            merged_any = True
                            changed = True
                            break
                new_working.append(cur)
                used[i] = True
            working = new_working

        # attach stable ids
        for i, w in enumerate(working, start=1):
            result.append({'id': f'troncal_{i}', 'stops': w['stops'], 'modes': set(w.get('modes') or [])})

        return result

    def export_red(self, troncales: List[Dict], out_path: str):
        import csv
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['troncal_id', 'stop_sequence', 'stops', 'modes', 'day_types'])
            writer.writeheader()
            for t in troncales:
                # normalize modes/day_types to semicolon-separated strings
                modes_v = t.get('modes', '')
                if isinstance(modes_v, (set, list, tuple)):
                    modes_str = ';'.join(sorted(str(x) for x in modes_v if x))
                else:
                    modes_str = str(modes_v) if modes_v is not None else ''

                day_v = t.get('day_types', '')
                if isinstance(day_v, (set, list, tuple)):
                    day_str = ';'.join(sorted(str(x) for x in day_v if x))
                else:
                    day_str = str(day_v) if day_v is not None else ''

                writer.writerow({
                    'troncal_id': t['id'],
                    'stop_sequence': '->'.join(t['stops']),
                    'stops': '|'.join(t['stops']),
                    'modes': modes_str,
                    'day_types': day_str
                })

        # also export intersections between troncales for debugging/transfer mapping
        try:
            inter_path = out_path.replace('red.csv', 'troncal_intersections.csv')
            # build intersections
            pairs = []
            for i, a in enumerate(troncales):
                for j, b in enumerate(troncales):
                    if j <= i:
                        continue
                    sa = set(a['stops'])
                    sb = set(b['stops'])
                    common = sorted(list(sa & sb))
                    if common:
                        pairs.append({'troncal_a': a['id'], 'troncal_b': b['id'], 'common_stops': '|'.join(common)})
            if pairs:
                with open(inter_path, 'w', newline='', encoding='utf-8') as f2:
                    w = csv.DictWriter(f2, fieldnames=['troncal_a', 'troncal_b', 'common_stops'])
                    w.writeheader()
                    for p in pairs:
                        w.writerow(p)
        except Exception:
            # don't fail export if intersection writing fails
            pass

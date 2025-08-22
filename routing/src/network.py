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

    def find_troncales(self) -> List[Dict]:
        """
        Build maximal troncales as segments whose internal nodes have degree == 2 and
        endpoints have degree != 2. Mark edges as visited so each edge appears in exactly
        one troncal (except when you want to preserve direction; here we consider undirected segments).
        Also detect pure cycles composed only of degree==2 nodes.
        Returns list of {'id': str, 'stops': [stop_ids], 'modes': set(...) }
        """
        grado = {n: len(neighs) for n, neighs in self.adj.items()}

        # endpoints: nodes with degree == 1 (terminal stops)
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
            # start a cycle/segment from eid[0] -> eid[1]
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

        # remove duplicates (reverse sequences)
        uniq = []
        seen = set()
        for p in troncales:
            t = tuple(p['stops'])
            rt = tuple(reversed(t))
            key = t if t <= rt else rt
            if key in seen:
                continue
            seen.add(key)
            uniq.append({'stops': list(t)})

        # expand each undirected segment into directional troncales when directed edges exist
        directional = []
        for seg in uniq:
            path = seg['stops']
            # check forward direction
            ok_fwd = True
            for a, b in zip(path, path[1:]):
                if (a, b) not in self.directed_edges:
                    ok_fwd = False
                    break
            if ok_fwd:
                directional.append(list(path))

            # check backward direction
            rev = list(reversed(path))
            ok_bwd = True
            for a, b in zip(rev, rev[1:]):
                if (a, b) not in self.directed_edges:
                    ok_bwd = False
                    break
            if ok_bwd:
                directional.append(rev)

        # if no directional expansion possible, keep undirected segments as-is
        final_segs = directional if directional else [s['stops'] for s in uniq]

        # attach an id and aggregate modes per troncal
        result = []
        for i, stops in enumerate(final_segs, start=1):
            modes = set()
            for a, b in zip(stops, stops[1:]):
                eid = self._edge_id(a, b)
                for meta in self.edge_meta.get(eid, []):
                    m = meta.get('mode')
                    if m:
                        modes.add(str(m))
            result.append({'id': f'troncal_{i}', 'stops': stops, 'modes': modes})

        return result

    def export_red(self, troncales: List[Dict], out_path: str):
        import csv
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['troncal_id', 'stop_sequence', 'stops', 'modes'])
            writer.writeheader()
            for t in troncales:
                modes = t.get('modes') or set()
                writer.writerow({
                    'troncal_id': t['id'],
                    'stop_sequence': '->'.join(t['stops']),
                    'stops': '|'.join(t['stops']),
                    'modes': ';'.join(sorted(modes))
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

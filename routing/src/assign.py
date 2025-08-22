import csv
from math import radians, cos, sin, asin, sqrt
from typing import List, Dict, Optional, Tuple
import heapq


def haversine(lat1, lon1, lat2, lon2):
    # return distance in meters
    # convert degrees to radians (preserve order lat, lon)
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    km = 6371 * c
    return km * 1000


def find_nearest_stop(lat, lon, stops: Dict[str, Dict], max_m=1000) -> Optional[Tuple[str, float]]:
    best = (None, float('inf'))
    for sid, s in stops.items():
        if s.get('lat') is None or s.get('lon') is None:
            continue
        d = haversine(lat, lon, s['lat'], s['lon'])
        if d < best[1]:
            best = (sid, d)
    if best[1] <= max_m:
        return best
    return None


def assign_users(users: List[Dict], network, troncales: List[Dict], out_path: str, logger=None,
                 transfer_penalty: float = 1.0, walk_km_factor: float = 0.001, mode_penalty: float = 0.5):
    # prepare mapping from stop to troncales
    stop_to_tron = {}
    for t in troncales:
        for s in t['stops']:
            stop_to_tron.setdefault(s, []).append(t['id'])

    # build troncal graph and intersections (troncal -> set(neighbor troncal))
    troncal_neighbors = {t['id']: set() for t in troncales}
    troncal_common = {}  # frozenset({a,b}) -> list of common stops
    id_to_tron = {t['id']: t for t in troncales}
    # helper: check if directed edge (a->b) has a tiempo record matching user's day_type and depart_time window
    def edge_allowed(a: str, b: str, depart_time, day_type: str) -> bool:
        eid = network._edge_id(a, b)
        metas = network.edge_meta.get(eid, [])
        # Collect candidate metas that match direction
        candidates = []
        for m in metas:
            if str(m.get('from_id')) != str(a) or str(m.get('to_id')) != str(b):
                continue
            candidates.append(m)

        if not candidates:
            return False

        # Prefer exact day_type matches, but allow 'habitual' as fallback
        preferred = [m for m in candidates if (m.get('day_type') or 'habitual') == (day_type or 'habitual')]
        pool = preferred if preferred else candidates

        # If no depart_time provided, accept if any meta exists for direction/day_type
        if depart_time is None:
            return True

        # Check each meta; allow window that may cross midnight
        for m in pool:
            st = m.get('start_time')
            et = m.get('end_time')
            # Missing window means always available
            if st is None or et is None:
                return True
            try:
                if st <= et:
                    if st <= depart_time <= et:
                        return True
                else:
                    # window crosses midnight (e.g., 23:00 - 03:00)
                    if depart_time >= st or depart_time <= et:
                        return True
            except Exception:
                # conservative allow on errors
                return True
        return False

    def troncal_available_for_user(troncal_id: str, depart_time, day_type: str) -> bool:
        t = id_to_tron.get(troncal_id)
        if not t:
            return False
        stops = t['stops']
        for a, b in zip(stops, stops[1:]):
            if not edge_allowed(a, b, depart_time, day_type):
                return False
        return True
    for i, a in enumerate(troncales):
        for j, b in enumerate(troncales):
            if j <= i:
                continue
            sa = set(a['stops'])
            sb = set(b['stops'])
            common = sorted(list(sa & sb))
            if common:
                troncal_neighbors[a['id']].add(b['id'])
                troncal_neighbors[b['id']].add(a['id'])
                troncal_common[frozenset((a['id'], b['id']))] = common

    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        fieldnames = [
            'user_id',
            'origin_stop_id', 'origin_stop_name', 'origin_dist_m',
            'dest_stop_id', 'dest_stop_name', 'dest_dist_m',
            'root', 'transfers', 'transfers_stops', 'no_root', 'notes'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for u in users:
            orig = find_nearest_stop(u['orig_lat'], u['orig_lon'], network.stops)
            dest = find_nearest_stop(u['dest_lat'], u['dest_lon'], network.stops)
            if orig is None or dest is None:
                notes = 'origin_or_dest_no_stop_within_1000m'
                if logger:
                    logger(f"User {u['user_id']}: {notes}")
                writer.writerow({
                    'user_id': u['user_id'],
                    'origin_stop_id': orig[0] if orig else '',
                    'origin_stop_name': network.stops.get(orig[0], {}).get('name') if orig else '',
                    'origin_dist_m': round(orig[1], 1) if orig else '',
                    'dest_stop_id': dest[0] if dest else '',
                    'dest_stop_name': network.stops.get(dest[0], {}).get('name') if dest else '',
                    'dest_dist_m': round(dest[1], 1) if dest else '',
                    'root': '', 'transfers': '', 'transfers_stops': '', 'no_root': '', 'notes': notes
                })
                continue

            origin_stop = orig[0]
            dest_stop = dest[0]

            # search for troncales that include origin and destination in order and are temporally available
            roots = []
            for t in troncales:
                stops = t['stops']
                if origin_stop in stops and dest_stop in stops and stops.index(origin_stop) <= stops.index(dest_stop):
                    if troncal_available_for_user(t['id'], u.get('depart_time'), u.get('day_type')):
                        roots.append(t['id'])

            if roots:
                # if any troncal contains both stops, accept it as single-troncal route
                writer.writerow({
                    'user_id': u['user_id'],
                    'origin_stop_id': origin_stop,
                    'origin_stop_name': network.stops.get(origin_stop, {}).get('name',''),
                    'origin_dist_m': round(orig[1], 1),
                    'dest_stop_id': dest_stop,
                    'dest_stop_name': network.stops.get(dest_stop, {}).get('name',''),
                    'dest_dist_m': round(dest[1], 1),
                    'root': str(roots), 'transfers': 0, 'transfers_stops': '', 'no_root': '', 'notes': ''
                })
                if logger:
                    logger(f"User {u['user_id']}: assigned roots {roots}")
            else:
                # try to find a sequence of troncales connecting origin_tr -> dest_tr via shared stops
                origin_tr = [tid for tid in stop_to_tron.get(origin_stop, []) if troncal_available_for_user(tid, u.get('depart_time'), u.get('day_type'))]
                dest_tr = set([tid for tid in stop_to_tron.get(dest_stop, []) if troncal_available_for_user(tid, u.get('depart_time'), u.get('day_type'))])

                if not origin_tr or not dest_tr:
                    # no troncales cover origin or dest
                    possible = []
                    writer.writerow({
                        'user_id': u['user_id'],
                        'origin_stop_id': origin_stop,
                        'origin_stop_name': network.stops.get(origin_stop, {}).get('name',''),
                        'origin_dist_m': round(orig[1], 1),
                        'dest_stop_id': dest_stop,
                        'dest_stop_name': network.stops.get(dest_stop, {}).get('name',''),
                        'dest_dist_m': round(dest[1], 1),
                        'root': '', 'transfers': '', 'transfers_stops': '', 'no_root': str(possible), 'notes': 'no_troncal_for_origin_or_dest'
                    })
                    if logger:
                        logger(f"User {u['user_id']}: no troncales at origin or dest")
                    continue

                # Use Dijkstra on troncal graph to find lowest-cost sequence
                # Costs consider: base transfer penalty, walking distance between transfer stops (meters -> km scaled),
                # and a small penalty for troncales that don't match user's mode_prefs.

                mode_prefs = u.get('mode_prefs') or []

                # helper: compute edge weight between two adjacent troncales
                def edge_weight(a_id: str, b_id: str) -> float:
                    commons = troncal_common.get(frozenset((a_id, b_id)), [])
                    if commons:
                        walk_m = 0.0
                    else:
                        # if no common stops (shouldn't happen because we built neighbors from commons),
                        # estimate minimal distance between stops lists
                        sa = id_to_tron.get(a_id, {}).get('stops', [])
                        sb = id_to_tron.get(b_id, {}).get('stops', [])
                        walk_m = float('inf')
                        for x in sa:
                            for y in sb:
                                sx = network.stops.get(x, {})
                                sy = network.stops.get(y, {})
                                if not sx or not sy or sx.get('lat') is None or sy.get('lat') is None:
                                    continue
                                walk_m = min(walk_m, haversine(sx['lat'], sx['lon'], sy['lat'], sy['lon']))
                        if walk_m == float('inf'):
                            walk_m = 1000.0
                    base_transfer = transfer_penalty
                    return base_transfer + (walk_m * walk_km_factor)

                # node penalty for mode preference
                def node_penalty(tid: str) -> float:
                    modes = id_to_tron.get(tid, {}).get('modes', set()) or set()
                    # if any preferred mode present, no penalty
                    try:
                        if any(m in modes for m in mode_prefs):
                            return 0.0
                    except Exception:
                        pass
                    return mode_penalty

                # Dijkstra: initial costs from origin_tr depend on walking from user origin to origin_stop (meters -> km)
                origin_walk_km = (orig[1] / 1000.0) if orig else 0.0
                heap = []  # (cost, troncal_id, predecessor)
                best_cost = {}
                prev = {}
                for o in origin_tr:
                    cost = origin_walk_km + node_penalty(o)
                    best_cost[o] = cost
                    heapq.heappush(heap, (cost, o))
                    prev[o] = None

                found_path = None
                target_troncal = None
                dest_candidates = set(dest_tr)
                while heap:
                    cost, cur = heapq.heappop(heap)
                    if cost != best_cost.get(cur, None):
                        continue
                    if cur in dest_candidates:
                        # build path
                        path = [cur]
                        p = prev.get(cur)
                        while p is not None:
                            path.append(p)
                            p = prev.get(p)
                        path.reverse()
                        found_path = path
                        target_troncal = cur
                        break
                    for nb in troncal_neighbors.get(cur, []):
                        w = edge_weight(cur, nb) + node_penalty(nb)
                        nc = cost + w
                        if nb not in best_cost or nc < best_cost[nb]:
                            best_cost[nb] = nc
                            prev[nb] = cur
                            heapq.heappush(heap, (nc, nb))

                if found_path:
                    transfers = max(0, len(found_path) - 1)
                    max_t = u.get('max_transfers') if u.get('max_transfers') is not None else 3
                    notes = ''
                    if isinstance(max_t, int) and transfers > max_t:
                        notes = f'exceeds_max_transfers({transfers}>{max_t})'

                    # compute transfer stops (choose first common stop between consecutive troncales)
                    # compute transfer stops: choose best common stop minimizing walking distance
                    transfer_stops = []
                    prev_point = origin_stop
                    for a, b in zip(found_path, found_path[1:]):
                        commons = troncal_common.get(frozenset((a, b)), [])
                        best = ''
                        best_d = float('inf')
                        stops_a = id_to_tron.get(a, {}).get('stops', [])
                        # try to prefer commons that are forward along troncal a from prev_point
                        prefer_forward = []
                        for s in commons:
                            try:
                                if prev_point in stops_a and stops_a.index(s) >= stops_a.index(prev_point):
                                    prefer_forward.append(s)
                            except Exception:
                                # ignore index errors
                                pass
                        candidates = prefer_forward if prefer_forward else commons
                        for s in candidates:
                            sa = network.stops.get(prev_point, {})
                            sb = network.stops.get(s, {})
                            if not sa or not sb or sa.get('lat') is None or sb.get('lat') is None:
                                d = 0.0
                            else:
                                d = haversine(sa['lat'], sa['lon'], sb['lat'], sb['lon'])
                            if d < best_d:
                                best_d = d
                                best = s
                        transfer_stops.append(best)
                        if best:
                            prev_point = best

                    writer.writerow({
                        'user_id': u['user_id'],
                        'origin_stop_id': origin_stop,
                        'origin_stop_name': network.stops.get(origin_stop, {}).get('name',''),
                        'origin_dist_m': round(orig[1], 1),
                        'dest_stop_id': dest_stop,
                        'dest_stop_name': network.stops.get(dest_stop, {}).get('name',''),
                        'dest_dist_m': round(dest[1], 1),
                        'root': str(found_path),
                        'transfers': transfers,
                        'transfers_stops': '|'.join([t for t in transfer_stops if t]),
                        'no_root': '',
                        'notes': notes
                    })
                    if logger:
                        logger(f"User {u['user_id']}: assigned troncal path {found_path} transfers={transfers} notes={notes}")
                else:
                    # fallback: list possible pairs as before
                    possible = []
                    for o in origin_tr:
                        for d in dest_tr:
                            if o == d:
                                continue
                            possible.append((o, d))
                    writer.writerow({
                        'user_id': u['user_id'],
                        'origin_stop_id': origin_stop,
                        'origin_stop_name': network.stops.get(origin_stop, {}).get('name',''),
                        'origin_dist_m': round(orig[1], 1),
                        'dest_stop_id': dest_stop,
                        'dest_stop_name': network.stops.get(dest_stop, {}).get('name',''),
                        'dest_dist_m': round(dest[1], 1),
                        'root': '', 'transfers': '', 'transfers_stops': '', 'no_root': str(possible), 'notes': 'no_troncal_path_found'
                    })
                    if logger:
                        logger(f"User {u['user_id']}: no troncal path found; possible pairs {possible}")

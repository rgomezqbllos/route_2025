import csv
from math import radians, cos, sin, asin, sqrt
from typing import List, Dict, Optional, Tuple, Iterable
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


def find_nearest_stop(
    lat: float,
    lon: float,
    stops: Dict[str, Dict],
    *,
    allowed_ids: Optional[Iterable[str]] = None,
    max_m: float = 1500.0,
) -> Optional[Tuple[str, float]]:
    """
    Find nearest stop. If allowed_ids is provided, prefer among that set; if no
    stop within max_m among allowed, fall back to the nearest allowed regardless
    of distance. If allowed_ids is None/empty, use all stops and respect max_m.
    Returns (stop_id, distance_m) or None if no stop found at all.
    """
    best_any = (None, float('inf'))
    best_allowed = (None, float('inf'))
    allowed = set(allowed_ids) if allowed_ids else None
    for sid, s in stops.items():
        if s.get('lat') is None or s.get('lon') is None:
            continue
        d = haversine(lat, lon, s['lat'], s['lon'])
        if d < best_any[1]:
            best_any = (sid, d)
        if allowed is not None and sid in allowed and d < best_allowed[1]:
            best_allowed = (sid, d)
    if allowed is not None:
        if best_allowed[0] is not None:
            # prefer allowed; if beyond max, still return to ensure connectivity
            return best_allowed
        # fallback to any stop within max_m
        if best_any[1] <= max_m:
            return best_any
        return best_any if best_any[0] is not None else None
    # no allowed filter: respect max_m threshold
    if best_any[1] <= max_m:
        return best_any
    return None

def find_k_nearest_stops(
    lat: float,
    lon: float,
    stops: Dict[str, Dict],
    *,
    allowed_ids: Optional[Iterable[str]] = None,
    k: int = 3
) -> List[Tuple[str, float]]:
    """Return top-k nearest stops (id, m). If allowed_ids is provided, only consider those."""
    allowed = set(allowed_ids) if allowed_ids else None
    items = []
    for sid, s in stops.items():
        if s.get('lat') is None or s.get('lon') is None:
            continue
        if allowed is not None and sid not in allowed:
            continue
        d = haversine(lat, lon, s['lat'], s['lon'])
        items.append((sid, d))
    items.sort(key=lambda x: x[1])
    return items[:k]


def assign_users(
    users: List[Dict],
    network,
    troncales: List[Dict],
    out_path: str,
    logger=None,
    *,
    transfer_penalty: float = 1.0,
    walk_km_factor: float = 0.001,
    mode_penalty: float = 0.5,
    near_transfer_m: float = 250.0,
    k_nearest: int = 3,
    prefer_troncal_stops: bool = True,
    max_nearest_m: float = 2000.0,
):
    # prepare mapping from stop to troncales
    stop_to_tron = {}
    for t in troncales:
        for s in t['stops']:
            stop_to_tron.setdefault(s, []).append(t['id'])

    # build troncal graph and intersections (troncal -> set(neighbor troncal))
    troncal_neighbors = {t['id']: set() for t in troncales}
    troncal_common = {}  # frozenset({a,b}) -> list of common stops
    troncal_proximity = {}  # frozenset({a,b}) -> {'pair':(sa,sb), 'dist_m':float}
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
    # Precompute lat/lon for stops
    stop_coords = {sid: (s.get('lat'), s.get('lon')) for sid, s in network.stops.items()}

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
            else:
                # consider near transfers (within near_transfer_m)
                best_pair = None
                best_d = float('inf')
                for x in sa:
                    cx = stop_coords.get(x)
                    if not cx or cx[0] is None or cx[1] is None:
                        continue
                    for y in sb:
                        cy = stop_coords.get(y)
                        if not cy or cy[0] is None or cy[1] is None:
                            continue
                        d = haversine(cx[0], cx[1], cy[0], cy[1])
                        if d < best_d:
                            best_d = d
                            best_pair = (x, y)
                if best_pair and best_d <= near_transfer_m:
                    troncal_neighbors[a['id']].add(b['id'])
                    troncal_neighbors[b['id']].add(a['id'])
                    troncal_proximity[frozenset((a['id'], b['id']))] = {
                        'pair': best_pair,
                        'dist_m': best_d,
                    }

    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        fieldnames = [
            'user_id',
            'origin_stop_id', 'origin_stop_name', 'origin_dist_m',
            'dest_stop_id', 'dest_stop_name', 'dest_dist_m',
            'root', 'transfers', 'transfers_stops', 'no_root', 'notes'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        # build set of stops that belong to at least one troncal
        stops_in_troncales = set()
        for t in troncales:
            for s in t['stops']:
                stops_in_troncales.add(s)

        for u in users:
            # prefer nearest stop among troncal stops; fallback to any if missing
            allowed = stops_in_troncales if prefer_troncal_stops else None
            orig = find_nearest_stop(u['orig_lat'], u['orig_lon'], network.stops, allowed_ids=allowed, max_m=max_nearest_m)
            dest = find_nearest_stop(u['dest_lat'], u['dest_lon'], network.stops, allowed_ids=allowed, max_m=max_nearest_m)
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

            # search for troncales that include origin and destination in order and are temporally available (segment only)
            roots = []
            for t in troncales:
                stops = t['stops']
                if origin_stop in stops and dest_stop in stops and stops.index(origin_stop) <= stops.index(dest_stop):
                    # segment-level temporal check
                    if True:
                        try:
                            def add_minutes_to_time(tt, minutes):
                                if tt is None:
                                    return None
                                total = tt.hour * 60 + tt.minute + tt.second / 60.0 + float(minutes)
                                total = total % (24 * 60)
                                h = int(total // 60)
                                m = int(total % 60)
                                s = int(round((total - int(total)) * 60))
                                from datetime import time as _time
                                return _time(h, m, s)
                            dep = u.get('depart_time')
                            ok = True
                            prev_t = dep
                            for a, b in zip(stops[stops.index(origin_stop):stops.index(dest_stop)], stops[stops.index(origin_stop)+1:stops.index(dest_stop)+1]):
                                if not edge_allowed(a, b, prev_t, u.get('day_type')):
                                    ok = False
                                    break
                                # estimate traversal minutes from meta if available
                                eid = network._edge_id(a, b)
                                metas = network.edge_meta.get(eid, [])
                                minutes = None
                                # prefer directed & day_type match
                                preferred = [m for m in metas if str(m.get('from_id')) == str(a) and str(m.get('to_id')) == str(b) and (m.get('day_type') or 'habitual') == (u.get('day_type') or 'habitual')]
                                pool = preferred if preferred else [m for m in metas if str(m.get('from_id')) == str(a) and str(m.get('to_id')) == str(b)]
                                for m in pool:
                                    try:
                                        minutes = float(m.get('mean_minutes'))
                                        break
                                    except Exception:
                                        continue
                                if minutes is None:
                                    minutes = 2.0
                                prev_t = add_minutes_to_time(prev_t, minutes)
                            if ok:
                                roots.append(t['id'])
                        except Exception:
                            # if any error in temporal evaluation, accept conservatively
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
                # multi-candidate origins/destinations (top-k nearest troncal stops)
                origin_candidates = [origin_stop]
                dest_candidates_stops = [dest_stop]
                try:
                    origin_candidates = [sid for sid, _ in find_k_nearest_stops(u['orig_lat'], u['orig_lon'], network.stops, allowed_ids=stops_in_troncales, k=k_nearest)] or [origin_stop]
                except Exception:
                    pass
                try:
                    dest_candidates_stops = [sid for sid, _ in find_k_nearest_stops(u['dest_lat'], u['dest_lon'], network.stops, allowed_ids=stops_in_troncales, k=k_nearest)] or [dest_stop]
                except Exception:
                    pass

                origin_tr = []
                for sid in origin_candidates:
                    origin_tr.extend(stop_to_tron.get(sid, []))
                dest_tr = set()
                for sid in dest_candidates_stops:
                    for tid in stop_to_tron.get(sid, []):
                        dest_tr.add(tid)

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
                    key = frozenset((a_id, b_id))
                    commons = troncal_common.get(key, [])
                    if commons:
                        walk_m = 0.0
                    else:
                        prox = troncal_proximity.get(key)
                        if prox:
                            walk_m = float(prox.get('dist_m', 0.0))
                        else:
                            # fallback conservative estimate
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

                    # compute transfer stops: prefer common stops; if none, use near-transfer stop from 'a' side
                    transfer_stops = []
                    prev_point = origin_stop
                    for a, b in zip(found_path, found_path[1:]):
                        key = frozenset((a, b))
                        commons = troncal_common.get(key, [])
                        best = ''
                        if commons:
                            best_d = float('inf')
                            stops_a = id_to_tron.get(a, {}).get('stops', [])
                            prefer_forward = []
                            for s in commons:
                                try:
                                    if prev_point in stops_a and stops_a.index(s) >= stops_a.index(prev_point):
                                        prefer_forward.append(s)
                                except Exception:
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
                        else:
                            prox = troncal_proximity.get(key)
                            # use the 'a' side stop for reporting
                            if prox:
                                a_stop = prox.get('pair', ('', ''))[0]
                                transfer_stops.append(a_stop)
                                if a_stop:
                                    prev_point = a_stop

                    # optional: temporal validation across segments; accumulate time and annotate if infeasible
                    seg_temporal_note = ''
                    try:
                        dep = u.get('depart_time')
                        cur_time = dep
                        path_stops = [origin_stop]
                        # build segment boundaries: transfer stops on 'a' side, end at dest
                        seg_points = list(filter(None, transfer_stops)) + [dest_stop]
                        path_idx = 0
                        for tron_id, next_point in zip(found_path, seg_points):
                            stops = id_to_tron.get(tron_id, {}).get('stops', [])
                            # ensure indices move forward
                            start = path_stops[-1]
                            if start not in stops or next_point not in stops:
                                continue
                            i0 = stops.index(start)
                            i1 = stops.index(next_point)
                            if i1 < i0:
                                seg_temporal_note = 'temporal_infeasible'
                                break
                            # walk through edges
                            for a, b in zip(stops[i0:i1], stops[i0+1:i1+1]):
                                if not edge_allowed(a, b, cur_time, u.get('day_type')):
                                    seg_temporal_note = 'temporal_infeasible'
                                    break
                                # advance time
                                eid = network._edge_id(a, b)
                                metas = network.edge_meta.get(eid, [])
                                minutes = None
                                preferred = [m for m in metas if str(m.get('from_id')) == str(a) and str(m.get('to_id')) == str(b) and (m.get('day_type') or 'habitual') == (u.get('day_type') or 'habitual')]
                                pool = preferred if preferred else [m for m in metas if str(m.get('from_id')) == str(a) and str(m.get('to_id')) == str(b)]
                                for m in pool:
                                    try:
                                        minutes = float(m.get('mean_minutes'))
                                        break
                                    except Exception:
                                        continue
                                if minutes is None:
                                    minutes = 2.0
                                # reuse local helper
                                from datetime import time as _time
                                total = (cur_time.hour * 60 + cur_time.minute + cur_time.second / 60.0 + float(minutes)) if cur_time else float(minutes)
                                total = total % (24 * 60)
                                h = int(total // 60)
                                m = int(total % 60)
                                s = int(round((total - int(total)) * 60))
                                cur_time = _time(h, m, s)
                            if seg_temporal_note:
                                break
                            path_stops.append(next_point)
                    except Exception:
                        seg_temporal_note = ''

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
                        'notes': (notes + (';' + seg_temporal_note if seg_temporal_note else '')).strip(';')
                    })
                    if logger:
                        logger(f"User {u['user_id']}: assigned troncal path {found_path} transfers={transfers} notes={(notes + (';' + seg_temporal_note if seg_temporal_note else '')).strip(';')}")
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

import csv
from datetime import datetime, time
from typing import Dict, List, Any


def parse_time(s: str) -> time:
    # expect H:MM:SS or HH:MM:SS
    if s is None or str(s).strip() == '':
        return None
    return datetime.strptime(str(s).strip(), "%H:%M:%S").time()


def read_csv(path: str) -> List[Dict[str, Any]]:
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            # normalize keys (strip BOM and whitespace)
            nr = {}
            for k, v in r.items():
                if k is None:
                    continue
                nk = k.strip().lstrip('\ufeff')
                nr[nk] = v
            rows.append(nr)
    return rows


def read_paradas(path: str) -> List[Dict[str, Any]]:
    rows = read_csv(path)
    # ensure id and name are strings and lat/lon floats
    for r in rows:
        r['stop_id'] = str(r.get('stop_id'))
        r['name'] = r.get('name')
        r['lat'] = float(r.get('lat')) if r.get('lat') not in (None, '') else None
        r['lon'] = float(r.get('lon')) if r.get('lon') not in (None, '') else None
        r['modes'] = r.get('modes')
    return rows


def read_tiempos(path: str) -> List[Dict[str, Any]]:
    rows = read_csv(path)
    for r in rows:
        r['from_id'] = str(r.get('from_id'))
        r['to_id'] = str(r.get('to_id'))
        r['mode'] = r.get('mode')
        r['day_type'] = r.get('day_type') or 'habitual'
        # times
        r['start_time'] = parse_time(r.get('start_time'))
        r['end_time'] = parse_time(r.get('end_time'))
        # mean_minutes may be decimal
        try:
            r['mean_minutes'] = float(r.get('mean_minutes'))
        except Exception:
            r['mean_minutes'] = None
    return rows


def read_usuarios(path: str) -> List[Dict[str, Any]]:
    rows = read_csv(path)
    for r in rows:
        r['user_id'] = str(r.get('user_id'))
        r['orig_lat'] = float(r.get('orig_lat'))
        r['orig_lon'] = float(r.get('orig_lon'))
        r['dest_lat'] = float(r.get('dest_lat'))
        r['dest_lon'] = float(r.get('dest_lon'))
        r['depart_time'] = parse_time(r.get('depart_time')) if r.get('depart_time') else None
        r['arrival_pref'] = r.get('arrival_pref')
        # max_transfers
        try:
            r['max_transfers'] = int(r.get('max_transfers'))
        except Exception:
            r['max_transfers'] = None
        # mode_prefs is a comma separated string
        mp = r.get('mode_prefs') or ''
        # normalize to list
        r['mode_prefs'] = [m.strip() for m in mp.split(',') if m.strip()]
    return rows

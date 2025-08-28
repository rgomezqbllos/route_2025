# -*- coding: utf-8 -*-
"""
Implementación simplificada del algoritmo ramales para integración.
"""

import pandas as pd
import os
from typing import List, Dict, Tuple
from collections import defaultdict


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


def inventariar_ramales_multimodo(paradas: List[Dict], tiempos: List[Dict], out_prefix: str,
                                   strict_directed: bool = False,
                                   split_on_mode_change: bool = True) -> Tuple[str, str, str, List[Dict]]:
    """
    Implementación simplificada del inventario de ramales.
    Retorna las rutas de los archivos CSV creados y las paradas anotadas.
    """
    
    # Crear DataFrames
    df_p = pd.DataFrame(paradas)
    df_t = pd.DataFrame(tiempos)
    
    # Construir grafo
    adj = defaultdict(set)
    edge_meta = defaultdict(list)
    
    for _, row in df_t.iterrows():
        from_id = str(row['from_id'])
        to_id = str(row['to_id'])
        mode = str(row.get('mode', ''))
        day_type = str(row.get('day_type', ''))
        
        adj[from_id].add(to_id)
        if not strict_directed:
            adj[to_id].add(from_id)
        
        key = tuple(sorted((from_id, to_id)))
        edge_meta[key].append({
            'mode': mode,
            'day_type': day_type,
            'time': row.get('mean_minutes', 0)
        })
    
    # Encontrar componentes conexas
    all_nodes = set(adj.keys())
    visited = set()
    components = []
    
    for node in all_nodes:
        if node in visited:
            continue
        
        # BFS para encontrar componente
        component = set()
        queue = [node]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            for neighbor in adj.get(current, []):
                if neighbor not in visited:
                    queue.append(neighbor)
        
        if component:
            components.append(component)
    
    # Extraer ramales de cada componente
    ramales = []
    ramal_id = 0
    
    for comp in components:
        if len(comp) < 2:
            continue
        
        # Encontrar nodos terminales (grado 1)
        terminals = set()
        for node in comp:
            degree = len([n for n in adj.get(node, []) if n in comp])
            if degree <= 1:
                terminals.add(node)
        
        if len(terminals) >= 2:
            # Buscar caminos entre terminales
            terminal_list = list(terminals)
            for i in range(len(terminal_list)):
                for j in range(i + 1, len(terminal_list)):
                    start = terminal_list[i]
                    end = terminal_list[j]
                    
                    # BFS para encontrar camino
                    queue = [(start, [start])]
                    visited_path = set()
                    
                    while queue:
                        current, path = queue.pop(0)
                        if current == end:
                            # Encontramos un camino
                            modes = set()
                            day_types = set()
                            
                            # Extraer modos y day_types del camino
                            for k in range(len(path) - 1):
                                edge_key = tuple(sorted((path[k], path[k+1])))
                                for meta in edge_meta.get(edge_key, []):
                                    if meta['mode']:
                                        modes.add(meta['mode'])
                                    if meta['day_type']:
                                        day_types.add(meta['day_type'])
                            
                            ramales.append({
                                'id': f'ramal_{ramal_id}',
                                'stops': path,
                                'modes': list(modes),
                                'day_types': list(day_types),
                                'type': 'terminal-terminal'
                            })
                            ramal_id += 1
                            break
                        
                        if current in visited_path:
                            continue
                        visited_path.add(current)
                        
                        for neighbor in adj.get(current, []):
                            if neighbor in comp and neighbor not in visited_path:
                                queue.append((neighbor, path + [neighbor]))
        
        else:
            # No hay terminales, usar camino más largo
            any_node = next(iter(comp))
            longest_path = [any_node]
            
            # Búsqueda greedy del camino más largo
            current = any_node
            visited_greedy = {current}
            
            while True:
                best_next = None
                for neighbor in adj.get(current, []):
                    if neighbor in comp and neighbor not in visited_greedy:
                        best_next = neighbor
                        break
                
                if best_next is None:
                    break
                
                longest_path.append(best_next)
                visited_greedy.add(best_next)
                current = best_next
            
            if len(longest_path) > 1:
                modes = set()
                day_types = set()
                
                for k in range(len(longest_path) - 1):
                    edge_key = tuple(sorted((longest_path[k], longest_path[k+1])))
                    for meta in edge_meta.get(edge_key, []):
                        if meta['mode']:
                            modes.add(meta['mode'])
                        if meta['day_type']:
                            day_types.add(meta['day_type'])
                
                ramales.append({
                    'id': f'ramal_{ramal_id}',
                    'stops': longest_path,
                    'modes': list(modes),
                    'day_types': list(day_types),
                    'type': 'longest-path'
                })
                ramal_id += 1
    
    # Crear archivos de salida
    summary_data = []
    stops_data = []
    arcs_data = []
    
    for ramal in ramales:
        # Summary
        summary_data.append({
            'id': ramal['id'],
            'num_stops': len(ramal['stops']),
            'modes': '|'.join(ramal['modes']) if ramal['modes'] else '',
            'day_types': '|'.join(ramal['day_types']) if ramal['day_types'] else '',
            'type': ramal['type'],
            'start_stop': ramal['stops'][0] if ramal['stops'] else '',
            'end_stop': ramal['stops'][-1] if ramal['stops'] else ''
        })
        
        # Stops
        for i, stop in enumerate(ramal['stops']):
            stops_data.append({
                'ramal_id': ramal['id'],
                'stop_id': stop,
                'sequence': i,
                'day_types': '|'.join(ramal['day_types']) if ramal['day_types'] else ''
            })
        
        # Arcs
        for i in range(len(ramal['stops']) - 1):
            from_stop = ramal['stops'][i]
            to_stop = ramal['stops'][i + 1]
            arcs_data.append({
                'ramal_id': ramal['id'],
                'from_stop': from_stop,
                'to_stop': to_stop,
                'sequence': i,
                'day_types': '|'.join(ramal['day_types']) if ramal['day_types'] else ''
            })
    
    # Escribir CSVs
    summary_csv = f"{out_prefix}_summary.csv"
    stops_csv = f"{out_prefix}_stops.csv"
    arcs_csv = f"{out_prefix}_arcs.csv"
    
    pd.DataFrame(summary_data).to_csv(summary_csv, index=False)
    pd.DataFrame(stops_data).to_csv(stops_csv, index=False)
    pd.DataFrame(arcs_data).to_csv(arcs_csv, index=False)
    
    print(f"Generados {len(ramales)} ramales:")
    print(f"  Summary: {summary_csv}")
    print(f"  Stops: {stops_csv}")
    print(f"  Arcs: {arcs_csv}")
    
    # Paradas anotadas (simplificado)
    paradas_ann = list(paradas)
    
    return summary_csv, stops_csv, arcs_csv, paradas_ann

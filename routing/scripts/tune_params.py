import csv
import itertools
import os
import subprocess
import sys


def analyze_oxd(path):
    total = 0
    no_path = 0
    temporal_inf = 0
    transfers_hist = {}
    roots = 0
    sum_o = 0.0
    sum_d = 0.0
    with open(path, newline='', encoding='utf-8') as f:
        r = csv.DictReader(f)
        for row in r:
            total += 1
            notes = (row.get('notes') or '').strip()
            if 'no_troncal_path_found' in notes:
                no_path += 1
            if 'temporal_infeasible' in notes:
                temporal_inf += 1
            tr = row.get('transfers')
            if tr:
                try:
                    t = int(tr)
                    transfers_hist[t] = transfers_hist.get(t, 0) + 1
                except Exception:
                    pass
            root = (row.get('root') or '').strip()
            if root:
                roots += 1
            try:
                sum_o += float(row.get('origin_dist_m') or 0.0)
                sum_d += float(row.get('dest_dist_m') or 0.0)
            except Exception:
                pass
    avg_o = (sum_o / total) if total else 0.0
    avg_d = (sum_d / total) if total else 0.0
    return {
        'total': total,
        'assigned': roots,
        'no_path': no_path,
        'temporal_infeasible': temporal_inf,
        'transfers_hist': dict(sorted(transfers_hist.items())),
        'avg_origin_m': round(avg_o, 1),
        'avg_dest_m': round(avg_d, 1),
    }


def run_once(near_m, k_near, t_penalty, max_users=2000):
    cmd = [
        sys.executable, '-m', 'src.cli',
        '--transfer-penalty', str(t_penalty),
        '--walk-km-factor', '0.001',
        '--mode-penalty', '0.5',
        '--near-transfer-m', str(near_m),
        '--k-nearest', str(k_near),
        '--max-nearest-m', '2000',
        '--max-users', str(max_users),
        '--quiet'
    ]
    env = dict(os.environ)
    env['PYTHONPATH'] = 'routing'
    subprocess.run(cmd, check=True, env=env, cwd=os.path.join(os.getcwd(), 'routing'))
    oxd = os.path.join(os.getcwd(), 'routing', 'data', 'outputs', 'OxD_assignment.csv')
    return analyze_oxd(oxd)


def main():
    grid_near = [200, 300, 400]
    grid_k = [2, 3]
    grid_t = [0.5, 1.0]
    max_users = int(os.environ.get('TUNE_MAX_USERS', '2000'))

    results = []
    for near_m, k_near, t_penalty in itertools.product(grid_near, grid_k, grid_t):
        metrics = run_once(near_m, k_near, t_penalty, max_users=max_users)
        results.append(((near_m, k_near, t_penalty), metrics))

    # print summary
    print('near_transfer_m,k_nearest,transfer_penalty,total,assigned,no_path,temporal_infeasible,avg_origin_m,avg_dest_m,transfers_hist')
    for (near_m, k_near, t_penalty), m in results:
        print(
            f"{near_m},{k_near},{t_penalty},{m['total']},{m['assigned']},{m['no_path']},{m['temporal_infeasible']},{m['avg_origin_m']},{m['avg_dest_m']},{m['transfers_hist']}"
        )


if __name__ == '__main__':
    main()


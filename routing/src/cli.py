import os
import argparse
from .io_utils import read_paradas, read_tiempos, read_usuarios
from .network import Network
from .ramales import inventariar_ramales_multimodo, load_inputs_all
from .assign import assign_users


def make_logger(log_path: str):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def _log(msg: str):
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(msg + '\n')
        print(msg)

    return _log


def run(
    base_path: str,
    transfer_penalty: float = 1.0,
    walk_km_factor: float = 0.001,
    mode_penalty: float = 0.5,
    near_transfer_m: float = 250.0,
    k_nearest: int = 3,
    max_nearest_m: float = 2000.0,
    max_users: int = None,
    quiet: bool = False,
):
    input_dir = os.path.join(base_path, 'data', 'input')
    outputs = os.path.join(base_path, 'data', 'outputs')
    os.makedirs(outputs, exist_ok=True)
    os.makedirs(os.path.join(outputs, 'logs'), exist_ok=True)

    paradas = read_paradas(os.path.join(input_dir, 'paradas.csv'))
    tiempos = read_tiempos(os.path.join(input_dir, 'tiempos_reco.csv'))
    usuarios = read_usuarios(os.path.join(input_dir, 'usuarios.csv'))
    if isinstance(max_users, int) and max_users > 0:
        usuarios = usuarios[:max_users]

    log_file = os.path.join(outputs, 'logs', 'app.log')
    simple_logger = (None if quiet else make_logger(log_file))
    if simple_logger:
        simple_logger(f"Read {len(paradas)} stops, {len(tiempos)} tiempos, {len(usuarios)} usuarios")

    if args.use_ramales:
        if simple_logger:
            simple_logger('Using integrated ramales inventory algorithm')
        # inventariar_ramales_multimodo expects DataFrames; reuse load_inputs_all
        tiempos_df, paradas_df, issues = load_inputs_all(os.path.join(input_dir, 'tiempos_reco.csv'), os.path.join(input_dir, 'paradas.csv'))
        out_prefix = os.path.join(outputs, 'ramales')
        # Convert DataFrames to list of dicts as expected by the function
        tiempos_list = tiempos_df.to_dict('records')
        paradas_list = paradas_df.to_dict('records')
        sum_csv, sto_csv, arc_csv, paradas_ann = inventariar_ramales_multimodo(paradas_list, tiempos_list, out_prefix)

        # convert summary+stops to troncales list expected by Network.export_red
        troncales = []
        try:
            import pandas as _pd
            rs = _pd.read_csv(sum_csv, dtype=str).fillna("")
            st = _pd.read_csv(sto_csv, dtype=str).fillna("")
            # group stops per branch preserving order
            for _, row in rs.iterrows():
                bid = row['branch_id']
                day_type = row.get('day_type','')
                modes = row.get('modes','')
                stops_seq = st.loc[st['branch_id'] == bid].sort_values('order')['stop_id'].astype(str).tolist()
                # Build an id that preserves day_type/modes if needed
                troncal_id = bid
                troncales.append({'id': troncal_id, 'stops': stops_seq, 'modes': set(modes.split("|")) if modes else set(), 'day_types': set([day_type]) if day_type else set()})
        except Exception:
            # fallback: call legacy Network logic if CSVs can't be read
            net = Network(paradas, tiempos)
            troncales = net.find_troncales(generate_terminal_paths=args.terminal_paths)

        # write to canonical red.csv path for downstream compatibility
        red_path = os.path.join(outputs, 'red.csv')
        net = Network(paradas, tiempos)
        net.export_red(troncales, red_path)
        if simple_logger:
            simple_logger(f"Exported red (from ramales) to {red_path}")
    else:
        net = Network(paradas, tiempos)
        troncales = net.find_troncales(generate_terminal_paths=args.terminal_paths)
        if simple_logger:
            simple_logger(f"Found {len(troncales)} troncales")

        red_path = os.path.join(outputs, 'red.csv')
        net.export_red(troncales, red_path)
        if simple_logger:
            simple_logger(f"Exported red to {red_path}")

    oxd_path = os.path.join(outputs, 'OxD_assignment.csv')
    assign_users(
        usuarios,
        net,
        troncales,
        oxd_path,
        logger=simple_logger,
        transfer_penalty=transfer_penalty,
        walk_km_factor=walk_km_factor,
        mode_penalty=mode_penalty,
        near_transfer_m=near_transfer_m,
        k_nearest=k_nearest,
        max_nearest_m=max_nearest_m,
    )
    if simple_logger:
        simple_logger(f"Wrote assignments to {oxd_path}")


if __name__ == '__main__':
    # assume package root is two levels up from src
    parser = argparse.ArgumentParser()
    parser.add_argument('--transfer-penalty', type=float, default=1.0)
    parser.add_argument('--walk-km-factor', type=float, default=0.001)
    parser.add_argument('--mode-penalty', type=float, default=0.5)
    parser.add_argument('--near-transfer-m', type=float, default=250.0, help='Maximum walking distance (m) to allow transfers between nearby stops of different troncales')
    parser.add_argument('--k-nearest', type=int, default=3, help='Number of nearest troncal stops to consider for origin/destination seeding')
    parser.add_argument('--max-nearest-m', type=float, default=2000.0, help='Maximum search radius (m) for nearest troncal stops; fallback returns nearest even beyond this to keep connectivity')
    parser.add_argument('--max-users', type=int, default=None, help='Limit number of users processed for faster experimentation')
    parser.add_argument('--quiet', action='store_true', help='Reduce logging verbosity (no per-user logs)')
    parser.add_argument('--terminal-paths', action='store_true', help='Generate troncales as longest simple paths between terminal nodes per mode')
    parser.add_argument('--use-ramales', action='store_true', help='Use the integrated ramales inventory algorithm (requires pandas/numpy)')
    args = parser.parse_args()
    base = os.path.dirname(os.path.dirname(__file__))
    run(
        base,
        transfer_penalty=args.transfer_penalty,
        walk_km_factor=args.walk_km_factor,
        mode_penalty=args.mode_penalty,
        near_transfer_m=args.near_transfer_m,
        k_nearest=args.k_nearest,
        max_nearest_m=args.max_nearest_m,
        max_users=args.max_users,
        quiet=args.quiet,
    )

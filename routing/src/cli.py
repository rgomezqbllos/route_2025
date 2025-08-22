import os
import argparse
from .io_utils import read_paradas, read_tiempos, read_usuarios
from .network import Network
from .assign import assign_users


def make_logger(log_path: str):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def _log(msg: str):
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(msg + '\n')
        print(msg)

    return _log


def run(base_path: str, transfer_penalty: float = 1.0, walk_km_factor: float = 0.001, mode_penalty: float = 0.5):
    input_dir = os.path.join(base_path, 'data', 'input')
    outputs = os.path.join(base_path, 'data', 'outputs')
    os.makedirs(outputs, exist_ok=True)
    os.makedirs(os.path.join(outputs, 'logs'), exist_ok=True)

    paradas = read_paradas(os.path.join(input_dir, 'paradas.csv'))
    tiempos = read_tiempos(os.path.join(input_dir, 'tiempos_reco.csv'))
    usuarios = read_usuarios(os.path.join(input_dir, 'usuarios.csv'))

    log_file = os.path.join(outputs, 'logs', 'app.log')
    simple_logger = make_logger(log_file)
    simple_logger(f"Read {len(paradas)} stops, {len(tiempos)} tiempos, {len(usuarios)} usuarios")

    net = Network(paradas, tiempos)
    troncales = net.find_troncales()
    simple_logger(f"Found {len(troncales)} troncales")

    red_path = os.path.join(outputs, 'red.csv')
    net.export_red(troncales, red_path)
    simple_logger(f"Exported red to {red_path}")

    oxd_path = os.path.join(outputs, 'OxD_assignment.csv')
    assign_users(usuarios, net, troncales, oxd_path, logger=simple_logger,
                 transfer_penalty=transfer_penalty, walk_km_factor=walk_km_factor, mode_penalty=mode_penalty)
    simple_logger(f"Wrote assignments to {oxd_path}")


if __name__ == '__main__':
    # assume package root is two levels up from src
    parser = argparse.ArgumentParser()
    parser.add_argument('--transfer-penalty', type=float, default=1.0)
    parser.add_argument('--walk-km-factor', type=float, default=0.001)
    parser.add_argument('--mode-penalty', type=float, default=0.5)
    args = parser.parse_args()
    base = os.path.dirname(os.path.dirname(__file__))
    run(base, transfer_penalty=args.transfer_penalty, walk_km_factor=args.walk_km_factor, mode_penalty=args.mode_penalty)

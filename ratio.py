import threading
from code.process_torrent import process_torrent
import argparse
import json
import sys


def parse_args():
    """Create the arguments"""
    parser = argparse.ArgumentParser('\nratio.py -c <configuration-file.json> or ratio.py --web')
    parser.add_argument("-c", "--configuration", help="Configuration file")
    parser.add_argument("--web", action="store_true", help="Start the web UI on localhost:5001")
    parser.add_argument("--port", type=int, default=5001, help="Port for web UI (default: 5001)")
    return parser.parse_args()


def load_configuration(configuration_file):
    with open(configuration_file) as f:
        configuration = json.load(f)

    if len(configuration["torrents"]) == 0:
        return None

    return configuration["torrents"]


def runTorrentTracker(configuration):
    to = process_torrent(configuration)
    to.tracker_process()


if __name__ == "__main__":
    args = parse_args()

    if args.web:
        from web.app import start_web
        print(f"Starting Ratio.py Web UI on http://127.0.0.1:{args.port}")
        start_web(port=args.port)
    elif args.configuration:
        torrentsList = load_configuration(args.configuration)
        if not torrentsList:
            sys.exit()
        for i in range(len(torrentsList)):
            threading.Thread(target=runTorrentTracker, args=(torrentsList[i],)).start()
    else:
        print("Usage: ratio.py --web  or  ratio.py -c <configuration.json>")
        sys.exit()

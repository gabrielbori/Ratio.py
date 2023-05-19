import threading
from code.process_torrent import process_torrent
import argparse
import json
import sys


def parse_args():
    """Create the arguments"""
    parser = argparse.ArgumentParser('\nratio.py -c <configuration-file.json>')
    parser.add_argument("-c", "--configuration", help="Configuration file")
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
    if args.configuration:
        torrentsList = load_configuration(args.configuration)
    else:
        sys.exit()

    if not torrentsList:
        sys.exit()

    for i in range(len(torrentsList)):
        threading.Thread(target=runTorrentTracker, args=(torrentsList[i],)).start()

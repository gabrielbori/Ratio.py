from code.decoding_bencoded import bencoding
from code.torrentclientfactory import Transmission292

from hashlib import sha1
from urllib.parse import quote_plus
import requests
import logging
import random
from time import sleep, time
import threading
from struct import unpack
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MAX_RETRIES = 5


class process_torrent():
    def __init__(self, configuration, stop_event=None, already_seeded=0, progress_callback=None):
        self.configuration = configuration
        self.stop_event = stop_event or threading.Event()
        self.force_event = threading.Event()
        self.already_seeded = already_seeded
        self.progress_callback = progress_callback
        self.interval = 0
        self.wait_remaining = 0
        self.status = {
            'torrent_name': os.path.basename(configuration['torrent']),
            'seeded_mb': already_seeded,
            'limit_mb': configuration['limit'],
            'upload_speed': configuration['upload'],
            'state': 'idle',
            'wait_remaining': 0,
            'wait_total': 0,
            'last_error': None
        }
        self.open_torrent()
        self.torrentclient = Transmission292(self.tracker_info_hash())

    def open_torrent(self):
        torrent_file = self.configuration['torrent']
        if not os.path.exists(torrent_file):
            raise FileNotFoundError(f"Torrent file not found: {torrent_file}")
        with open(torrent_file, 'rb') as tf:
            data = tf.read()
        self.b_enc = bencoding()
        self.metainfo = self.b_enc.bdecode(data)
        self.info = self.metainfo['info']
        if 'length' not in self.info:
            self.info['length'] = 0
            for file in self.info['files']:
                self.info['length'] += file['length']

    def tracker_info_hash(self):
        raw_info = self.b_enc.get_dict('info')
        hash_factory = sha1()
        hash_factory.update(raw_info)
        hashed = hash_factory.hexdigest()
        sha = bytearray.fromhex(hashed)
        return str(quote_plus(sha))

    def send_request(self, params, headers):
        url = self.metainfo['announce']
        for attempt in range(MAX_RETRIES):
            try:
                r = requests.get(url, params=params, headers=headers, timeout=30)
                return r.content
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                logger.warning(f"Request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    sleep(2 ** attempt)
                else:
                    raise

    def tracker_start_request(self):
        tc = self.torrentclient
        headers = tc.get_headers()
        params = tc.get_query(uploaded=0, downloaded=0, event='started')
        self._update_status_field('state', 'connecting')
        logger.info('Sending start request to tracker')
        content = self.send_request(params, headers)
        self.tracker_response_parser(content)

    def tracker_response_parser(self, tr_response):
        try:
            b_enc = bencoding()
            response = b_enc.bdecode(tr_response)
            logger.info('Received tracker response')
            raw_peers = b_enc.get_dict('peers')
            i = 0
            peers = []
            while i < len(raw_peers) - 6:
                peer = raw_peers[i:i + 6]
                i += 6
                unpacked_ip = unpack('BBBB', peer[0:4])
                ip = ".".join(str(x) for x in unpacked_ip)
                unpacked_port = unpack('!H', peer[4:6])
                port = unpacked_port[0]
                peers.append((ip, port))
            self.interval = min(response['interval'], 1200)
        except Exception as e:
            logger.error(f"Failed to parse tracker response: {e}")
            self.interval = 1200

    def wait(self):
        logger.info(f'Waiting {self.interval} seconds')
        self.wait_remaining = self.interval
        self.status['wait_total'] = self.interval
        self.status['wait_remaining'] = self.interval
        self._notify_callback()

        t = 0
        while t < self.interval:
            if self.stop_event.is_set():
                return
            if self.force_event.is_set():
                self.force_event.clear()
                logger.info('Force seed triggered, skipping wait')
                break
            t += 1
            self.wait_remaining = self.interval - t
            self.status['wait_remaining'] = self.wait_remaining
            self._notify_callback()
            sleep(1)

        self.status['wait_remaining'] = 0
        self.status['wait_total'] = 0

    def _update_status_field(self, key, value):
        self.status[key] = value
        self._notify_callback()

    def _update_status(self, seeded, state='running'):
        self.status['seeded_mb'] = seeded
        self.status['state'] = state
        self.status['last_error'] = None
        self._notify_callback()

    def _notify_callback(self):
        if self.progress_callback:
            self.progress_callback(self.status)

    def tracker_process(self):
        seeded = self.already_seeded
        limit = self.configuration['limit']
        self._update_status(seeded, state='running')

        while not self.stop_event.is_set():
            try:
                self.tracker_start_request()
            except Exception as e:
                logger.error(f"Start request failed: {e}")
                self.status['last_error'] = str(e)
                self._update_status_field('state', 'error')
                # Wait before retrying
                self.interval = 60
                self.wait()
                continue

            min_up = self.interval - (self.interval * 0.1)
            max_up = self.interval
            randomize_upload = random.randint(int(min_up), int(max_up))
            uploaded = int(self.configuration['upload']) * 1000 * randomize_upload
            downloaded = 0

            tc = self.torrentclient
            headers = tc.get_headers()
            params = tc.get_query(uploaded=uploaded, downloaded=downloaded, event='stopped')

            self._update_status_field('state', 'seeding')

            try:
                content = self.send_request(params, headers)
            except Exception as e:
                logger.error(f"Seed request failed: {e}")
                self.status['last_error'] = str(e)
                self._update_status_field('state', 'error')
                self.interval = 60
                self.wait()
                continue

            seeded = seeded + uploaded / 1000000

            logger.info(f'Torrent: {self.configuration["torrent"]}')
            logger.info(f'Seeded: {seeded:.1f} MB | Limit: {limit} MB')

            self._update_status(seeded, state='running')

            if seeded >= limit:
                self._update_status(seeded, state='finished')
                logger.info(f'Limit reached for {self.configuration["torrent"]}')
                return

            self.tracker_response_parser(content)

            self._update_status_field('state', 'waiting')
            self.wait()

        self._update_status(seeded, state='stopped')
        logger.info(f'Stopped {self.configuration["torrent"]} at {seeded:.1f} MB')

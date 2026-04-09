import threading
import json
import os
import uuid
import logging
from datetime import datetime
from code.process_torrent import process_torrent

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
PROGRESS_FILE = os.path.join(DATA_DIR, 'progress.json')
TORRENT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'torrents')


class TorrentManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.torrents = {}
        os.makedirs(DATA_DIR, exist_ok=True)
        self._load_progress()
        self._check_duplicates()

    def _load_progress(self):
        if os.path.exists(PROGRESS_FILE):
            with open(PROGRESS_FILE, 'r') as f:
                self.progress = json.load(f)
        else:
            self.progress = {}

    def _check_duplicates(self):
        if not os.path.exists(TORRENT_DIR):
            return
        files = [f for f in os.listdir(TORRENT_DIR) if f.endswith('.torrent')]
        seen = {}
        duplicates = []
        for f in files:
            name = f.lower()
            if name in seen:
                duplicates.append((f, seen[name]))
            else:
                seen[name] = f
        if duplicates:
            for dup, original in duplicates:
                logger.warning(f"Duplicate torrent file detected: '{dup}' matches '{original}'")
        logger.info(f"Startup check: {len(files)} torrent file(s), {len(self.progress)} saved progress entries, {len(duplicates)} duplicate(s)")

    def _save_progress(self):
        with open(PROGRESS_FILE, 'w') as f:
            json.dump(self.progress, f, indent=2)

    def _progress_callback(self, torrent_id, status):
        with self.lock:
            torrent_name = status['torrent_name']
            self.progress[torrent_name] = {
                'seeded_mb': round(status['seeded_mb'], 1),
                'limit_mb': status['limit_mb'],
                'last_updated': datetime.now().isoformat()
            }
            self._save_progress()

    def add_torrent(self, torrent_path, upload_speed, limit):
        with self.lock:
            torrent_name = os.path.basename(torrent_path)

            # Check for duplicates
            for entry in self.torrents.values():
                if entry['status']['torrent_name'] == torrent_name:
                    return None

            torrent_id = str(uuid.uuid4())[:8]
            already_seeded = 0
            if torrent_name in self.progress:
                already_seeded = self.progress[torrent_name].get('seeded_mb', 0)

            self.torrents[torrent_id] = {
                'config': {
                    'torrent': torrent_path,
                    'upload': str(upload_speed),
                    'limit': limit
                },
                'thread': None,
                'stop_event': None,
                'process': None,
                'status': {
                    'torrent_name': torrent_name,
                    'seeded_mb': already_seeded,
                    'limit_mb': limit,
                    'upload_speed': str(upload_speed),
                    'state': 'idle'
                },
                'already_seeded': already_seeded
            }
            return torrent_id

    def start_torrent(self, torrent_id):
        with self.lock:
            if torrent_id not in self.torrents:
                return {'ok': False, 'error': 'Torrent not found'}
            entry = self.torrents[torrent_id]
            active_states = ('running', 'waiting', 'connecting', 'seeding')
            if entry['status']['state'] in active_states:
                return {'ok': False, 'error': 'Already running'}

            stop_event = threading.Event()
            entry['stop_event'] = stop_event

            def callback(status):
                self._progress_callback(torrent_id, status)
                with self.lock:
                    entry['status'] = status.copy()

            try:
                proc = process_torrent(
                    entry['config'],
                    stop_event=stop_event,
                    already_seeded=entry['already_seeded'],
                    progress_callback=callback
                )
            except Exception as e:
                entry['status']['state'] = 'error'
                entry['status']['last_error'] = str(e)
                return {'ok': False, 'error': str(e)}

            entry['process'] = proc

            def run():
                try:
                    proc.tracker_process()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Torrent thread error: {e}", exc_info=True)
                    with self.lock:
                        entry['status']['state'] = 'error'
                        entry['status']['last_error'] = str(e)

            thread = threading.Thread(target=run, daemon=True)
            entry['thread'] = thread
            entry['status']['state'] = 'starting'
            thread.start()
            return {'ok': True}

    def force_torrent(self, torrent_id):
        with self.lock:
            if torrent_id not in self.torrents:
                return False
            entry = self.torrents[torrent_id]
            if entry['process'] and hasattr(entry['process'], 'force_event'):
                entry['process'].force_event.set()
                return True
            return False

    def stop_torrent(self, torrent_id):
        with self.lock:
            if torrent_id not in self.torrents:
                return False
            entry = self.torrents[torrent_id]
            if entry['stop_event']:
                entry['stop_event'].set()
            return True

    def stop_all(self):
        with self.lock:
            count = 0
            for entry in self.torrents.values():
                if entry['stop_event'] and not entry['stop_event'].is_set():
                    entry['stop_event'].set()
                    count += 1
            return count

    def remove_torrent(self, torrent_id):
        with self.lock:
            if torrent_id not in self.torrents:
                return False
            entry = self.torrents[torrent_id]
            if entry['stop_event']:
                entry['stop_event'].set()
            del self.torrents[torrent_id]
            return True

    def reset_progress(self, torrent_id):
        with self.lock:
            if torrent_id not in self.torrents:
                return False
            entry = self.torrents[torrent_id]
            torrent_name = entry['status']['torrent_name']
            if torrent_name in self.progress:
                del self.progress[torrent_name]
                self._save_progress()
            entry['already_seeded'] = 0
            entry['status']['seeded_mb'] = 0
            return True

    def delete_progress(self, torrent_name):
        with self.lock:
            if torrent_name in self.progress:
                del self.progress[torrent_name]
                self._save_progress()
                return True
            return False

    def get_all_status(self):
        with self.lock:
            result = []
            for tid, entry in self.torrents.items():
                status = entry['status'].copy()
                status['id'] = tid
                status['already_seeded'] = entry['already_seeded']
                # Get live wait info from process
                proc = entry.get('process')
                if proc:
                    status['wait_remaining'] = proc.status.get('wait_remaining', 0)
                    status['wait_total'] = proc.status.get('wait_total', 0)
                result.append(status)
            return result

    def get_progress(self):
        with self.lock:
            return dict(self.progress)

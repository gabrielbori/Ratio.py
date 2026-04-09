from flask import Flask, render_template, request, jsonify
from code.torrent_manager import TorrentManager
import os

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TORRENT_DIR = os.path.join(BASE_DIR, 'torrents')

app = Flask(__name__,
            template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
            static_folder=os.path.join(os.path.dirname(__file__), 'static'))

manager = TorrentManager()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/torrents', methods=['GET'])
def list_torrents():
    return jsonify(manager.get_all_status())


@app.route('/api/torrents', methods=['POST'])
def add_torrent():
    files = request.files.getlist('torrent')
    if not files or files[0].filename == '':
        return jsonify({'error': 'No torrent file provided'}), 400

    upload_speed = request.form.get('upload_speed', '300')
    limit = request.form.get('limit', '10000')

    try:
        upload_speed = int(upload_speed)
        limit = int(limit)
    except ValueError:
        return jsonify({'error': 'Upload speed and limit must be numbers'}), 400

    if upload_speed <= 0 or limit <= 0:
        return jsonify({'error': 'Upload speed and limit must be positive'}), 400

    os.makedirs(TORRENT_DIR, exist_ok=True)
    ids = []
    skipped = []
    for file in files:
        if not file.filename.endswith('.torrent'):
            continue
        filepath = os.path.join(TORRENT_DIR, file.filename)
        file.save(filepath)
        torrent_id = manager.add_torrent(filepath, upload_speed, limit)
        if torrent_id is None:
            skipped.append(file.filename)
            continue
        manager.start_torrent(torrent_id)
        ids.append(torrent_id)

    if not ids and not skipped:
        return jsonify({'error': 'No valid .torrent files provided'}), 400

    msg = f'{len(ids)} torrent(s) added and started'
    if skipped:
        msg += f', {len(skipped)} skipped (already added)'
    if not ids and skipped:
        return jsonify({'error': f'All torrents already added: {", ".join(skipped)}'}), 409

    return jsonify({'ids': ids, 'message': msg})


@app.route('/api/torrents/stop-all', methods=['POST'])
def stop_all_torrents():
    count = manager.stop_all()
    return jsonify({'message': f'Stopped {count} torrent(s)'})


@app.route('/api/progress/resume-all', methods=['POST'])
def resume_all_pending():
    data = request.get_json() or {}
    upload_speed = data.get('upload_speed', 300)
    ids = []
    progress = manager.get_progress()
    for name, p in progress.items():
        if p['seeded_mb'] >= p['limit_mb']:
            continue
        filepath = os.path.join(TORRENT_DIR, name)
        if not os.path.exists(filepath):
            continue
        torrent_id = manager.add_torrent(filepath, int(upload_speed), p['limit_mb'])
        if torrent_id is None:
            continue
        manager.start_torrent(torrent_id)
        ids.append(torrent_id)
    return jsonify({'ids': ids, 'message': f'{len(ids)} pending torrent(s) resumed and started'})


@app.route('/api/torrents/<torrent_id>/start', methods=['POST'])
def start_torrent(torrent_id):
    result = manager.start_torrent(torrent_id)
    if result['ok']:
        return jsonify({'message': 'Started'})
    return jsonify({'error': result.get('error', 'Could not start torrent')}), 400


@app.route('/api/torrents/<torrent_id>/force', methods=['POST'])
def force_torrent(torrent_id):
    if manager.force_torrent(torrent_id):
        return jsonify({'message': 'Force seed triggered'})
    return jsonify({'error': 'Could not force torrent'}), 400


@app.route('/api/torrents/<torrent_id>/stop', methods=['POST'])
def stop_torrent(torrent_id):
    if manager.stop_torrent(torrent_id):
        return jsonify({'message': 'Stopped'})
    return jsonify({'error': 'Could not stop torrent'}), 400


@app.route('/api/torrents/<torrent_id>', methods=['DELETE'])
def remove_torrent(torrent_id):
    if manager.remove_torrent(torrent_id):
        return jsonify({'message': 'Removed'})
    return jsonify({'error': 'Torrent not found'}), 404


@app.route('/api/torrents/<torrent_id>/reset', methods=['POST'])
def reset_progress(torrent_id):
    if manager.reset_progress(torrent_id):
        return jsonify({'message': 'Progress reset'})
    return jsonify({'error': 'Torrent not found'}), 404


@app.route('/api/progress', methods=['GET'])
def get_progress():
    progress = manager.get_progress()
    for name, data in progress.items():
        filepath = os.path.join(TORRENT_DIR, name)
        data['has_file'] = os.path.exists(filepath)
    return jsonify(progress)


@app.route('/api/torrents/resume', methods=['POST'])
def resume_torrent():
    data = request.get_json()
    torrent_name = data.get('torrent_name')
    upload_speed = data.get('upload_speed', 300)
    limit = data.get('limit')

    if not torrent_name:
        return jsonify({'error': 'torrent_name is required'}), 400

    filepath = os.path.join(TORRENT_DIR, torrent_name)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Torrent file not found on disk'}), 404

    try:
        upload_speed = int(upload_speed)
        limit = int(limit)
    except (ValueError, TypeError):
        return jsonify({'error': 'Upload speed and limit must be numbers'}), 400

    torrent_id = manager.add_torrent(filepath, upload_speed, limit)
    if torrent_id is None:
        return jsonify({'error': 'Torrent already added'}), 409
    manager.start_torrent(torrent_id)
    return jsonify({'id': torrent_id, 'message': 'Torrent resumed and started'})


@app.route('/api/progress/<path:torrent_name>', methods=['DELETE'])
def delete_progress(torrent_name):
    if manager.delete_progress(torrent_name):
        return jsonify({'message': 'Progress deleted'})
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/progress/lookup', methods=['GET'])
def lookup_progress():
    filename = request.args.get('filename', '')
    progress = manager.get_progress()
    if filename in progress:
        return jsonify(progress[filename])
    return jsonify({})


def start_web(host='127.0.0.1', port=5001):
    app.run(host=host, port=port, debug=False)

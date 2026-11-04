import base64
import binascii
import hashlib
import io
import json
import os
import time

from flask import Flask, jsonify, request, send_file
from minio import Minio
from minio.error import S3Error
import redis

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_REQUEST_BYTES', str(16 * 1024 * 1024)))
QUEUE = os.getenv('WORK_QUEUE', 'toWorker')
INPUT_BUCKET = os.getenv('INPUT_BUCKET', 'queue')
OUTPUT_BUCKET = os.getenv('OUTPUT_BUCKET', 'output')
TRACKS = {'bass.mp3', 'base.mp3', 'vocals.mp3', 'drums.mp3', 'other.mp3'}
ALLOWED_MODELS = {'htdemucs', 'htdemucs_ft', 'mdx_extra_q'}

r = redis.Redis(host=os.getenv('REDIS_HOST', 'redis'), port=int(os.getenv('REDIS_PORT', '6379')), decode_responses=True, socket_connect_timeout=5)
m = Minio(os.getenv('MINIO_ENDPOINT', 'minio-local:9000'), access_key=os.environ['MINIO_ACCESS_KEY'], secret_key=os.environ['MINIO_SECRET_KEY'], secure=False)


def log(message):
    print(message, flush=True)
    try:
        r.lpush('logging', f'REST: {message}')
    except redis.RedisError:
        app.logger.exception('Could not send Redis log')


def ensure_buckets():
    for bucket in (INPUT_BUCKET, OUTPUT_BUCKET):
        if not m.bucket_exists(bucket):
            try:
                m.make_bucket(bucket)
            except S3Error as exc:
                if exc.code not in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
                    raise


def job_key(songhash):
    return f'job:{songhash}'


@app.get('/')
def home():
    return jsonify(service='demucs-rest', message='Music separation API')


@app.get('/health')
def health():
    try:
        r.ping()
        m.list_buckets()
        return jsonify(status='ok')
    except Exception as exc:
        return jsonify(status='unavailable', error=str(exc)), 503


@app.post('/apiv1/separate')
def separate():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get('mp3'), str):
        return jsonify(error='Expected JSON with base64-encoded mp3 string'), 400
    model = body.get('model') or 'htdemucs'
    if model not in ALLOWED_MODELS:
        return jsonify(error=f'Unsupported model. Choose from {sorted(ALLOWED_MODELS)}'), 400
    try:
        audio = base64.b64decode(body['mp3'], validate=True)
    except (ValueError, binascii.Error):
        return jsonify(error='Invalid base64 MP3 payload'), 400
    if not audio:
        return jsonify(error='Empty MP3'), 400
    songhash = hashlib.sha256(audio).hexdigest()
    callback = body.get('callback')
    if callback is not None and not isinstance(callback, dict):
        return jsonify(error='callback must be an object'), 400

    try:
        ensure_buckets()
        existing = r.hgetall(job_key(songhash))
        if existing.get('status') in ('queued', 'processing', 'completed'):
            return jsonify(hash=songhash, reason=f"Song already {existing['status']}")
        m.put_object(INPUT_BUCKET, f'{songhash}.mp3', io.BytesIO(audio), length=len(audio), content_type='audio/mpeg')
        job = {'hash': songhash, 'model': model, 'callback': callback}
        r.hset(job_key(songhash), mapping={'status': 'queued', 'model': model, 'updated_at': str(time.time()), 'error': ''})
        r.lpush(QUEUE, json.dumps(job))
        log(f'enqueued {songhash} using {model}')
        return jsonify(hash=songhash, reason='Song enqueued for separation')
    except Exception as exc:
        app.logger.exception('Failed to enqueue job')
        return jsonify(error=str(exc)), 503


@app.get('/apiv1/queue')
@app.get('/apiv1/queue/')
def queue():
    try:
        entries = [json.loads(v).get('hash', v) for v in r.lrange(QUEUE, 0, -1)]
        return jsonify(queue=entries)
    except Exception as exc:
        return jsonify(error=str(exc)), 503


@app.get('/apiv1/status/<songhash>')
def status(songhash):
    values = r.hgetall(job_key(songhash))
    if not values:
        return jsonify(error='Unknown job'), 404
    return jsonify(hash=songhash, **values)


def normalized_track(track):
    if track not in TRACKS:
        return None
    return 'bass.mp3' if track == 'base.mp3' else track


@app.get('/apiv1/track/<songhash>/<track>')
def get_track(songhash, track):
    track = normalized_track(track)
    if track is None:
        return jsonify(error='Unknown track'), 400
    obj = None
    try:
        obj = m.get_object(OUTPUT_BUCKET, f'{songhash}-{track}')
        data = obj.read()
    except S3Error as exc:
        if exc.code in ('NoSuchKey', 'NoSuchBucket'):
            return jsonify(error='Track not yet available'), 404
        return jsonify(error=str(exc)), 503
    finally:
        if obj is not None:
            obj.close()
            obj.release_conn()
    return send_file(io.BytesIO(data), mimetype='audio/mpeg', as_attachment=True, download_name=track)


@app.route('/apiv1/remove/<songhash>/<track>', methods=['GET', 'DELETE'])
def remove_track(songhash, track):
    track = normalized_track(track)
    if track is None:
        return jsonify(error='Unknown track'), 400
    try:
        m.remove_object(OUTPUT_BUCKET, f'{songhash}-{track}')
        return jsonify(hash=songhash, removed=track)
    except Exception as exc:
        return jsonify(error=str(exc)), 503


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

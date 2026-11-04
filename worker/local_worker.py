import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from minio import Minio
from minio.error import S3Error
import redis
import requests

QUEUE = os.getenv('WORK_QUEUE', 'toWorker')
INPUT_BUCKET = os.getenv('INPUT_BUCKET', 'queue')
OUTPUT_BUCKET = os.getenv('OUTPUT_BUCKET', 'output')
ALLOWED_MODELS = {'htdemucs', 'htdemucs_ft', 'mdx_extra_q'}
r = redis.Redis(host=os.getenv('REDIS_HOST', 'redis'), port=int(os.getenv('REDIS_PORT', '6379')), decode_responses=True)
m = Minio(os.getenv('MINIO_ENDPOINT', 'minio-local:9000'), access_key=os.environ['MINIO_ACCESS_KEY'], secret_key=os.environ['MINIO_SECRET_KEY'], secure=False)


def log(message):
    print(message, flush=True)
    try:
        r.lpush('logging', f'WORKER: {message}')
    except redis.RedisError:
        pass


def set_status(songhash, status, error=''):
    r.hset(f'job:{songhash}', mapping={'status': status, 'error': error[:1000], 'updated_at': str(time.time())})


def process(job):
    songhash = job['hash']
    if not isinstance(songhash, str) or len(songhash) != 64 or any(c not in '0123456789abcdef' for c in songhash):
        raise ValueError('Invalid song hash')
    model = job.get('model') or 'htdemucs'
    if model not in ALLOWED_MODELS:
        raise ValueError(f'Unsupported model: {model}')
    set_status(songhash, 'processing')
    log(f'started {songhash}, model={model}')

    with tempfile.TemporaryDirectory(prefix='demucs-') as tmp:
        input_file = Path(tmp) / f'{songhash}.mp3'
        output_dir = Path(tmp) / 'output'
        m.fget_object(INPUT_BUCKET, f'{songhash}.mp3', str(input_file))
        command = ['python', '-m', 'demucs.separate', '--mp3', '--mp3-bitrate', '128', '-n', model, '--out', str(output_dir), str(input_file)]
        log(f'running Demucs for {songhash}')
        subprocess.run(command, check=True)
        stems_dir = output_dir / model / songhash
        for stem in ('bass', 'drums', 'vocals', 'other'):
            path = stems_dir / f'{stem}.mp3'
            if not path.is_file():
                raise FileNotFoundError(f'Missing output: {path}')
            m.fput_object(OUTPUT_BUCKET, f'{songhash}-{stem}.mp3', str(path), content_type='audio/mpeg')
    set_status(songhash, 'completed')
    log(f'completed {songhash}')
    # Optional callback; disabled by default for local safety.
    callback = job.get('callback')
    if os.getenv('ENABLE_CALLBACKS', 'false').lower() == 'true' and isinstance(callback, dict) and callback.get('url'):
        try:
            requests.post(callback['url'], json=callback.get('data', {}), timeout=5).raise_for_status()
        except requests.RequestException as exc:
            log(f'callback failed for {songhash}: {exc}')


def main():
    log('worker started')
    while True:
        try:
            message = r.blpop(QUEUE, timeout=5)
            if message is None:
                continue
            job = json.loads(message[1])
            songhash = job.get('hash')
            try:
                process(job)
            except Exception as exc:
                log(f'ERROR processing {songhash}: {exc}')
                if songhash:
                    set_status(songhash, 'failed', str(exc))
        except (redis.RedisError, S3Error, ValueError, KeyError, TypeError) as exc:
            log(f'worker loop error: {exc}')
            time.sleep(3)


if __name__ == '__main__':
    main()

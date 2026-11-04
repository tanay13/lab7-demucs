import argparse
import base64
import os
import time
import requests

parser = argparse.ArgumentParser()
parser.add_argument('mp3', help='Path to a short MP3 file')
parser.add_argument('--model', default='htdemucs', choices=['htdemucs', 'htdemucs_ft', 'mdx_extra_q'])
parser.add_argument('--url', default=os.getenv('REST_URL', 'http://localhost:5000'))
parser.add_argument('--timeout', type=int, default=900)
args = parser.parse_args()

with open(args.mp3, 'rb') as audio:
    payload = base64.b64encode(audio.read()).decode('ascii')
resp = requests.post(args.url + '/apiv1/separate', json={'mp3': payload, 'model': args.model}, timeout=60)
resp.raise_for_status()
songhash = resp.json()['hash']
print('job hash:', songhash)

end = time.monotonic() + args.timeout
while time.monotonic() < end:
    result = requests.get(f'{args.url}/apiv1/status/{songhash}', timeout=10)
    result.raise_for_status()
    status = result.json()
    print('status:', status['status'])
    if status['status'] == 'completed':
        os.makedirs('local-output', exist_ok=True)
        for stem in ('vocals', 'drums', 'bass', 'other'):
            response = requests.get(f'{args.url}/apiv1/track/{songhash}/{stem}.mp3', timeout=60)
            response.raise_for_status()
            path = f'local-output/{songhash}-{stem}.mp3'
            with open(path, 'wb') as output:
                output.write(response.content)
            print('saved', path)
        break
    if status['status'] == 'failed':
        raise SystemExit('Demucs failed: ' + status.get('error', 'unknown error'))
    time.sleep(5)
else:
    raise SystemExit('Timed out waiting for job')

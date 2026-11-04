# Local Minikube Demucs implementation

Copy contents of this overlay into your existing repository root. No existing files are overwritten; local-only files use distinct filenames.

## Start

```bash
minikube start --driver=docker --cpus=4 --memory=10240 --disk-size=30g
kubectl apply -f redis/redis-deployment.yaml -f redis/redis-service.yaml
kubectl apply -f minio/minio-local.yaml
kubectl rollout status deploy/minio-local

docker build -f rest/Dockerfile.local -t demucs-rest:local rest/
docker build -f worker/Dockerfile.local -t demucs-worker:local worker/
minikube image load demucs-rest:local
minikube image load demucs-worker:local

kubectl apply -f rest/rest-local.yaml
kubectl apply -f worker/worker-local.yaml
kubectl apply -f logs/logs-local.yaml
kubectl rollout status deploy/rest-local
kubectl rollout status deploy/worker-local
kubectl port-forward svc/rest-local 5000:5000
```

In another terminal:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests
python local-test.py data/short-sample.mp3
```

Substitute an actual short MP3 path from your `data/` directory. The CLI uses `htdemucs` by default and downloads results to `local-output/`.

## Inspect
```bash
kubectl get pods
kubectl logs -f deploy/worker-local
kubectl logs -f deploy/rest-local
kubectl logs -f deploy/logs-local
kubectl port-forward svc/minio-local 9001:9001
```

Visit http://localhost:9001 (user `minioadmin`, password `minioadmin123`).

## Ingress (optional)
```bash
minikube addons enable ingress
minikube tunnel
# In another shell, if kubectl get ingress shows 127.0.0.1:
curl -H 'Host: demucs.local' http://127.0.0.1/health
```

## Reset
```bash
kubectl delete -f worker/worker-local.yaml -f rest/rest-local.yaml -f logs/logs-local.yaml -f minio/minio-local.yaml
minikube stop
```

#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-52zhimo-mir-indexer:latest}"
PLATFORM="${PLATFORM:-linux/amd64}"
OUT="${OUT:-dist/52zhimo-mir-indexer.tar.gz}"

mkdir -p "$(dirname "$OUT")"

if docker buildx version >/dev/null 2>&1; then
  docker buildx build --platform "$PLATFORM" -t "$IMAGE_NAME" --load .
else
  docker build -t "$IMAGE_NAME" .
fi

docker save "$IMAGE_NAME" | gzip > "$OUT"

echo "Image archive created: $OUT"
ls -lh "$OUT"

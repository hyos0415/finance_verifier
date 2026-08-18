#!/usr/bin/env bash
# Issue #3 — run the official vLLM OpenAI-compatible server in Docker (WSL2 + Docker Desktop).
#
# Usage (from Git Bash on Windows):
#   scripts/run_vllm_container.sh <model_name> [extra vllm args...]
#
# Example:
#   scripts/run_vllm_container.sh kakaocorp/kanana-2-3b-instruct --max-model-len 1024
#
# Notes (see issue #3 for the full writeup):
# - VLLM_WSL2_ENABLE_PIN_MEMORY=1 avoids "RuntimeError: UVA is not available" —
#   vLLM disables pinned memory by default under WSL2.
# - --enforce-eager trims CUDA graph / torch.compile VRAM overhead; on an 8GB
#   card under WSL2 the real usable budget is smaller than nvidia-smi implies
#   (observed ~6.9GB, not 8GB), so this headroom is usually needed.
# - The Hugging Face cache volume mount MUST use MSYS_NO_PATHCONV=1 with a
#   Windows-style host path. Without it, Git Bash mangles the -v path and the
#   mount silently fails (container re-downloads the model instead of hitting
#   cache, with no error).

set -euo pipefail

MODEL="${1:?usage: run_vllm_container.sh <model_name> [extra vllm args...]}"
shift

CONTAINER_NAME="vllm-server"
HF_CACHE_WIN_PATH="C:\\Users\\user\\.cache\\huggingface"

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

MSYS_NO_PATHCONV=1 docker run -d --name "$CONTAINER_NAME" --gpus all \
  -e VLLM_WSL2_ENABLE_PIN_MEMORY=1 \
  -v "${HF_CACHE_WIN_PATH}:/root/.cache/huggingface" \
  -p 8000:8000 \
  --ipc=host \
  vllm/vllm-openai:latest \
  --model "$MODEL" \
  --enforce-eager \
  --gpu-memory-utilization 0.85 \
  "$@"

echo "Started container '$CONTAINER_NAME' serving $MODEL on http://localhost:8000"
echo "Follow logs with: docker logs -f $CONTAINER_NAME"

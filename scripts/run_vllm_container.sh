#!/usr/bin/env bash
# Issue #3 — run the official vLLM OpenAI-compatible server in Docker (WSL2 + Docker Desktop).
#
# Usage (from Git Bash on Windows):
#   scripts/run_vllm_container.sh <model_name> [extra vllm args...]
#
# Example (override a default, e.g. go back to batch=1):
#   scripts/run_vllm_container.sh Intel/Qwen3.5-4B-int4-AutoRound --max-num-seqs 1
#
# Notes (see issue #3 for the full writeup, issue #25 for the tuning below):
# - VLLM_WSL2_ENABLE_PIN_MEMORY=1 avoids "RuntimeError: UVA is not available" —
#   vLLM disables pinned memory by default under WSL2.
# - Defaults below are #25's adopted CUDA-graph config for the Qwen candidate:
#   --enforce-eager is NOT passed (letting CUDA graphs capture), with
#   --max-model-len 1024 (workload's real context need, verified against the
#   full Test claim set with headroom to spare) and --max-num-seqs 4 (balances
#   throughput/latency/VRAM for the typical 1-2 claim per answer decomposition
#   -- see results/latency/capability_and_results.md). This gave a measured
#   ~3.6x decode throughput win over the old --enforce-eager baseline with no
#   change to the core FAR/UNSUPPORTED-Recall metrics.
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
  --gpu-memory-utilization 0.85 \
  --max-model-len 1024 \
  --max-num-seqs 4 \
  "$@"

echo "Started container '$CONTAINER_NAME' serving $MODEL on http://localhost:8000"
echo "Follow logs with: docker logs -f $CONTAINER_NAME"

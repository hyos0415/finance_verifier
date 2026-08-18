"""Issue #2 — minimum load/inference check for a verifier model candidate.

Usage:
    python scripts/check_model_load.py kakaocorp/kanana-2-3b-instruct
    python scripts/check_model_load.py Intel/Qwen3.5-4B-int4-AutoRound

Qwen3.5 defaults to a long English "thinking" trace before its answer, which
doesn't fit the Verifier's strict JSON output — pass enable_thinking=False
(applied unconditionally below; chat templates ignore unknown kwargs, so this
is a no-op for Kanana).
"""

import sys
import time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

PROMPT = "12개월 정기예금 기본금리가 3.0%인 상품에 대해 한 문장으로 설명해줘."


def main(model_name: str, max_new_tokens: int = 64) -> None:
    print(f"[load] {model_name}")
    t0 = time.time()

    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    print(f"[load] done in {time.time() - t0:.1f}s")

    messages = [{"role": "user", "content": PROMPT}]
    inputs = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
        enable_thinking=False,
    ).to(model.device)

    t0 = time.time()
    output = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    print(f"[generate] done in {time.time() - t0:.1f}s")

    print("[output]")
    print(tokenizer.decode(output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True))

    if torch.cuda.is_available():
        peak_gb = torch.cuda.max_memory_allocated() / (1024**3)
        print(f"[vram] peak_allocated={peak_gb:.2f}GB")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("usage: python scripts/check_model_load.py <model_name> [max_new_tokens]")
        sys.exit(1)
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) == 3 else 64)

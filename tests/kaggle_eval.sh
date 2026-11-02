#!/usr/bin/env bash
# Evaluate the agent against Qwen3-14B served by vLLM on a free Kaggle GPU. vLLM is the same kind of
# OpenAI-compatible server as the course endpoint: it honours chat_template_kwargs (thinking off), supports
# native tool calls, and serves the full 40,960-token window. No agent switches are needed.
#
# Kaggle notebook settings: Accelerator "GPU T4 x2", Internet ON (needs a phone-verified account).
# Upload dist/hw1_submission.zip as a Kaggle dataset (Kaggle extracts it), add it to the notebook, then run:
#
#   !bash /kaggle/input/*/hw1_submission/tests/kaggle_eval.sh quick     # or: full
#
# /kaggle/input is read-only, so the script first copies the submission to /kaggle/working and runs there.
#
# quick (~1-1.5 h): story_all.sh, held-out set, tests/test_live.py (CP1 + CP2)
# full  (~3-4 h):   quick + adversarial CP1/CP2 cases + CP3 (tally, wordstat, two debugging incidents)
# Results: /kaggle/working/eval_results.tgz (download it, or paste eval_summary.txt into the chat).
#
# Model: Qwen/Qwen3-14B-AWQ (official 4-bit AWQ). The full-precision model does not fit two T4s with a 40k
# window, so expect small quantization differences against the grader; the serving stack is the same.

set -u
MODE="${1:-quick}"
SUB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -w "$SUB" ]; then  # Kaggle datasets are read-only: run from a writable copy
    rm -rf /kaggle/working/hw1_submission
    cp -r "$SUB" /kaggle/working/hw1_submission
    chmod +x /kaggle/working/hw1_submission/run_agent.sh
    exec bash /kaggle/working/hw1_submission/tests/kaggle_eval.sh "$MODE"
fi
OUT=/kaggle/working/eval_results
mkdir -p "$OUT"
cd "$SUB" || exit 1
log() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$OUT/eval_summary.txt"; }

log "1/5 installing vLLM (about 5 minutes)"
pip install -q vllm > "$OUT/pip.log" 2>&1 || { tail -20 "$OUT/pip.log"; exit 1; }
# vLLM pulls in a newer torch; Kaggle's preinstalled torchaudio (built for the old CUDA) then
# breaks the transformers import. vLLM does not use torchaudio, so remove it.
pip uninstall -y -q torchaudio >> "$OUT/pip.log" 2>&1
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | tee -a "$OUT/eval_summary.txt"
python3 -c "import torch, vllm; print('torch', torch.__version__, 'cuda', torch.version.cuda, '| vllm', vllm.__version__, '| gpus usable:', torch.cuda.device_count() if torch.cuda.is_available() else 0)" 2>&1 \
    | tail -3 | tee -a "$OUT/eval_summary.txt"
python3 -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null \
    || { log "torch cannot use the GPUs (CUDA build newer than the driver?). Paste the lines above."; exit 1; }

log "2/5 starting vLLM with Qwen/Qwen3-14B-AWQ on 2 GPUs (model download + load: about 5-10 minutes)"
nohup python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen3-14B-AWQ --served-model-name Qwen/Qwen3-14B \
    --tensor-parallel-size 2 --dtype half --max-model-len 40960 --gpu-memory-utilization 0.92 \
    --enable-auto-tool-choice --tool-call-parser hermes --port 8000 > "$OUT/vllm.log" 2>&1 &
VLLM_PID=$!
for i in $(seq 1 180); do
    curl -s -m 2 localhost:8000/v1/models > /dev/null && break
    kill -0 "$VLLM_PID" 2>/dev/null || break   # server died: stop waiting
    sleep 10
done
curl -s -m 2 localhost:8000/v1/models > /dev/null || { log "vLLM did not start"; tail -40 "$OUT/vllm.log"; exit 1; }

export OPENAI_BASE_URL=http://localhost:8000/v1 MODEL=Qwen/Qwen3-14B QWEN_API_KEY=local LIVE_WORKERS=2

log "3/5 checking that thinking is off with the grader's request (chat_template_kwargs)"
python3 - <<'EOF' | tee -a "$OUT/eval_summary.txt"
import json, time, urllib.request
body = {"model": "Qwen/Qwen3-14B", "messages": [{"role": "user", "content": "Say only the word hello."}],
        "max_tokens": 64, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
t = time.time()
req = urllib.request.Request("http://localhost:8000/v1/chat/completions", json.dumps(body).encode(),
                             {"Content-Type": "application/json"})
d = json.load(urllib.request.urlopen(req, timeout=120))
m = d["choices"][0]["message"]
content = m.get("content") or ""
print("content=%r completion_tokens=%s %.1fs" % (content[:80], d["usage"]["completion_tokens"], time.time() - t))
print("THINKING OFF: OK" if "hello" in content.lower() and "<think>" not in content else "THINKING OFF: FAILED")
EOF

log "4/5 running suites (mode: $MODE)"
bash tests/story_all.sh > "$OUT/story.log" 2>&1;               log "story_all: $(tail -1 "$OUT/story.log")"
python3 tests/heldout.py > "$OUT/heldout.log" 2>&1;           log "held-out: $(tail -1 "$OUT/heldout.log")"
python3 tests/test_live.py cp1 cp2 > "$OUT/test_live.log" 2>&1; log "test_live: $(tail -1 "$OUT/test_live.log")"
if [ "$MODE" = "full" ]; then
    python3 tests/adversarial/runner.py --submission . --label kaggle --only cp1,cp2 --workers 2 --reruns 0 \
        > "$OUT/adversarial.log" 2>&1
    log "adversarial CP1/CP2: $(grep 'done:' "$OUT/adversarial.log")"
    python3 tests/adversarial/runner.py --submission . --label kaggle-cp3 --only PG-O,PG-R,PG-W,DB-1,DB-S31 \
        --workers 1 > "$OUT/cp3.log" 2>&1
    log "CP3: $(grep -E 'PG-|DB-' "$OUT/cp3.log" | grep -E 'PASS|FAIL' | tr '\n' ';')"
    cp tests/adversarial/results/kaggle*.json "$OUT/" 2>/dev/null
fi

log "5/5 packaging results"
grep -hE '^(PASS|FAIL|INFRA)' "$OUT/heldout.log" "$OUT/test_live.log" | grep -v '^PASS' >> "$OUT/eval_summary.txt"
tar -czf /kaggle/working/eval_results.tgz -C /kaggle/working eval_results
log "done: download /kaggle/working/eval_results.tgz, or paste eval_results/eval_summary.txt into the chat"
cat "$OUT/eval_summary.txt"

# -*- coding: utf-8 -*-
"""每轮 omni_init(重置 session) 验证每轮 TTS 都产出。"""
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))
from api.omni_client import OmniClient

SEGS = [f"/workspace/llama.cpp-omni/tools/omni/assets/my_test/segments/hsr_{i:02d}.wav" for i in range(6)]
OUT = "/workspace/llama.cpp-omni/tools/omni/output_tts_reset"

for r in range(2):
    c = OmniClient()
    t0=time.time()
    c.omni_init(use_tts=True, output_dir=OUT)
    init_t = time.time()-t0
    c.prefill("", cnt=0)
    for i,s in enumerate(SEGS):
        c.prefill(s, cnt=i+1)
    text, dt = c.decode(round_idx=0)
    print(f"round{r}: omni_init {init_t:.1f}s, decode {dt:.1f}s -> {text.strip()[:45]!r}")

print("done - check remote dirs")

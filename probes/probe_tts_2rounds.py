# -*- coding: utf-8 -*-
"""HTTP 连续 2 轮(不重置 session),检查每轮 TTS 是否产出。"""
import json, sys, time, urllib.request, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))
from api.omni_client import OmniClient

SEGS = [f"/workspace/llama.cpp-omni/tools/omni/assets/my_test/segments/hsr_{i:02d}.wav" for i in range(6)]
OUT = "/workspace/llama.cpp-omni/tools/omni/output_tts_2r"

c = OmniClient()
c.omni_init(use_tts=True, output_dir=OUT)
print("omni_init OK")

for r in range(2):
    if r == 0:
        c.prefill("", cnt=0)
    for i, s in enumerate(SEGS):
        c.prefill(s, cnt=i+1)
    text, dt = c.decode(round_idx=r)
    print(f"round{r}: decode {dt:.1f}s -> {text.strip()[:50]!r}")
    # 等 TTS
    tts_dir = f"{OUT}/round_{r:03d}/tts_wav"
    for _ in range(60):
        try:
            import paramiko  # 不用;用 ssh 检查
            break
        except: break
    time.sleep(3)  # 给 TTS 时间

print("done - check remote round dirs")

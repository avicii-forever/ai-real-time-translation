# -*- coding: utf-8 -*-
"""多轮累积对 decode 耗时的影响:每轮新 session vs 同 session 连续多轮。"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))
from api.omni_client import OmniClient

SEGS = [f"/workspace/llama.cpp-omni/tools/omni/assets/my_test/segments/hsr_{i:02d}.wav" for i in range(6)]

# 场景 A:每轮全新 session(omni_init)
print("== 场景A:每轮 omni_init(全新 session)==")
for r in range(3):
    c = OmniClient()
    c.omni_init(output_dir="/workspace/llama.cpp-omni/tools/omni/output_ra")
    c.prefill("", cnt=0)
    for i, s in enumerate(SEGS):
        c.prefill(s, cnt=i+1)
    t0 = time.time()
    text, dt = c.decode(round_idx=0)
    print(f"  round{r+1}: decode {dt*1000:.0f}ms, tokens≈{len(text.split())} -> {text.strip()[:50]}")

# 场景 B:同一个 session 连续 3 轮
print("== 场景B:同一 session 连续 3 轮(不重建)==")
c = OmniClient()
c.omni_init(output_dir="/workspace/llama.cpp-omni/tools/omni/output_rb")
for r in range(3):
    c.prefill("", cnt=0)
    for i, s in enumerate(SEGS):
        c.prefill(s, cnt=i+1)
    t0 = time.time()
    text, dt = c.decode(round_idx=r)
    print(f"  round{r+1}: decode {dt*1000:.0f}ms, tokens≈{len(text.split())} -> {text.strip()[:50]}")

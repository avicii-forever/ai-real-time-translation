# -*- coding: utf-8 -*-
"""HTTP + use_tts=true 验证:中文音频 -> 英文译文文本 + 英文 TTS 语音落盘。"""
import json, sys, time, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client")) if False else None
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))
from api.omni_client import OmniClient

SEGS = [f"/workspace/llama.cpp-omni/tools/omni/assets/my_test/segments/hsr_{i:02d}.wav" for i in range(6)]
OUT = "/workspace/llama.cpp-omni/tools/omni/output_tts_test"

c = OmniClient()
t0=time.time()
c.omni_init(media_type=1, use_tts=True, output_dir=OUT)
print(f"omni_init(use_tts=true) OK ({time.time()-t0:.1f}s)")
c.prefill("", cnt=0)
print("cnt=0 OK")
for i,s in enumerate(SEGS):
    c.prefill(s, cnt=i+1)
print("6 prefill OK")

t0=time.time()
text, dt = c.decode(round_idx=0)
print(f"decode {dt:.1f}s -> {text.strip()[:80]!r}")

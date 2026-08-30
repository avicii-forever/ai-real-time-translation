# -*- coding: utf-8 -*-
"""每轮 omni_init + 清理 output_dir + decode round_idx=-1:验证第二轮 TTS。"""
import os, sys, time, shutil, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))
from api.omni_client import OmniClient

SEGS = [f"/workspace/llama.cpp-omni/tools/omni/assets/my_test/segments/hsr_{i:02d}.wav" for i in range(6)]
OUT = "/workspace/llama.cpp-omni/tools/omni/output_tts_clean"

for r in range(2):
    # 清理远端输出目录
    subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "<SSH别名>",
                    f"rm -rf {OUT}; mkdir -p {OUT}"], capture_output=True, timeout=30)
    c = OmniClient()
    c.omni_init(use_tts=True, output_dir=OUT)
    c.prefill("", cnt=0)
    for i, s in enumerate(SEGS):
        c.prefill(s, cnt=i+1)
    text, dt = c.decode(round_idx=-1)
    print(f"round{r}: decode {dt:.1f}s -> {text.strip()[:45]!r}")
    time.sleep(4)

print("done")

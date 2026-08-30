# -*- coding: utf-8 -*-
"""精确验证:每次 omni_init 清理目录,每次 round_000 应有新 TTS。"""
import os, sys, time, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))
from api.omni_client import OmniClient

SEGS = [f"/workspace/llama.cpp-omni/tools/omni/assets/my_test/segments/hsr_{i:02d}.wav" for i in range(6)]
OUT = "/workspace/llama.cpp-omni/tools/omni/output_tts_verify"

for r in range(2):
    subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "<SSH别名>",
                    f"rm -rf {OUT} && mkdir -p {OUT}"], capture_output=True, timeout=30)
    c = OmniClient()
    c.omni_init(use_tts=True, output_dir=OUT)
    c.prefill("", cnt=0)
    for i, s in enumerate(SEGS):
        c.prefill(s, cnt=i+1)
    text, dt = c.decode(round_idx=-1)
    time.sleep(5)  # 等 TTS 落盘
    # 检查 round_000 的 wav 数量
    chk = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "<SSH别名>",
                          f"ls {OUT}/round_000/tts_wav/wav_*.wav 2>/dev/null | wc -l; ls {OUT}/round_000/tts_wav/generation_done.flag 2>/dev/null"],
                         capture_output=True, timeout=30)
    print(f"session{r}: decode {dt:.1f}s, round_000 TTS: {chk.stdout.decode().strip().replace(chr(10),' wav, flag=')} -> {text.strip()[:40]!r}")

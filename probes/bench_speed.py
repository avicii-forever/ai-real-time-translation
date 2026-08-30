# -*- coding: utf-8 -*-
"""翻译速度基准测试。

测量:omni_init / prefill / decode / 端到端延迟 / RTF(实时性)。

用法:
    python bench_speed.py                  # 用远端 6 段高铁票切片(8.2s 音频)
    python bench_speed.py --rounds 3       # 跑 3 轮取平均
"""
import argparse
import json
import os
import sys
import time
import urllib.request

# 让脚本能 import client/ 下的模块(无论从哪运行)
_CLIENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client")
sys.path.insert(0, _CLIENT_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.omni_client import OmniClient

# 远端 6 段高铁票切片(8.2s 中文)
SEGMENTS = [
    "/workspace/llama.cpp-omni/tools/omni/assets/my_test/segments/hsr_%02d.wav" % i
    for i in range(6)
]
AUDIO_SECONDS = 8.2  # 6 段 × 1.5s ≈ 8.2s 有效语音


def fmt(ms):
    return f"{ms:8.1f} ms"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--segments", type=str, nargs="*", default=SEGMENTS)
    args = ap.parse_args()

    c = OmniClient()
    print("== 翻译速度基准 ==")
    print(f"  音频: {len(args.segments)} 段切片 ≈ {AUDIO_SECONDS:.1f}s 语音")
    print(f"  轮次: {args.rounds}")
    print()

    times = {"omni_init": [], "prefill_total": [], "prefill_per": [],
             "decode": [], "end2end": []}
    samples = []

    for r in range(args.rounds):
        t0 = time.time()
        c.omni_init(output_dir="/workspace/llama.cpp-omni/tools/omni/output_bench")
        t_omni = (time.time() - t0) * 1000
        times["omni_init"].append(t_omni)

        # prefill cnt=0 (系统初始化)
        c.prefill("", cnt=0)

        # 各块 prefill
        t_prefill0 = time.time()
        per = []
        for i, seg in enumerate(args.segments):
            t1 = time.time()
            c.prefill(seg, cnt=i + 1)
            per.append((time.time() - t1) * 1000)
        t_prefill = (time.time() - t_prefill0) * 1000

        # decode
        t2 = time.time()
        text, dt = c.decode(round_idx=r)
        t_decode = dt * 1000

        # 端到端 = prefill + decode(不含 omni_init)
        t_e2e = t_prefill + t_decode

        times["prefill_total"].append(t_prefill)
        times["prefill_per"].extend(per)
        times["decode"].append(t_decode)
        times["end2end"].append(t_e2e)
        samples.append(text.strip())

        print(f"--- 轮次 {r+1} ---")
        print(f"  omni_init : {fmt(t_omni)}")
        print(f"  prefill   : {fmt(t_prefill)} (共{len(per)}块, 均值 {sum(per)/len(per):.1f}ms/块)")
        print(f"  decode    : {fmt(t_decode)}")
        print(f"  端到端    : {fmt(t_e2e)} (音频 {AUDIO_SECONDS:.1f}s)")
        print(f"  RTF       : {t_e2e/1000/AUDIO_SECONDS:.2f}x (音频时长的比例, <1 表示快于实时)")
        print(f"  译文      : {text.strip()[:70] if text.strip() else '(空)'}")
        print()

    # 汇总
    def avg(x):
        return sum(x) / len(x)

    print("== 汇总(平均值) ==")
    print(f"  omni_init : {avg(times['omni_init']):8.1f} ms")
    print(f"  prefill   : {avg(times['prefill_total']):8.1f} ms (均值 {avg(times['prefill_per']):.1f} ms/块)")
    print(f"  decode    : {avg(times['decode']):8.1f} ms")
    print(f"  端到端    : {avg(times['end2end']):8.1f} ms")
    print(f"  RTF       : {avg(times['end2end'])/1000/AUDIO_SECONDS:.2f}x")
    print()
    print("== 译文样本 ==")
    for i, s in enumerate(samples[:2]):
        print(f"  [{i+1}] {s[:100]}")


if __name__ == "__main__":
    main()

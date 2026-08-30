# -*- coding: utf-8 -*-
"""测 listen_prob_scale 对翻译质量的影响。

背景:当前 hard-listen(listen_prob_scale=0.01)让模型永远在 SPEAK、停不下来,
退化后 token 增速从 ~18/s 飙到 ~53/s,全是口水词。假设:放宽 listen 让模型
能自然收 turn,把 token 增速压回正常。

用法:
    cd client && python ../probes/probe_listen_scale.py --scale 0.5
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))

import config as cfg
from audio.file_source import read_wav_mono
from api.ws_duplex_client import WSDuplexClient
from config import SAMPLE_RATE, SLICE_SECONDS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="../audio_test/cs336/seg_000500.wav")
    ap.add_argument("--secs", type=float, default=30)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--src", default="English")
    ap.add_argument("--tgt", default="中文")
    args = ap.parse_args()

    vc, _ = cfg.make_prompts(args.src, args.tgt)
    data = read_wav_mono(args.wav)
    flen = int(SAMPLE_RATE * SLICE_SECONDS)
    n = min(int(args.secs / SLICE_SECONDS), len(data) // flen)

    texts = []
    t_stream0 = [None]

    def on_text(t):
        texts.append((time.time() - (t_stream0[0] or time.time()), t))

    print(f"listen_prob_scale={args.scale} 连接中…")
    ws = WSDuplexClient(system_prompt=vc, listen_prob_scale=args.scale)
    ws._on_text = on_text
    ws.connect()
    print(f"session.init {args.scale}: 完成,推 {n} 帧…")

    t0 = time.time()
    t_stream0[0] = t0
    for i in range(n):
        due = t0 + (i + 1) * SLICE_SECONDS
        if due > time.time():
            time.sleep(due - time.time())
        ws.push_audio(data[i * flen:(i + 1) * flen].tolist())

    ws.reset_turn()
    ws.push_silence(0.8)
    time.sleep(args.secs + 10)   # 留时间把最后一句吐完

    full = "".join(t for _, t in texts)
    dur = time.time() - t0
    # 粗略 token 增速:中文按 1.5 字/中文 token 估
    print(f"\n=== scale={args.scale} 结果 ===")
    print(f"delta 数 {len(texts)},总字符 {len(full)},墙钟 {dur:.0f}s"
          f"(音频 {args.secs}s)")
    print(f"产字率 {len(full)/args.secs:.1f} 字符/秒音频")
    print(f"译文: {full}")
    ws.close()


if __name__ == "__main__":
    main()

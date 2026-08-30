# -*- coding: utf-8 -*-
"""验证 duplex 模式下 use_tts=true 到底出不出语音。

这是"实时中文配音"的前提问题。已知风险:
  - duplex 之前一直用 use_tts=false,能不能开没验证过
  - 记录在案的多轮 TTS 不产出 bug(不过那是 turn_based/HTTP 路径)
  - 切 use_tts 会让后端无法复用 shared_octx,等于一次冷加载(~80s+)

产出:
  - 每个 audio delta 的到达时刻/样本数
  - 合成语音落成 wav,可直接听
  - 文本与音频的时间错位(配音要用来对齐)

用法:
    cd client && python ../probes/probe_duplex_tts.py --wav ../audio_test/cs336/seg_000500.wav --secs 30
"""
import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))

import config as cfg
from audio.file_source import read_wav_mono
from api.ws_duplex_client import WSDuplexClient
from config import SAMPLE_RATE, SLICE_SECONDS

TTS_SR = 24000   # MiniCPM-o token2wav 输出;事件里不带采样率,按 24k 存


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--secs", type=float, default=30)
    ap.add_argument("--slice", type=float, default=SLICE_SECONDS)
    ap.add_argument("--src", default="English")
    ap.add_argument("--tgt", default="中文")
    ap.add_argument("--tail", type=float, default=40)
    ap.add_argument("--out", default="../audio_test/cs336/dub_probe.wav")
    args = ap.parse_args()

    vc, _ = cfg.make_prompts(args.src, args.tgt)
    data = read_wav_mono(args.wav)
    flen = int(SAMPLE_RATE * args.slice)
    n = min(int(args.secs / args.slice), len(data) // flen)

    texts, chunks = [], []      # (t, 文本) / (t, pcm)
    t_stream0 = [None]

    def now():
        return time.time() - (t_stream0[0] or time.time())

    def on_text(t):
        texts.append((now(), t))

    def on_audio(pcm):
        chunks.append((now(), pcm))
        total = sum(c.size for _, c in chunks)
        print(f"  [{now():6.1f}s] audio +{pcm.size:6d} 采样 "
              f"({pcm.size/TTS_SR:4.2f}s) 累计 {total/TTS_SR:5.2f}s")

    print("连接中(use_tts=True 会强制冷加载,可能要 2-3 分钟)…")
    tc = time.time()
    ws = WSDuplexClient(system_prompt=vc, use_tts=True)
    ws._on_text = on_text
    ws._on_audio = on_audio
    ws.connect()
    print(f"session.init 耗时 {time.time()-tc:.1f}s\n"
          f"推 {n} 帧 / {n*args.slice:.1f}s 音频…\n")

    t0 = time.time()
    t_stream0[0] = t0
    for i in range(n):
        due = t0 + (i + 1) * args.slice
        if due > time.time():
            time.sleep(due - time.time())
        ws.push_audio(data[i * flen:(i + 1) * flen].tolist())

    print(f"\n推完,等 {args.tail}s 排空…")
    ws.reset_turn()
    ws.push_silence(0.8)
    time.sleep(args.tail)

    print("\n--- 结果 ---")
    print(f"文本片段 {len(texts)} 个,音频片段 {len(chunks)} 个")
    full_text = "".join(t for _, t in texts)
    print(f"译文: {full_text}")

    if not chunks:
        print("\n❌ duplex + use_tts=true 没有产出任何音频事件。")
        print("   -> 后端 duplex 路径不走 TTS,配音得换方案。")
        ws.close()
        return 1

    pcm = np.concatenate([c for _, c in chunks])
    dur = pcm.size / TTS_SR
    print(f"\n✅ 收到语音:{pcm.size} 采样 = {dur:.2f}s @ {TTS_SR}Hz")
    print(f"   首个音频包 @ {chunks[0][0]:.1f}s,最后一个 @ {chunks[-1][0]:.1f}s")
    if texts:
        print(f"   首个文本   @ {texts[0][0]:.1f}s  (音频比文本晚 "
              f"{chunks[0][0]-texts[0][0]:.1f}s)")
    print(f"   峰值 {np.abs(pcm).max():.3f} RMS {float(np.sqrt((pcm**2).mean())):.4f}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TTS_SR)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"\n配音已存 {out}(可直接播放核对)")
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""量一下 duplex 的实时性:每帧 push 耗时 + 首个 text_delta 延迟 + 是否跟得上实时。

用法:
    cd client && python ../probes/bench_duplex.py --wav ../audio_test/cs336/seg_000500.wav --frames 12
"""
import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))

import config as cfg
from audio.file_source import read_wav_mono
from api.ws_duplex_client import WSDuplexClient
from config import SAMPLE_RATE, SLICE_SECONDS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--src", default="English")
    ap.add_argument("--tgt", default="中文")
    ap.add_argument("--slice", type=float, default=SLICE_SECONDS)
    ap.add_argument("--drain-idle", type=float, default=20,
                    help="多久没有新 delta 就认为吐完了")
    ap.add_argument("--drain-max", type=float, default=180,
                    help="排空阶段最长等多久")
    ap.add_argument("--pace", action="store_true",
                    help="按真实播放速度推帧(默认全速灌,测服务端吞吐上限)")
    args = ap.parse_args()

    vc, _ = cfg.make_prompts(args.src, args.tgt)
    data = read_wav_mono(args.wav)
    flen = int(SAMPLE_RATE * args.slice)
    n = min(args.frames, len(data) // flen)
    print(f"音频 {len(data)/SAMPLE_RATE:.1f}s -> 推 {n} 帧 x {args.slice}s "
          f"(音频总时长 {n*args.slice:.1f}s)")

    deltas = []           # (相对时刻, 文本)
    t_connect0 = time.time()
    ws = WSDuplexClient(system_prompt=vc)
    ws._on_text = lambda t: deltas.append((time.time(), t))
    ws.connect()
    print(f"session.init 耗时 {time.time()-t_connect0:.1f}s")

    t0 = time.time()
    push_times = []
    for i in range(n):
        frame = data[i * flen:(i + 1) * flen]
        if args.pace:
            due = t0 + (i + 1) * args.slice
            if due > time.time():
                time.sleep(due - time.time())
        a = time.time()
        ws.push_audio(frame.tolist())
        b = time.time()
        push_times.append(b - a)
        first = deltas[0][0] - t0 if deltas else None
        print(f"  帧{i:2d} push={b-a:6.2f}s  已收 delta={len(deltas):3d}"
              f"  累计墙钟={b-t0:6.2f}s (音频{(i+1)*args.slice:5.1f}s)"
              + (f"  首delta@{first:.2f}s" if first else ""))

    # push 不阻塞(帧只是塞进 socket),真正的瓶颈是服务端解码速度。
    # 所以推完后要**持续排空**:看 delta 以多快的速度吐出来,直到静默 drain_idle 秒。
    ws.reset_turn()
    ws.push_silence(0.8)
    print(f"\n(推完,开始排空;静默 {args.drain_idle}s 或超过 {args.drain_max}s 就停)")
    last_n = 0
    last_change = time.time()
    drain0 = time.time()
    while time.time() - drain0 < args.drain_max:
        time.sleep(0.5)
        if len(deltas) != last_n:
            new = "".join(t for _, t in deltas[last_n:])
            print(f"  [{time.time()-t0:6.1f}s] +{len(deltas)-last_n:2d} delta  {new}")
            last_n = len(deltas)
            last_change = time.time()
        elif time.time() - last_change > args.drain_idle:
            break

    total = time.time() - t0
    audio_s = n * args.slice

    print(f"\n--- 汇总 ---")
    print(f"音频时长      {audio_s:.1f}s")
    print(f"墙钟总耗时    {total:.1f}s")
    print(f"实时率 RTF    {total/audio_s:.2f}x  ({'跟得上' if total<=audio_s else '跟不上'})")
    if deltas:
        print(f"首 delta 延迟 {deltas[0][0]-t0:.1f}s")
        print(f"delta 总数    {len(deltas)}")
    print(f"push 耗时     max={max(push_times):.2f}s avg={sum(push_times)/len(push_times):.2f}s")
    print(f"\n译文: {''.join(t for _, t in deltas)}")
    ws.close()


if __name__ == "__main__":
    main()

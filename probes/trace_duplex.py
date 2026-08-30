# -*- coding: utf-8 -*-
"""单会话全量事件追踪 —— 一次 session.init 榨出最多信息。

背景:后端是单 session,且 session 拆除要 60-90s,新 init 得排队,
所以每次连接都很贵(实测 24-184s)。这个脚本一次连接跑完整实验:
按真实播放速度推 N 秒音频,记录**每一个后端事件**的时刻和类型,
用来回答:模型是不是吐一段就 EOS 停住了?后续音频还会不会触发生成?

用法:
    cd client && python ../probes/trace_duplex.py --wav ../audio_test/cs336/seg_000500.wav --secs 60
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))

import config as cfg
from audio.file_source import read_wav_mono
from api.ws_duplex_client import WSDuplexClient
from config import SAMPLE_RATE, SLICE_SECONDS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--secs", type=float, default=60)
    ap.add_argument("--slice", type=float, default=SLICE_SECONDS)
    ap.add_argument("--src", default="English")
    ap.add_argument("--tgt", default="中文")
    ap.add_argument("--tail", type=float, default=25, help="推完后再等多久")
    ap.add_argument("--out", default="../audio_test/cs336/trace.jsonl")
    args = ap.parse_args()

    vc, _ = cfg.make_prompts(args.src, args.tgt)
    data = read_wav_mono(args.wav)
    flen = int(SAMPLE_RATE * args.slice)
    n = min(int(args.secs / args.slice), len(data) // flen)

    events = []          # (t, type, kind, textlen, raw)
    t_stream0 = [None]

    def on_event(ev):
        now = time.time()
        base = t_stream0[0] or now
        events.append({
            "t": round(now - base, 3),
            "type": ev.get("type"),
            "kind": ev.get("kind"),
            "text": ev.get("text", "")[:80],
        })

    print(f"连接中(冷启动可能要 1-3 分钟)…")
    tc = time.time()
    ws = WSDuplexClient(system_prompt=vc)
    ws._on_event = on_event
    ws.connect()
    print(f"session.init 耗时 {time.time()-tc:.1f}s;开始按播放速度推 "
          f"{n} 帧 / {n*args.slice:.1f}s 音频\n")

    t0 = time.time()
    t_stream0[0] = t0
    for i in range(n):
        due = t0 + (i + 1) * args.slice
        if due > time.time():
            time.sleep(due - time.time())
        try:
            ws.push_audio(data[i * flen:(i + 1) * flen].tolist())
        except Exception as e:
            print(f"  帧{i} 推送失败: {e}")
            break
        if i % 4 == 0:
            types = Counter(e["type"] for e in events)
            print(f"  [{time.time()-t0:6.1f}s] 已推 {i+1:3d} 帧 "
                  f"({(i+1)*args.slice:5.1f}s 音频) 事件: {dict(types)}")

    print(f"\n推完,再等 {args.tail}s 看有没有后续事件…")
    time.sleep(args.tail)

    print(f"\n--- 事件时间线 ---")
    for e in events:
        txt = f"  {e['text']!r}" if e["text"] else ""
        print(f"  [{e['t']:7.2f}s] {e['type']:<26} kind={e['kind']}{txt}")

    print(f"\n--- 统计 ---")
    print(f"事件总数 {len(events)}: {dict(Counter(e['type'] for e in events))}")
    text = "".join(e["text"] for e in events
                   if e["type"] == "response.output.delta" and e["kind"] == "text")
    print(f"最后一个事件在 {events[-1]['t']:.1f}s(音频推到 {n*args.slice:.1f}s)"
          if events else "无事件")
    print(f"\n译文全文:\n{text}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
        encoding="utf-8")
    print(f"\n事件已存 {args.out}")
    ws.close()


if __name__ == "__main__":
    main()

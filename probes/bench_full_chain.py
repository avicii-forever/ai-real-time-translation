# -*- coding: utf-8 -*-
"""完整客户端链路延迟基准:loopback 采集 -> VAD -> 切片 -> 上传 -> prefill -> decode。

测量:
- 采集端到端:VAD 检测语句结束 -> 译文显示
- 各阶段分解:切片/SFTP上传/prefill/decode
- 对比:音频实际时长 vs 总处理耗时

用法:
    python bench_full_chain.py          # 等 VAD 自动切句(需本机播放中文)
    python bench_full_chain.py --once 60  # 最多采 60s
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))

from pipeline import Pipeline
from audio.capture import LoopbackCapture
from config import SAMPLE_RATE, SLICE_SECONDS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", type=float, default=0, help="采 N 秒后自动停止")
    args = ap.parse_args()

    print("== 完整链路延迟基准 ==")
    print(f"  输入源: 系统声音(loopback) @ {SAMPLE_RATE}Hz,切片 {SLICE_SECONDS}s")

    stats = {"rounds": 0, "utterance_s": 0, "slice_n": 0,
             "prefill_ms": 0, "decode_ms": 0, "total_ms": 0, "rtf": 0}
    results = []

    def on_log(s):
        print(f"    {s}")

    def on_status(s):
        if s in ("语句结束,翻译中",):
            print(f"  [状态] {s}")

    def on_translation(t):
        print(f"  [译文] {t[:80]}")

    # 用插桩的 pipeline 计时
    from pipeline import Pipeline as P
    import pipeline as pl

    _orig_translate_batch = pl.Pipeline._translate_batch

    def timed_translate(self, chunks, batch_no=0):
        t0 = time.time()
        _orig_translate_batch(self, chunks, batch_no)
        dt = (time.time() - t0) * 1000
        audio_s = sum(sec for _, sec in chunks)
        stats["rounds"] += 1
        stats["utterance_s"] += audio_s
        stats["slice_n"] += len(chunks)
        stats["total_ms"] += dt
        stats["rtf"] = stats["total_ms"] / 1000 / stats["utterance_s"] if stats["utterance_s"] else 0
        print(f"  [本段] 切{len(chunks)}块/{audio_s:.1f}s 处理{dt:.0f}ms RTF={dt/1000/audio_s:.2f}x")

    pl.Pipeline._translate_batch = timed_translate

    pipe = Pipeline(
        source=LoopbackCapture(),
        on_status=on_status,
        on_log=on_log,
        on_translation=on_translation,
    )
    print(">> 开始采集(请播放中文音频,如高铁票句)...")
    pipe.start()

    if args.once > 0:
        time.sleep(args.once)
        pipe.stop()
    else:
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pipe.stop()

    print()
    print("== 汇总 ==")
    if stats["rounds"] == 0:
        print("  (未检测到语音)")
    else:
        print(f"  轮次      : {stats['rounds']}")
        print(f"  音频总时长: {stats['utterance_s']:.1f}s ({stats['slice_n']} 块)")
        print(f"  总处理    : {stats['total_ms']:.0f} ms")
        print(f"  平均 RTF  : {stats['rtf']:.2f}x")
        print(f"  平均延迟  : {stats['total_ms']/stats['rounds']:.0f} ms/句 (音频均长 {stats['utterance_s']/stats['rounds']:.1f}s)")


if __name__ == "__main__":
    main()

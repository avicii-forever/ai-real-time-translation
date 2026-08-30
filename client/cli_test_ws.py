# -*- coding: utf-8 -*-
"""WS duplex 流式翻译 CLI 测试:播放中文,边说边出译文。

用法:
    python cli_test_ws.py --once 60   # 采 60s
"""
import argparse
import sys
import time

sys.path.insert(0, ".")

from pipeline_ws import WSPipeline
from audio.capture import LoopbackCapture, MicCapture
from config import SAMPLE_RATE, SLICE_SECONDS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mic", action="store_true")
    ap.add_argument("--once", type=float, default=0)
    args = ap.parse_args()

    src = MicCapture() if args.mic else LoopbackCapture()
    print(f"== WS duplex 流式翻译 ==  输入: {src.name}", flush=True)

    pipe = WSPipeline(
        source=src,
        on_status=lambda s: print(f"[状态] {s}", flush=True),
        on_log=lambda s: print(f"  {s}", flush=True),
        on_translation=lambda t: print(f"  [译文] {t}", flush=True),
    )
    print(">> 开始采集(播放中文音频)...", flush=True)
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
    print("done")


if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""P1 核心链路 CLI 测试:采集一段本地播放的音频 -> 自动翻译 -> 打印译文。

用法:
    python cli_test.py                  # 监听系统声音(loopback),播一段中文音频即可
    python cli_test.py --mic            # 改用麦克风
    python cli_test.py --once <秒>       # 只采集 N 秒后停止(无需 VAD 等待)
"""
import argparse
import sys
import threading
import time

sys.path.insert(0, ".")  # 允许直接 python cli_test.py 运行

from pipeline import Pipeline
from audio.capture import LoopbackCapture, MicCapture
from api.omni_client import OmniError
from config import SAMPLE_RATE, SLICE_SECONDS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mic", action="store_true", help="用麦克风")
    ap.add_argument("--once", type=float, default=0, help="采集 N 秒后停止(0=等 VAD)")
    args = ap.parse_args()

    source = MicCapture() if args.mic else LoopbackCapture()
    print(f"== 输入源: {source.name} ==")
    print(f"== 采样率 {SAMPLE_RATE}Hz, 切片 {SLICE_SECONDS}s ==")

    results = []

    def on_translation(t):
        print("\n--- 译文 ---")
        print(t)
        print("-----------")
        results.append(t)

    pipe = Pipeline(
        source=source,
        on_status=lambda s: print(f"[status] {s}"),
        on_log=lambda s: print(f"  {s}"),
        on_error=lambda e: print(f"[error] {e}"),
        on_translation=on_translation,
    )

    print(">> 开始监听(播放一段中文音频...)")
    pipe.start()

    if args.once > 0:
        time.sleep(args.once)
        print(f">> {args.once}s 已到,停止")
        pipe.stop()
    else:
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pipe.stop()

    print("== done ==")
    sys.exit(0 if results else 1)


if __name__ == "__main__":
    main()

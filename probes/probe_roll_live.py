# -*- coding: utf-8 -*-
"""离线验证 WSPipeline 的滚动 session(不需要真后端)。

拉起 probes/mock_ws_duplex.py 当假后端,用文件音源按真实速度喂 WSPipeline,
把 max_session_seconds 压到很小,看:
  1. 会话数随时间增长(确实在回收重开)
  2. 回收只发生在 VAD 静音处 —— 即回收时不能有正在进行的语句
  3. 回收后译文继续滚(管线没死)

用法:
    set PYTHONIOENCODING=utf-8
    python probes/probe_roll_live.py --seconds 70 --roll 20
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

DEFAULT_WAV = ROOT / "audio_test" / "cs336" / "seg_1200s_300s.wav"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default=str(DEFAULT_WAV))
    ap.add_argument("--seconds", type=float, default=70.0, help="跑多久")
    ap.add_argument("--roll", type=float, default=20.0, help="max_session_seconds")
    ap.add_argument("--port", type=int, default=28099)
    args = ap.parse_args()

    mock = subprocess.Popen(
        [sys.executable, str(ROOT / "probes" / "mock_ws_duplex.py"),
         "--port", str(args.port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace")
    time.sleep(1.5)
    if mock.poll() is not None:
        print("[probe] mock 起不来:\n" + (mock.stdout.read() or ""))
        return 1

    try:
        from audio.file_source import FileCapture
        from pipeline_ws import WSPipeline

        events = []          # (t, kind, detail)
        t0 = time.time()

        def log(s):
            events.append((time.time() - t0, "log", s))
            print(f"[{time.time() - t0:6.1f}s] {s}")

        def status(s):
            events.append((time.time() - t0, "status", s))

        last_text = {"v": ""}

        def trans(t):
            last_text["v"] = t

        src = FileCapture(args.wav, duration=args.seconds + 10, realtime=True)
        pipe = WSPipeline(source=src, on_log=log, on_status=status,
                          on_translation=trans,
                          on_error=lambda e: log(f"[错误] {e}"),
                          max_session_seconds=args.roll)
        pipe.start()
        time.sleep(args.seconds)
        pipe.stop()

        rolls = [e for e in events if "滚动回收" in e[2]]
        print("\n==== 结果 ====")
        print(f"跑了 {args.seconds:.0f}s,roll={args.roll:.0f}s")
        print(f"建立会话数 : {pipe.sessions}(1 个初始 + {pipe.sessions - 1} 次回收)")
        print(f"回收次数   : {len(rolls)} @ " +
              ", ".join(f"{t:.0f}s" for t, _, _ in rolls))
        print(f"末尾译文   : {last_text['v'][:60]!r}")

        expect = int(args.seconds // args.roll)
        ok = len(rolls) >= expect - 1 and pipe.sessions == len(rolls) + 1
        # 回收前一条日志必须是"本句完成"或空闲,不能是"推帧"(那说明句子被切断)
        cut = []
        for i, e in enumerate(events):
            if "滚动回收" in e[2]:
                prev = [p for p in events[:i] if p[1] == "log"]
                if prev and "推帧" in prev[-1][2]:
                    cut.append(e[0])
        if cut:
            ok = False
            print(f"❌ 有 {len(cut)} 次回收发生在语句中途 @ {cut}")
        else:
            print("✅ 所有回收都落在静音边界(没切断句子)")
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=5)
        except Exception:
            mock.kill()


if __name__ == "__main__":
    sys.exit(main())

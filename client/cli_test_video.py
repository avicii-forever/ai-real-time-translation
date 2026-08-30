# -*- coding: utf-8 -*-
"""视频 → 流式中文字幕 端到端测试(CLI,无 GUI、无声卡)。

把视频音轨按真实播放速度喂进 WS duplex 管线,打印逐帧滚动的中文字幕,
并落成 .srt / .txt,便于人工核对翻译质量与延迟。

两种切句模式:
  --mode continuous  (默认) 音频背靠背连推,字幕在文本层按标点断 —— 讲座类首选
  --mode vad         沿用 pipeline_ws 的能量 VAD 切句 —— 对话类/有停顿的音源

用法:
    cd client
    python cli_test_video.py --media "..\\cs336.mp4" --ss 300 --t 60
    python cli_test_video.py --wav ..\\audio_test\\cs336\\seg_000500.wav
    python cli_test_video.py --wav x.wav --fast       # 不按播放速度,全速跑
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config as cfg
from audio.file_source import FileCapture


def srt_ts(sec):
    h, rem = divmod(max(0.0, sec), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


class SubtitleLog:
    """收字幕 + 原地滚动打印。"""

    def __init__(self, t0, offset=0.0):
        self.t0 = t0
        self.offset = offset      # 视频内的起始秒(写进 srt 时间轴)
        self.lines = []           # (媒体时间, 文本)
        self._last_len = 0
        self._first_at = None
        # session.init(冷启动要几十秒到几分钟)不算进字幕延迟,
        # 从"开始推流"起算才是用户真正感知到的延迟。
        self._stream_t0 = None

    def mark_stream_start(self):
        self._stream_t0 = time.time()

    def _now(self):
        return time.time() - (self._stream_t0 or self.t0)

    def partial(self, text):
        if not text:
            return
        if self._first_at is None:
            self._first_at = self._now()
        pad = " " * max(0, self._last_len - len(text))
        sys.stdout.write(f"\r  … [{self._now():6.1f}s] {text}{pad}")
        sys.stdout.flush()
        self._last_len = len(text)

    def subtitle(self, text):
        text = text.strip()
        if not text:
            return
        now = self._now()
        if self._first_at is None:
            self._first_at = now
        self.lines.append((now, text))
        sys.stdout.write("\r" + " " * (self._last_len + 20) + "\r")
        print(f"[{now:6.1f}s] {text}")
        self._last_len = 0

    @property
    def first_latency(self):
        return self._first_at

    def to_srt(self):
        out = []
        for i, (t, txt) in enumerate(self.lines, 1):
            nxt = self.lines[i][0] if i < len(self.lines) else t + 3.0
            a = self.offset + max(0.0, t - 2.0)   # 译文滞后于原声,回拨 2s 近似
            b = self.offset + max(a - self.offset + 1.0, nxt - 2.0)
            out.append(f"{i}\n{srt_ts(a)} --> {srt_ts(b)}\n{txt}\n")
        return "\n".join(out)


def build_vad_pipeline(src, log):
    """VAD 模式:复用 pipeline_ws,把"整句译文"当一条字幕。"""
    from pipeline_ws import WSPipeline

    state = {"cur": ""}

    def on_translation(t):
        if not t:
            if state["cur"]:
                log.subtitle(state["cur"])
            state["cur"] = ""
            return
        state["cur"] = t
        log.partial(t)

    pipe = WSPipeline(
        source=src,
        on_status=lambda s: None,
        on_translation=on_translation,
        on_error=lambda e: print(f"\n[错误] {e}"),
        on_log=lambda s: _log(s),
    )
    pipe._flush_final = lambda: on_translation("")
    return pipe


def build_continuous_pipeline(src, log, args, dub_sinks=None):
    from pipeline_media import MediaPipeline

    def on_status(s):
        if s == "流式翻译中":
            log.mark_stream_start()
        _log(f"[状态] {s}")

    return MediaPipeline(
        source=src,
        on_partial=log.partial,
        on_subtitle=log.subtitle,
        on_status=on_status,
        on_log=_log,
        on_error=lambda e: print(f"\n[错误] {e}"),
        max_line_chars=args.line_chars,
        line_timeout=args.line_timeout,
        segment_seconds=args.segment,
        max_session_seconds=args.max_session,
        dub=args.dub or args.dub_save_only,
        dub_sinks=dub_sinks,
    )


def _log(s):
    if s and not s.startswith("  推帧"):
        print(f"\n[log] {s}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--media", help="视频/音频文件(自动抽音轨)")
    ap.add_argument("--wav", help="已抽好的 24kHz 单声道 wav")
    ap.add_argument("--ss", type=float, default=0.0,
                    help="在媒体文件内跳过多少秒(wav 已是切好的片段就别传)")
    ap.add_argument("--offset", type=float, default=None,
                    help="字幕时间轴偏移(该片段在原视频中的起始秒);默认跟随 --ss")
    ap.add_argument("--t", type=float, default=60.0, help="测试时长(秒)")
    ap.add_argument("--src", default="English", help="源语言")
    ap.add_argument("--tgt", default="中文", help="目标语言")
    ap.add_argument("--mode", default="continuous", choices=("continuous", "vad"))
    ap.add_argument("--fast", action="store_true", help="不按播放速度,全速喂")
    ap.add_argument("--line-chars", type=int, default=40, help="字幕最长字数")
    ap.add_argument("--line-timeout", type=float, default=6.0, help="多久无新字定稿")
    ap.add_argument("--segment", type=float, default=0, help=">0 时每 N 秒重建会话")
    ap.add_argument("--max-session", type=float, default=None,
                    help="无限期流(会议):每个 session 跑这么多秒就回收再开新的"
                         "(默认 110,在模型漂移前换新会话)")
    ap.add_argument("--out", default="../audio_test/cs336", help="字幕输出目录")
    ap.add_argument("--trace", action="store_true",
                    help="打印后端每一个事件(调试推流停住之类的问题)")
    ap.add_argument("--dub", action="store_true",
                    help="开中文配音:后端合成语音,边翻边从扬声器播出")
    ap.add_argument("--dub-save-only", action="store_true",
                    help="出配音但不播放,只落 wav(适合没有音频设备时核对)")
    args = ap.parse_args()

    path = args.wav or args.media
    if not path:
        ap.error("需要 --media 或 --wav")

    cfg.VOICE_CLONE_PROMPT, cfg.ASSISTANT_PROMPT = cfg.make_prompts(args.src, args.tgt)
    print(f"语言   : {args.src} -> {args.tgt}")
    print(f"模式   : {args.mode}")
    print(f"提示词 : {cfg.VOICE_CLONE_PROMPT!r}")

    src = FileCapture(path, start_sec=args.ss, duration=args.t,
                      realtime=not args.fast)
    print(f"音源   : {src.name}  {src.duration_sec:.1f}s"
          f"  ({'全速' if args.fast else '按播放速度'})\n")

    t0 = time.time()
    log = SubtitleLog(t0, offset=args.offset if args.offset is not None else args.ss)
    if args.mode == "vad" and (args.dub or args.dub_save_only):
        ap.error("配音目前只支持 --mode continuous")

    # 配音输出:播放器(实时)+ 落盘器(事后核对),可同时挂
    dub_sinks, player, recorder = [], None, None
    if args.dub or args.dub_save_only:
        from audio.dub_player import DubPlayer, DubRecorder
        recorder = DubRecorder()
        dub_sinks.append(recorder)
        if args.dub:
            player = DubPlayer(on_log=_log)
            player.start()
            dub_sinks.append(player)
        print(f"配音   : 已开启({'实时播放 + 落盘' if args.dub else '仅落盘'})"
              f" —— 首次连接会因为切 use_tts 冷加载,耐心等\n")

    pipe = (build_continuous_pipeline(src, log, args, dub_sinks)
            if args.mode == "continuous" else build_vad_pipeline(src, log))
    if args.trace:
        def on_event(ev):
            base = log._stream_t0 or t0
            print(f"\n  <evt {time.time()-base:7.2f}s> {ev.get('type')} "
                  f"kind={ev.get('kind')} {ev.get('text', '')[:60]!r}")
        pipe.ws._on_event = on_event
    pipe.start()

    # deadline 必须从**推流开始**算,不能从进程启动算 ——
    # session.init 冷启动要 24-184s(排队等上一个 session 拆除),
    # 否则等于把 init 时间从测试时长里扣掉,音频还没放完就被截断。
    try:
        while True:
            time.sleep(0.5)
            if log._stream_t0 and time.time() > log._stream_t0 + src.duration_sec + 60:
                print("\n[超时] 推流超过音频时长 +60s,停止")
                break
            if not log._stream_t0 and time.time() > t0 + cfg.WS_CONNECT_TIMEOUT + 30:
                print("\n[超时] session.init 一直没完成")
                break
            rec = src.recorder
            if rec is not None and rec.exhausted:
                time.sleep(3)   # 留时间收尾(别拖太久:后端是单 session,赶紧放掉)
                break
    except KeyboardInterrupt:
        print("\n中断")
    pipe.stop()
    if hasattr(pipe, "_flush_final"):
        pipe._flush_final()

    if player is not None:
        print(f"\n等配音播完… ({player.stats()})")
        player.stop(drain=True)
        print(f"  {player.stats()}")

    print(f"\n===== 共 {len(log.lines)} 条字幕 =====")
    if log._stream_t0:
        print(f"session.init 耗时: {log._stream_t0 - log.t0:.1f}s (冷启动/排队,一次性)")
    if log.first_latency is not None:
        print(f"首字延迟(推流起算): {log.first_latency:.1f}s")
    for t, txt in log.lines:
        print(f"[{t:6.1f}s] {txt}")

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"{Path(path).stem[:24]}_{int(args.ss)}s_{args.mode}"
    (outdir / f"{stem}.srt").write_text(log.to_srt(), encoding="utf-8")
    (outdir / f"{stem}.txt").write_text(
        "\n".join(t for _, t in log.lines), encoding="utf-8")
    print(f"\n已写出 {outdir / (stem + '.srt')}")

    if recorder is not None:
        dub_path = outdir / f"{stem}_dub.wav"
        if recorder.save(dub_path):
            print(f"配音音轨 {dub_path}  ({recorder.duration:.1f}s"
                  f",原声 {src.duration_sec:.1f}s"
                  f",压缩比 {recorder.duration/src.duration_sec:.2f}x)")
            print(f"  配音包数 {pipe.dub_chunks}")
        else:
            print("⚠️  没有收到任何配音音频")


if __name__ == "__main__":
    main()

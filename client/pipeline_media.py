# -*- coding: utf-8 -*-
"""媒体(视频/讲座)流式字幕 pipeline —— 连续推流,不用 VAD 切句。

为什么不用 pipeline_ws 的 VAD:
  讲座类音频是**连续语音**(实测 CS336 音轨 RMS 0.037~0.093,全程没有 1.5s 静音),
  VAD 只能靠 max_utterance_s 每 8s 强制断一次,断点落在句子中间,而且
  reset 后要 3 个块(0.6s)才重新起 speech,加上 0.5s 静音帧 —— 每 8s 丢掉 ~1s 音频。

这里改成:音轨按 1.5s 帧**背靠背连续推**,让 duplex(hard-listen)一直滚动出译文;
字幕的断句交给**文本层**(中文句末标点 / 长度 / 静默超时),不再切音频。

会话保活:
  duplex 的 KV 会随时间增长,`--segment` 秒后主动 reset 一次会话(重连),
  避免上下文无限膨胀。默认 0 = 不重置。
"""
import re
import threading
import time

import numpy as np

from api.ws_duplex_client import WSDuplexClient
from audio import slicer
from config import (SAMPLE_RATE, SLICE_SECONDS, MAX_SESSION_SECONDS,
                    WS_RECONNECT_TIMEOUT)
import text_utils

# 中文/英文句末标点 —— 用来把连续译文流切成一条条字幕
SENT_END = "。！？!?；;…"
# 逗号级别的次要断点(句子太长时兜底)
SOFT_END = "，,、"


class MediaPipeline:
    """连续音频 -> 流式译文 -> 逐句字幕。

    回调:
      on_partial(text)   当前未定稿的字幕(滚动中)
      on_subtitle(text)  一条定稿字幕
      on_status / on_log / on_error
    """

    def __init__(self, source, on_partial=None, on_subtitle=None,
                 on_status=None, on_log=None, on_error=None,
                 system_prompt=None, ws_client=None,
                 max_line_chars=40, line_timeout=6.0, segment_seconds=0,
                 dub=False, dub_sinks=None, max_session_seconds=None):
        self.source = source
        self.on_partial = on_partial or (lambda t: None)
        self.on_subtitle = on_subtitle or (lambda t: None)
        self.on_status = on_status or (lambda s: None)
        self.on_log = on_log or (lambda s: None)
        self.on_error = on_error or (lambda e: None)

        if system_prompt is None:
            import config as cfg
            system_prompt = cfg.VOICE_CLONE_PROMPT
        self.system_prompt = system_prompt
        # dub=True -> session.init 带 use_tts,后端把中文配音流式推回来。
        # 注意:切换 use_tts 后端无法复用 shared_octx,这一次连接是冷加载。
        self.dub = dub
        self.ws = ws_client or WSDuplexClient(system_prompt=system_prompt,
                                              use_tts=dub)
        self.ws._on_text = self._on_frag
        # 配音消费者(播放器 / 落盘器),可以挂多个
        self.dub_sinks = list(dub_sinks or [])
        if self.dub_sinks:
            self.ws._on_audio = self._on_audio
        self.dub_chunks = 0
        self.dub_samples = 0

        self.max_line_chars = max_line_chars   # 超过这个长度在软标点处断行
        self.line_timeout = line_timeout       # 多久没新字就把当前行定稿
        self.segment_seconds = segment_seconds # >0 时周期性重建会话(旧参数,等价 max_session_seconds)
        # 滚动 session:无限期流(会议)下,每个 session 只跑这么久就干净地回收、
        # 立刻开新的。实测 scale=0.5 下回收只有 ~8s,而模型 ~90-180s 会漂移,
        # 所以默认 110s 在漂移之前就换新会话,永续且不漂。
        self.max_session_seconds = (max_session_seconds
                                    if max_session_seconds is not None
                                    else (segment_seconds or MAX_SESSION_SECONDS))

        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._buf = ""            # 当前未定稿字幕
        self._last_frag_at = 0.0
        self.frames_pushed = 0

    # ---- 生命周期 ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=8)
        # 必须真把 socket 关掉:后端是**单 session**,WS 一断它才 omni_prepare_for_reuse
        # 释放共享上下文。关不干净,下个 session.init 会撞上 "active session exists"。
        try:
            self.ws._force_close()
        except Exception:
            pass

    # ---- 文本层断句 ----
    def _on_frag(self, frag):
        """收到一个 text_delta:并进当前行,遇句末标点就定稿。"""
        frag = _fix_output(frag)
        if not frag:
            return
        with self._lock:
            self._buf += frag
            self._last_frag_at = time.time()
            lines, self._buf = _split_lines(self._buf, self.max_line_chars)
            partial = self._buf
        for ln in lines:
            self.on_subtitle(ln)
        self.on_partial(partial)

    def _on_audio(self, pcm):
        """收到一段 TTS 配音 —— 这是 WS 接收线程,必须立刻返回,不能在这播。"""
        self.dub_chunks += 1
        self.dub_samples += len(pcm)
        for sink in self.dub_sinks:
            try:
                sink.feed(pcm)
            except Exception as e:
                self.on_log(f"  配音输出失败: {e}")

    def _flush_line(self, force=False):
        """把当前行定稿(静默超时 / 收尾时调用)。"""
        with self._lock:
            text = self._buf.strip()
            if not text:
                return
            if not force and (time.time() - self._last_frag_at) < self.line_timeout:
                return
            self._buf = ""
        self.on_subtitle(text)
        self.on_partial("")

    # ---- 连接 ----
    def _connect(self, attempts=5, delay=3, timeout=None):
        """timeout=None -> config.WS_CONNECT_TIMEOUT(够冷加载);
        滚动回收传短超时,模型已常驻时 init 只要 ~5-8s。"""
        for i in range(attempts):
            try:
                self.ws.connect(timeout=timeout)
                self.on_log("WS 会话已建立")
                return True
            except Exception as e:
                self.on_log(f"  WS 连接失败({i + 1}/{attempts}): {e}")
                if self._stop.is_set():
                    return False
                time.sleep(delay)
        return False

    def _push(self, samples):
        try:
            self.ws.push_audio(samples)
            self.frames_pushed += 1
            return True
        except Exception as e:
            self.on_log(f"  推帧失败(连接断开): {e}")
            self.ws._mark_disconnected()
            return False

    # ---- 主循环 ----
    def _run(self):
        try:
            self.on_status("连接后端")
            if not self._connect():
                self.on_error("无法建立 WS 连接")
                return
            self.on_status("流式翻译中")
            rec = self.source.open()
            block_size = max(2048, int(SAMPLE_RATE * 0.2))
            frame_len = int(SAMPLE_RATE * SLICE_SECONDS)
            buf = []
            seg_started = time.time()

            with rec:
                while not self._stop.is_set():
                    if self.ws.disconnected:
                        self.on_status("重连中…")
                        self._flush_line(force=True)
                        if self._connect():
                            buf = []
                            seg_started = time.time()
                            self.on_status("流式翻译中")
                        else:
                            time.sleep(2)
                        continue

                    # 周期性回收会话,防 duplex 上下文无限增长 —— 无限期流的滚轮。
                    # 关键:先 _force_close 把旧 WS 干净关掉(后端是单 session,
                    # 不关它新 init 会撞 "active session exists"),再 _connect。
                    if self.max_session_seconds and \
                            (time.time() - seg_started) >= self.max_session_seconds:
                        self.on_log(f"  会话已跑 {self.max_session_seconds}s,滚动回收")
                        self._finish_turn()
                        self._flush_line(force=True)
                        try:
                            self.ws._force_close()
                        except Exception:
                            pass
                        if not self._connect(attempts=3, delay=2,
                                             timeout=WS_RECONNECT_TIMEOUT):
                            self.on_error("滚动回收失败")
                            return
                        buf = []
                        seg_started = time.time()

                    block = rec.record(block_size)
                    if block is None or len(block) == 0:
                        time.sleep(0.01)
                        continue
                    block = np.asarray(block, dtype=np.float32).reshape(-1)
                    buf.append(block)

                    # 攒满一帧就推(背靠背,不留缝)
                    if sum(len(b) for b in buf) >= frame_len:
                        audio = slicer.concat_audio(buf)
                        while len(audio) >= frame_len and not self._stop.is_set():
                            if not self._push(audio[:frame_len].tolist()):
                                break
                            audio = audio[frame_len:]
                        buf = [audio]

                    # 静默超时定稿(模型这一段说完了)
                    self._flush_line()

                    if getattr(rec, "exhausted", False):
                        self.on_log("  音源播放结束,收尾")
                        break

            # 收尾:推静音把最后一句补完
            self._finish_turn()
            self._flush_line(force=True)
        except Exception as e:
            self.on_error(f"媒体管线异常: {e}")
            self.on_log(f"媒体管线异常: {e!r}")
        finally:
            self.on_status("已停止")

    def _finish_turn(self):
        """推静音帧触发模型把最后的译文吐完。

        先 reset_turn 清掉上一次 chunk 留下的 done 标志,否则 wait 会立刻返回。
        别等太久:后端是单 session,这里只是补最后一句,不是把 session 攥着不放。
        """
        try:
            self.ws.reset_turn()
            self.ws.push_silence(0.8)
            self.ws.wait_turn_done(timeout=6)
        except Exception:
            pass


def _split_lines(text, max_chars):
    """从累积文本里切出已完成的字幕行,返回 (完整行列表, 剩余)。"""
    lines = []
    while True:
        idx = _find_break(text, max_chars)
        if idx < 0:
            break
        ln = text[:idx + 1].strip()
        if ln:
            lines.append(ln)
        text = text[idx + 1:].lstrip()
    return lines, text


def _find_break(text, max_chars):
    """找断点:优先句末标点;文本过长时退而求其次用逗号。"""
    for i, ch in enumerate(text):
        if ch in SENT_END:
            return i
    if len(text) >= max_chars:
        # 从 max_chars 往前找最近的软标点
        for i in range(min(len(text), max_chars + 12) - 1, max_chars // 2, -1):
            if text[i] in SOFT_END:
                return i
        return max_chars - 1   # 实在没标点就硬切
    return -1


def _fix_output(text):
    """duplex 输出修正(下划线->空格、去中文之间的空格),见 text_utils。"""
    return text_utils.fix_output(text)

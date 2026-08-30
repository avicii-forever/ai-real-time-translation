# -*- coding: utf-8 -*-
"""WS full_duplex 流式翻译 pipeline:采集同时推帧,边说边出译文。

与 HTTP pipeline(等整句说完再翻)不同,WS duplex 是逐帧流水:
- 采集线程持续读块 -> VAD 判断"有声/静音"
- 有声段切成 1.5s 帧 -> push_audio(后端立即 prefill+decode)
- 每帧的 text_delta 流式回调 -> 累积显示(边说边翻)
- VAD 静音结束 -> push_silence 触发收尾 -> 收完整译文

滚动 session(长会议必需):
  duplex 会话跑久了模型会漂移(~90-180s 后中文里掺英文,见 duplex-drift-root-cause),
  所以跑满 MAX_SESSION_SECONDS 就把会话回收重开。**只在 VAD 判定静音时回收**,
  不切断正在说的句子;VAD 最长 8s 强制断句,所以最多超时 8s 就能落到边界上。
"""
import threading
import time

import numpy as np

from audio.capture import LoopbackCapture, MicCapture
from audio.vad import VAD
from audio import slicer
from audio.dub_player import DubPlayer
from api.ws_duplex_client import WSDuplexClient
import config as cfg
from config import SAMPLE_RATE
import text_utils


class WSPipeline:
    def __init__(self, source=None, on_status=None, on_translation=None,
                 on_error=None, on_log=None, ws_client=None, system_prompt=None,
                 max_session_seconds=None, dub=False):
        self.source = source
        self.on_status = on_status or (lambda s: None)
        self.on_translation = on_translation or (lambda t: None)
        self.on_error = on_error or (lambda e: None)
        self.on_log = on_log or (lambda s: None)

        # 🔧 语言配置修复:运行时从 config 模块读取(而非 import 时固化),
        # 这样 GUI 在 _start 里改 cfg.VOICE_CLONE_PROMPT 才能生效。
        if system_prompt is None:
            import config as cfg
            system_prompt = cfg.VOICE_CLONE_PROMPT
        self.dub = dub
        # dub=True -> session.init 带 use_tts,后端流式推回中文配音。
        # 注意:切换 use_tts 后端无法复用 shared_octx,这一次连接是冷加载。
        self.ws = ws_client or WSDuplexClient(system_prompt=system_prompt,
                                              use_tts=dub)
        self.ws._on_text = self._on_frag
        self.dub_player = None
        if dub:
            self.dub_player = DubPlayer(on_log=self.on_log)
            self.ws._on_audio = self._on_audio

        # 0/None = 不回收(一直用同一个 session,长会议会漂)
        self.max_session_seconds = (cfg.MAX_SESSION_SECONDS
                                    if max_session_seconds is None
                                    else max_session_seconds)
        self.sessions = 0        # 已建立的会话数(含滚动回收出来的)

        self._stop = threading.Event()
        self._thread = None
        self._cur_text = []     # 当前句已出译文片段
        self._lock = threading.Lock()

    # ---- 生命周期 ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        if self.dub_player:
            self.dub_player.start()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.on_status("启动中")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        # 必须 _force_close:后端是**单 session**,socket 真断了它才
        # omni_prepare_for_reuse 释放上下文。关不干净下次 start 会撞
        # "session.init rejected — active session exists"。
        try:
            self.ws._force_close()
        except Exception:
            pass
        if self.dub_player:
            self.dub_player.stop(drain=True)

    # ---- 流式回调 ----
    def _on_frag(self, frag):
        """WS 收到一个 text_delta -> 更新译文。"""
        with self._lock:
            self._cur_text.append(frag)
            cur = "".join(self._cur_text)
        self.on_translation(_fix_output(cur))   # 增量显示

    def _on_audio(self, pcm):
        """WS 收到一段 TTS 配音 —— 这是接收线程,必须立刻返回,塞进播放队列。"""
        if self.dub_player:
            self.dub_player.feed(pcm)

    @staticmethod
    def _cleanup():
        pass

    # ---- 连接(含断线重连)----
    def _connect_ws(self, attempts=5, delay=3, timeout=None):
        """建立 WS 会话,失败重试(后端可能正在重启 / 旧 session 未释放)。

        timeout=None -> config.WS_CONNECT_TIMEOUT(够冷加载 19.7GB 模型);
        滚动回收这种"模型已常驻"的场景传短超时,别让界面静默等几分钟。
        """
        for i in range(attempts):
            try:
                self.ws.connect(timeout=timeout)
                self.sessions += 1
                self.on_log("WS 会话已建立")
                return True
            except Exception as e:
                self.on_log(f"  WS 连接失败({i + 1}/{attempts}): {e}")
                if self._stop.is_set():
                    return False
                time.sleep(delay)
        return False

    def _roll_session(self):
        """回收当前会话、立刻开新的(防长会话漂移)。

        调用点保证此刻处于静音(VAD 未在语句中),所以不会切断正在说的句子。
        代价:回收期间(实测 ~8s)采不到音,这段音频会丢 —— 单 session 后端
        没法预热下一个连接,只能挑静音时付这个代价。
        """
        self.on_status("会话回收中…")
        self.on_log(f"  会话已跑 {self.max_session_seconds}s,滚动回收")
        try:
            self.ws._force_close()
        except Exception:
            pass
        with self._lock:
            self._cur_text = []
        # 短超时快速试;失败就交给主循环的断线重连分支(用完整超时慢慢磨)
        if self._connect_ws(attempts=2, delay=1,
                            timeout=cfg.WS_RECONNECT_TIMEOUT):
            self.on_status("采集中(等待说话)")
            return True
        self.on_log("  滚动回收失败,转入重连")
        self.ws._mark_disconnected()
        return False

    def _safe_push(self, samples):
        """推一帧;若连接已断开则标记并返回 False,让主循环去重连。"""
        try:
            self.ws.push_audio(samples)
            return True
        except Exception as e:
            self.on_log(f"  推帧失败(连接断开): {e}")
            self.ws._mark_disconnected()
            return False

    # ---- 主循环 ----
    def _run(self):
        try:
            self.on_log("连接 WS duplex 后端...")
            if not self._connect_ws():
                self.on_error("无法建立 WS 连接")
                return
            self.on_status("采集中(等待说话)")
            vad = VAD(sample_rate=SAMPLE_RATE)
            rec = self.source.open()
            block_size = max(2048, int(SAMPLE_RATE * 0.2))
            buf = []          # 当前句的 float32 块
            sess_started = time.time()

            with rec:
                while not self._stop.is_set():
                    # 断线自动重连:后端/隧道掉线后静默恢复
                    if self.ws.disconnected:
                        self.on_status("重连中…")
                        if self._connect_ws():
                            vad.reset()
                            buf = []
                            sess_started = time.time()
                            with self._lock:
                                self._cur_text = []
                            self.on_status("采集中(等待说话)")
                        else:
                            time.sleep(2)
                        continue

                    # 滚动回收:跑满就换新会话,但**只在静音时**换,
                    # 避免把正在说的句子切两半(VAD 最长 8s 强制断句,
                    # 所以最多晚 8s 就能等到这个边界)。
                    if (self.max_session_seconds and not vad.speaking
                            and (time.time() - sess_started) >= self.max_session_seconds):
                        rolled = self._roll_session()
                        vad.reset()
                        buf = []
                        sess_started = time.time()
                        if not rolled:
                            continue

                    block = rec.record(block_size)
                    if block is None or len(block) == 0:
                        time.sleep(0.01)
                        continue
                    block = np.asarray(block, dtype=np.float32).reshape(-1)
                    dt = len(block) / SAMPLE_RATE
                    evt = vad.process(block, dt)

                    if evt == "speech_start":
                        self.on_status("检测到语音,采集中")
                        self.ws.reset_turn()
                        with self._lock:
                            self._cur_text = []
                        self.on_translation("")
                        buf = [block]
                    elif evt is None and vad.speaking:
                        buf.append(block)
                        # 攒满 1.5s 就推一帧
                        total = sum(len(b) for b in buf) / SAMPLE_RATE
                        if total >= cfg.SLICE_SECONDS:
                            audio = slicer.concat_audio(buf)
                            frame = audio[:int(SAMPLE_RATE * cfg.SLICE_SECONDS)]
                            if not self._safe_push(frame.tolist()):
                                continue
                            self.on_log(f"  推帧 {len(frame)/SAMPLE_RATE:.1f}s")
                            buf = [audio[int(SAMPLE_RATE * cfg.SLICE_SECONDS):]]
                    elif evt == "speech_end":
                        buf.append(block)
                        ok = True
                        # 剩余音频不足 1.5s 也推(收尾)
                        if buf:
                            audio = slicer.concat_audio(buf)
                            ok = self._safe_push(audio.tolist())
                            if ok:
                                self.on_log(f"  末帧 {len(audio)/SAMPLE_RATE:.1f}s")
                        if ok:
                            try:
                                # 空帧触发收尾
                                self.ws.push_silence(0.5)
                                self.on_status("语句结束,收尾中")
                                # 等最后一帧 done
                                final = self.ws.wait_turn_done(timeout=20)
                                if final.strip():
                                    self.on_translation(_fix_output(final.strip()))
                                self.on_log("  本句完成")
                            except Exception as e:
                                self.on_log(f"  收尾失败(连接断开): {e}")
                                self.ws._mark_disconnected()
                        buf = []
                        vad.reset()
                        self.on_status("采集中(等待说话)")
        except Exception as e:
            self.on_error(f"采集异常: {e}")
            self.on_log(f"采集异常: {e!r}")
        finally:
            self.on_status("已停止")


def _fix_output(text):
    """duplex 输出修正(下划线->空格、去中文之间的空格),见 text_utils。"""
    return text_utils.fix_output(text).strip()

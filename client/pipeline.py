# -*- coding: utf-8 -*-
"""采集→切片→上传→prefill→decode 状态机。

线程模型:
- capture 线程:打开音频源,持续读块 → VAD → 缓存 utterance 音频
- 当 VAD 判定语句结束:把 utterance 切片 → SFTP 上传每块 → prefill
  → decode(SSE)→ 回调译文
- 用回调(on_status / on_translation / on_error)与 GUI 解耦
"""
import os
import re
import tempfile
import threading
import time

import numpy as np

from audio.capture import LoopbackCapture, MicCapture
from audio.vad import VAD
from audio import slicer
from audio import player as audio_player
from api.omni_client import OmniClient, OmniError
from transport.sftp_uploader import SFTPUploader
from config import (
    SAMPLE_RATE, SLICE_SECONDS, USE_TTS, TTS_PLAY,
    REMOTE_TTS_DIR_FMT, TTS_DONE_FLAG, TTS_WAIT_MAX,
    REMOTE_OUTPUT_DIR,
)


class Pipeline:
    def __init__(self, source=None, on_status=None, on_translation=None,
                 on_error=None, on_log=None, omni_client=None, uploader=None):
        self.source = source            # CaptureSource(loopback/mic)
        self.on_status = on_status or (lambda s: None)
        self.on_translation = on_translation or (lambda t: None)
        self.on_error = on_error or (lambda e: None)
        self.on_log = on_log or (lambda s: None)

        self.omni = omni_client or OmniClient()
        self.uploader = uploader or SFTPUploader()

        self._stop = threading.Event()
        self._thread = None
        self._init_lock = threading.Lock()
        self._connected = False

    # ---- 生命周期 ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.on_status("启动中")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    # ---- 初始化(连接上传器,惰性;omni_init 每轮在 _translate_batch 做)----
    def _ensure_ready(self):
        with self._init_lock:
            if self._connected:
                return
            self.on_log("连接上传器(嵌套 SSH/SFTP)...")
            self.uploader.connect()
            self._connected = True
            self.on_log("上传器就绪(会话初始化在首轮翻译时进行)")

    # ---- 主循环 ----
    def _run(self):
        try:
            self._ensure_ready()
            self.on_status("采集中(等待说话)")
            vad = VAD(sample_rate=SAMPLE_RATE)
            frames = []          # 当前 utterance 的 float32 块
            rec = self.source.open()
            block_size = max(2048, int(SAMPLE_RATE * 0.2))  # 0.2s 一块

            # soundcard recorder 需 with 进入才初始化(否则 record 报 _pending_chunk)
            with rec:
                while not self._stop.is_set():
                    block = rec.record(block_size)  # float32 (n,1) or (n,)
                    if block is None or len(block) == 0:
                        time.sleep(0.01)
                        continue
                    block = np.asarray(block, dtype=np.float32).reshape(-1)  # (n,) mono
                    dt = len(block) / SAMPLE_RATE
                    evt = vad.process(block, dt)

                    if evt == "speech_start":
                        self.on_status("检测到语音,采集中")
                        frames = [block]
                    elif evt is None and vad.speaking:
                        frames.append(block)
                    elif evt == "speech_end":
                        frames.append(block)
                        self.on_status("语句结束,翻译中")
                        try:
                            self._translate(frames)
                        except Exception as e:
                            self.on_error(f"翻译失败: {e}")
                            self.on_log(f"翻译失败: {e!r}")
                        frames = []
                        vad.reset()
                        self.on_status("采集中(等待说话)")
        except Exception as e:
            self.on_error(f"采集异常: {e}")
            self.on_log(f"采集异常: {e!r}")
        finally:
            self.on_status("已停止")

    # ---- 翻译一段 utterance(超长自动分段)----
    def _translate(self, frames):
        audio = slicer.concat_audio(frames)
        self.on_log(f"utterance {len(audio)/SAMPLE_RATE:.1f}s")
        chunks = slicer.slice_audio(audio)
        if not chunks:
            self.on_log("(无有效音频)")
            return

        # 后端对单次翻译可靠的上限约 6 块(≈9s,实测 8 块已退化);
        # 超长时按块分批,每批独立一轮 prefill+decode。
        BATCH = 6
        for b in range(0, len(chunks), BATCH):
            batch = chunks[b:b + BATCH]
            self._translate_batch(batch, batch_no=b // BATCH)

    def _translate_batch(self, chunks, batch_no=0):
        # 每轮用全新 session(omni_init)保证 TTS 可靠产出:
        # 同 session 多轮时第二轮 TTS 不产出(后端 simplex_round_idx 递增 bug)。
        # 先清远端输出目录,再 omni_init(use_tts=true),再 prefill cnt=0 + 音频。
        self.uploader.reset_idx()
        self.on_log("  重置 session(omni_init, 保证 TTS 产出)...")
        try:
            self.uploader.rmtree(REMOTE_OUTPUT_DIR)
        except Exception:
            pass
        self.uploader.mkdirs(REMOTE_OUTPUT_DIR)
        self.omni.omni_init(use_tts=USE_TTS, output_dir=REMOTE_OUTPUT_DIR)
        self.on_log("  prefill cnt=0 (system init)")
        self.omni.prefill("", cnt=0)
        for i, (wav, sec) in enumerate(chunks):
            self.on_log(f"  [batch{batch_no}] 上传切片 {i+1}/{len(chunks)} ({sec:.1f}s) ...")
            remote_path = self.uploader.upload(wav)
            self.on_log(f"  prefill cnt={i+1}")
            self.omni.prefill(remote_path, cnt=i + 1)

        self.on_log(f"  decode(round=0, 新 session)...")
        text, elapsed = self.omni.decode(round_idx=0,
                                         on_content=lambda frag: None)
        cleaned = _clean_output(text)
        if cleaned:
            self.on_translation(cleaned)
            self.on_log(f"  decode {elapsed:.1f}s -> {cleaned[:60]}...")
        else:
            self.on_log("  decode 返回空")

        # TTS 语音:decode 后从服务端拉取 tts_wav 并播放
        if USE_TTS and TTS_PLAY and cleaned:
            try:
                self._play_tts(round_idx=0)
            except Exception as e:
                self.on_log(f"  TTS 播放失败: {e!r}")

    def _play_tts(self, round_idx):
        """等待 TTS 落盘完成,拉取 wav 段合并播放。"""
        tts_dir = REMOTE_TTS_DIR_FMT.format(round_idx)
        self.on_log(f"  等 TTS 落盘({tts_dir})...")
        # 等待 generation_done.flag(最多 TTS_WAIT_MAX 秒)
        flag_path = f"{tts_dir}/{TTS_DONE_FLAG}"
        waited = 0.0
        while waited < TTS_WAIT_MAX:
            files = self.uploader.listdir(tts_dir)
            if TTS_DONE_FLAG in files:
                break
            time.sleep(0.5)
            waited += 0.5
        if waited >= TTS_WAIT_MAX:
            self.on_log("  TTS 落盘超时,跳过语音")
            return

        # 拉取 wav_N.wav(排序)
        wav_names = sorted(f for f in files if f.startswith("wav_") and f.endswith(".wav"))
        if not wav_names:
            self.on_log("  (无 TTS wav 段)")
            return
        self.on_log(f"  拉取 {len(wav_names)} 段 TTS 语音...")
        tmp_dir = tempfile.mkdtemp(prefix="tts_")
        local_wavs = []
        try:
            for name in wav_names:
                local = os.path.join(tmp_dir, name)
                self.uploader.pull_file(f"{tts_dir}/{name}", local)
                local_wavs.append(local)
            # 合并播放
            ok = audio_player.play_wav_files(local_wavs)
            self.on_log(f"  TTS 播放{'完成' if ok else '失败'} ({len(local_wavs)} 段)")
        finally:
            for f in local_wavs:
                try:
                    os.remove(f)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass


def _clean_output(text):
    """清理模型输出:去掉角色前缀和多余客套。"""
    t = text.strip()
    # 去掉 "Me:", "Assistant:", "AI:" 等角色前缀
    t = re.sub(r"^(Me|Assistant|AI|Model)\s*[:：]\s*", "", t)
    # 去掉开头 "Okay," / "Oh, I see" 等(若紧跟无标点则保留主体)
    t = re.sub(r"^(Okay|Oh|Alright|Sure|Yes)[,，]?\s+", "", t, flags=re.I)
    t = t.strip()
    return t

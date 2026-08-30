# -*- coding: utf-8 -*-
"""文件音频源:把 wav / 视频音轨当成"正在播放的系统声音"喂进 pipeline。

用途:测试"播放视频 → 流式字幕",不依赖声卡 loopback,可复现。
接口与 audio/capture.py 的 CaptureSource 一致(open() 返回带 record() 的
上下文管理器),所以能直接塞进 WSPipeline。

realtime=True 时按真实播放速度出块(sleep 对齐 wall-clock),模拟边播边译;
realtime=False 时全速喂,用于快速回归。
"""
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from audio.capture import CaptureSource
from config import SAMPLE_RATE


def ffmpeg_exe():
    """拿 imageio-ffmpeg 自带的 ffmpeg(免装系统 ffmpeg)。"""
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def extract_audio(media_path, out_wav=None, start_sec=0.0, duration=None,
                  sample_rate=SAMPLE_RATE):
    """从视频/音频文件抽单声道 wav(默认 24kHz,后端硬要求)。"""
    media_path = str(media_path)
    if out_wav is None:
        out_wav = Path(tempfile.gettempdir()) / (
            f"rt_{Path(media_path).stem[:20]}_{int(start_sec)}.wav")
    out_wav = str(out_wav)
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y"]
    if start_sec:
        cmd += ["-ss", str(start_sec)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-i", media_path, "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-c:a", "pcm_s16le", out_wav]
    subprocess.run(cmd, check=True)
    return out_wav


def read_wav_mono(path, target_sr=SAMPLE_RATE):
    """读 wav → float32 单声道 [-1,1],必要时线性重采样到 target_sr。"""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"仅支持 16-bit PCM wav,当前 {width * 8}-bit")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != target_sr and data.size:
        n_out = int(round(data.size * target_sr / sr))
        data = np.interp(
            np.linspace(0, data.size - 1, n_out, dtype=np.float64),
            np.arange(data.size, dtype=np.float64),
            data,
        ).astype(np.float32)
    return data


class _FileRecorder:
    """模拟 soundcard recorder:record(n) 按播放速度返回 n 个采样。"""

    def __init__(self, samples, realtime=True, sample_rate=SAMPLE_RATE,
                 on_progress=None):
        self._samples = samples
        self._pos = 0
        self._realtime = realtime
        self._sr = sample_rate
        self._t0 = None
        self._on_progress = on_progress
        self.exhausted = False

    def __enter__(self):
        self._t0 = time.time()
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        if self._pos >= len(self._samples):
            self.exhausted = True
            # 播完后返回静音,让 pipeline 的 VAD 能收尾最后一句
            return np.zeros(numframes, dtype=np.float32)
        if self._realtime:
            # 对齐 wall-clock:第 pos+numframes 个采样应在 t0 + (pos+n)/sr 时到达
            due = self._t0 + (self._pos + numframes) / self._sr
            delay = due - time.time()
            if delay > 0:
                time.sleep(delay)
        block = self._samples[self._pos:self._pos + numframes]
        self._pos += numframes
        if block.size < numframes:
            block = np.pad(block, (0, numframes - block.size))
        if self._on_progress:
            self._on_progress(self._pos / self._sr)
        return block.astype(np.float32)

    @property
    def position_sec(self):
        return self._pos / self._sr


class FileCapture(CaptureSource):
    """把本地 wav / 视频当采集源。

        src = FileCapture("cs336.mp4", start_sec=300, duration=60)
        pipe = WSPipeline(source=src, ...)
    """

    def __init__(self, path, start_sec=0.0, duration=None, realtime=True,
                 on_progress=None):
        self._path = str(path)
        self._start = start_sec
        self._duration = duration
        self._realtime = realtime
        self._on_progress = on_progress
        self._samples = None
        self.recorder = None

    @property
    def name(self):
        return f"文件:{Path(self._path).name}"

    def load(self):
        """预加载音频(视频自动抽轨)。返回 float32 采样。"""
        if self._samples is not None:
            return self._samples
        p = self._path
        if not p.lower().endswith(".wav"):
            p = extract_audio(p, start_sec=self._start, duration=self._duration)
            self._samples = read_wav_mono(p)
        else:
            data = read_wav_mono(p)
            a = int(self._start * SAMPLE_RATE)
            if a >= len(data):
                raise ValueError(
                    f"起始位置 {self._start}s 超出音频长度 "
                    f"{len(data)/SAMPLE_RATE:.1f}s({Path(p).name})。"
                    " wav 已经是切好的片段时不要再传 --ss,用 --offset 只改字幕时间轴。")
            b = a + int(self._duration * SAMPLE_RATE) if self._duration else len(data)
            self._samples = data[a:b]
        if self._samples.size == 0:
            raise ValueError(f"音源为空:{p}")
        return self._samples

    @property
    def duration_sec(self):
        return len(self.load()) / SAMPLE_RATE

    def open(self, block_size=4800):
        self.recorder = _FileRecorder(
            self.load(), realtime=self._realtime, on_progress=self._on_progress)
        return self.recorder

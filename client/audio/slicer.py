# -*- coding: utf-8 -*-
"""切片器:把一段 float32 音频切成 1.5s 的 WAV 块(内存)。

后端硬约束:24kHz、1-2s 块。本模块把一次 utterance 的音频
切成固定窗,不足则 pad 静音,输出 (wav_bytes, seconds) 列表。
"""
import io
import wave

import numpy as np

import config as _cfg
from config import SAMPLE_RATE


def float32_to_pcm16(block):
    """float32 [-1,1] -> int16 PCM bytes。"""
    b = np.asarray(block, dtype=np.float32)
    b = np.clip(b, -1.0, 1.0)
    pcm = (b * 32767.0).astype(np.int16)
    return pcm.tobytes()


def wav_bytes(pcm16_bytes, sample_rate=SAMPLE_RATE):
    """int16 PCM -> wav bytes(内存)。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16_bytes)
    return buf.getvalue()


def slice_audio(mono_f32, sample_rate=SAMPLE_RATE, slice_s=None,
                pad_to_slice=True):
    """把一段 float32 mono 音频切成 [slice_s] 的 wav 块。

    返回 [(wav_bytes, seconds_actual), ...]。最后一块不足时 pad 静音
    (pad_to_slice=True) 或不处理(False,直接小块)。
    """
    if slice_s is None:
        slice_s = _cfg.SLICE_SECONDS  # 运行时读:让 GUI 改 cfg.SLICE_SECONDS 生效
    if len(mono_f32) == 0:
        return []
    block = int(sample_rate * slice_s)
    chunks = []
    n = len(mono_f32)
    for start in range(0, n, block):
        seg = mono_f32[start:start + block]
        sec = len(seg) / sample_rate
        if pad_to_slice and len(seg) < block:
            seg = np.pad(seg, (0, block - len(seg)))
        chunks.append((wav_bytes(float32_to_pcm16(seg), sample_rate), sec))
    return chunks


def concat_audio(frames):
    """把多块 float32 拼成一段。"""
    if not frames:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(frames)

# -*- coding: utf-8 -*-
"""TTS 语音播放:从远端拉取 wav 段 -> 合并 -> 本地播放。

winsound.PlaySound 播放 24kHz mono wav。合并多段成一个 wav 再播,
避免逐段播放的间隙。
"""
import io
import os
import wave

import winsound


def read_wav_frames(path):
    """读 wav,返回 (params, frames_bytes)。"""
    w = wave.open(path, "rb")
    params = w.getparams()
    frames = w.readframes(w.getnframes())
    w.close()
    return params, frames


def merge_wav_bytes(wav_paths):
    """把多个同规格 wav 合并为一个 wav 的 bytes(自动跳过坏文件)。"""
    combined = bytearray()
    params = None
    for p in wav_paths:
        try:
            pr, frames = read_wav_frames(p)
        except Exception:
            continue
        if params is None:
            params = pr
        combined += frames
    if params is None or not combined:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(params.nchannels)
        w.setsampwidth(params.sampwidth)
        w.setframerate(params.framerate)
        w.writeframes(bytes(combined))
    return buf.getvalue()


def play_wav_bytes(wav_bytes):
    """播放 wav bytes。返回是否成功。"""
    if not wav_bytes:
        return False
    tmp = os.path.join(os.environ.get("TEMP", "."), "_trans_tts_play.wav")
    with open(tmp, "wb") as f:
        f.write(wav_bytes)
    try:
        winsound.PlaySound(tmp, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
        return True
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def play_wav_files(wav_paths):
    """合并并播放一组 wav 文件。"""
    data = merge_wav_bytes(wav_paths)
    if data is None:
        return False
    return play_wav_bytes(data)

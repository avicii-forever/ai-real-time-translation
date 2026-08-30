# -*- coding: utf-8 -*-
"""音频采集:系统声音(loopback)+ 麦克风。

用 soundcard 库:
- 系统声音:打开默认扬声器的 loopback recorder(采集声卡正在播放的内容)
- 麦克风:打开默认麦克风

输出:24kHz 单声道 float32(由 capture 内部重采样),供 slicer 切块。
"""
import threading
import time

import numpy as np
import soundcard as sc

from config import SAMPLE_RATE


class AudioCaptureError(Exception):
    pass


class CaptureSource:
    """统一采集源接口。start 返回一个采样器,每帧 yield (float32 mono 数组)。"""

    def open(self, block_size=4800):
        """返回采样器对象:带 record(numframes) 方法的 recorder。

        用法:
            rec = source.open()
            while True:
                block = rec.record(4800)   # 拉一块,float32 (n,1)
        """
        raise NotImplementedError

    @property
    def name(self):
        raise NotImplementedError


class LoopbackCapture(CaptureSource):
    """系统声音(扬声器 loopback)。采集正在播放的音频。

    soundcard 里 loopback = 扬声器作为"虚拟麦克风",通过
    all_microphones(include_loopback=True) 枚举,isloopback=True。
    """

    def __init__(self, device=None):
        self._device = device  # None = 自动找默认 loopback 设备

    @property
    def name(self):
        if self._device is not None:
            return self._device.name
        return "系统声音(loopback,自动)"

    def open(self, block_size=4800):
        dev = self._device
        if dev is None:
            # 优先第一个 isloopback 设备
            for m in sc.all_microphones(include_loopback=True):
                if getattr(m, "isloopback", False):
                    dev = m
                    break
            if dev is None:
                raise AudioCaptureError("未找到系统声音 loopback 设备")
        rec = dev.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=block_size)
        return rec


class MicCapture(CaptureSource):
    """麦克风。"""

    def __init__(self, device=None):
        self._device = device

    @property
    def name(self):
        return self._device.name if self._device else "麦克风(默认)"

    def open(self, block_size=4800):
        dev = self._device
        if dev is None:
            dev = sc.default_microphone()
        if dev is None:
            raise AudioCaptureError("未找到麦克风")
        return dev.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=block_size)


def list_devices():
    """返回 (loopback 设备列表, 麦克风列表) 供 GUI 选择。

    loopback 设备来自 all_microphones(include_loopback=True) 中 isloopback 的项。
    """
    all_mics = list(sc.all_microphones(include_loopback=True))
    loopbacks = [m for m in all_mics if getattr(m, "isloopback", False)]
    mics = [m for m in all_mics if not getattr(m, "isloopback", False)]
    return loopbacks, mics
# -*- coding: utf-8 -*-
"""能量 VAD(相对阈值):检测语句起止。

v4:
- 启动阶段:低百分位估计底噪,绝对阈值 = 底噪 * 2.5
- 语音段内:动态跟踪峰值,用"峰值*0.30"作相对结束阈值,
  避免底噪(静音时 RMS 0.03-0.05)干扰语句结束判定
- 去抖:speech_start 连续 3 块有声;静音持续 silence_end_s 结束
- 触顶 max_utterance_s 强制结束(超长分段)
"""
import collections
import numpy as np

import config as _cfg


class VAD:
    def __init__(self, sample_rate=24000, threshold=None,
                 silence_end_s=None, max_utterance_s=8.0,
                 hangover_start=3, window_s=3.0, rel_end_ratio=0.30):
        # 运行时读 config:让 GUI 改 cfg.VAD_THRESHOLD / SILENCE_END_SECONDS 生效
        if threshold is None:
            threshold = _cfg.VAD_THRESHOLD
        if silence_end_s is None:
            silence_end_s = _cfg.SILENCE_END_SECONDS
        self.sr = sample_rate
        self.base_threshold = threshold
        self.silence_end_s = silence_end_s
        self.max_utterance_s = max_utterance_s
        self.rel_end_ratio = rel_end_ratio

        self.noise_floor = threshold
        self.threshold = threshold

        self._win = collections.deque(maxlen=int(window_s / 0.2))
        self._start_hangover = hangover_start
        self._start_count = 0

        self.speaking = False
        self.silence_since = 0.0
        self.utterance_start = 0.0
        self._total_sec = 0.0
        self._peak = 0.0          # 语音段峰值(动态)
        self._end_threshold = 0.0  # 相对结束阈值

    def reset(self):
        self.speaking = False
        self.silence_since = 0.0
        self.utterance_start = 0.0
        self._total_sec = 0.0
        self._start_count = 0
        self._win.clear()
        self._peak = 0.0
        self._end_threshold = 0.0

    def _rms(self, block):
        b = np.asarray(block, dtype=np.float32)
        if b.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(b * b)))

    def _update_abs_threshold(self):
        if len(self._win) >= 5:
            self.noise_floor = float(np.percentile(list(self._win), 25))
            self.threshold = max(self.base_threshold, self.noise_floor * 2.5)

    def process(self, block, dt):
        self._total_sec += dt
        rms = self._rms(block)
        self._win.append(rms)

        if not self.speaking:
            self._update_abs_threshold()
            if rms > self.threshold:
                self._start_count += 1
                if self._start_count >= self._start_hangover:
                    self.speaking = True
                    self.silence_since = 0.0
                    self.utterance_start = self._total_sec
                    self._start_count = 0
                    self._peak = rms
                    self._end_threshold = rms * self.rel_end_ratio
                    return "speech_start"
            else:
                self._start_count = 0
            return None
        else:
            # 更新峰值与相对结束阈值(语音段)
            if rms > self._peak:
                self._peak = rms
                self._end_threshold = self._peak * self.rel_end_ratio
            # 结束判定:低于相对阈值 且 持续
            if rms < self._end_threshold:
                self.silence_since += dt
            else:
                self.silence_since = 0.0
            if self.silence_since >= self.silence_end_s:
                self.speaking = False
                self._win.clear()
                return "speech_end"
            if (self._total_sec - self.utterance_start) >= self.max_utterance_s:
                self.speaking = False
                self._win.clear()
                return "speech_end"
            return None

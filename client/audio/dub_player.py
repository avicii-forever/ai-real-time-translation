# -*- coding: utf-8 -*-
"""实时配音播放:把后端流式推回来的 TTS PCM 排队、连续播出。

后端在 duplex + use_tts=true 下会以 `response.output.delta(kind=audio)` 把
合成语音一块块推回来(实测约每 1.0s 一块,24kHz float32)。直接在收包回调里
播放会阻塞 WS 接收线程,所以这里用"队列 + 独立播放线程":

    player = DubPlayer()
    player.start()
    ws._on_audio = player.feed        # 收到就塞队列,立刻返回
    ...
    player.stop()

播放用 soundcard(项目里已有,采集也用它),不用 winsound —— winsound 是
文件级阻塞播放,做不到连续拼接。

关于"跟不上":实测 30s 英文原声 -> 22.5s 中文配音(中文更紧凑),配音天然
比原声短,所以正常情况下队列不会堆积。语速快时后端会突发式地推一大段音频,
`max_buffer_seconds`(默认 30s)是缓冲上限 —— 超过才丢最旧的块。之前设 8s
太紧,语速快时会被误丢、听感就是"配音被截断",故放宽到 30s(反正配音比
原声短,句间会自然排空,不会真的滞后 30s)。
"""
import queue
import threading
import time

import numpy as np

TTS_SAMPLE_RATE = 24000     # MiniCPM-o token2wav 输出;事件里不带采样率


class DubPlayer:
    def __init__(self, sample_rate=TTS_SAMPLE_RATE, device=None,
                 max_buffer_seconds=30.0, on_log=None):
        self.sample_rate = sample_rate
        self.device = device
        self.max_buffer_seconds = max_buffer_seconds
        self.on_log = on_log or (lambda s: None)

        self._q = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._queued_samples = 0     # 队列中待播样本数
        self.played_seconds = 0.0
        self.dropped_seconds = 0.0
        self.peak_buffer_seconds = 0.0
        # 注意:队列空**不等于**出问题。中文配音天然比原声短(实测 60s 英文 ->
        # 43.5s 中文,0.73x),所以句子之间本来就有空档,队列大部分时间就是空的。
        # 真正要盯的是 peak_buffer_seconds(涨起来才说明配音在掉队)。
        self.idle_polls = 0

    # ---- 生命周期 ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, drain=False, timeout=15):
        """drain=True 时等队列播完再停(收尾用),否则立即停。"""
        if drain:
            end = time.time() + timeout
            while self.buffered_seconds > 0.05 and time.time() < end:
                time.sleep(0.1)
        self._stop.set()
        self._q.put(None)            # 叫醒播放线程
        if self._thread:
            self._thread.join(timeout=5)

    # ---- 喂数据(WS 接收线程调用,必须立刻返回)----
    def feed(self, pcm):
        if self._stop.is_set() or pcm is None or len(pcm) == 0:
            return
        pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
        with self._lock:
            # 缓冲过深说明配音已经落后原声太多,丢最旧的保延迟
            while (self._queued_samples + pcm.size) / self.sample_rate > \
                    self.max_buffer_seconds:
                try:
                    old = self._q.get_nowait()
                except queue.Empty:
                    break
                if old is None:
                    continue
                self._queued_samples -= old.size
                self.dropped_seconds += old.size / self.sample_rate
                self.on_log(f"  配音缓冲超过 {self.max_buffer_seconds}s,丢弃 "
                            f"{old.size/self.sample_rate:.2f}s 保延迟")
            self._queued_samples += pcm.size
            self.peak_buffer_seconds = max(
                self.peak_buffer_seconds, self._queued_samples / self.sample_rate)
        self._q.put(pcm)

    @property
    def buffered_seconds(self):
        with self._lock:
            return self._queued_samples / self.sample_rate

    # ---- 播放线程 ----
    def _run(self):
        try:
            import soundcard as sc
        except Exception as e:
            self.on_log(f"配音播放不可用(soundcard 导入失败): {e}")
            return
        spk = self.device or sc.default_speaker()
        if spk is None:
            self.on_log("配音播放不可用:找不到扬声器")
            return
        self.on_log(f"配音输出设备: {spk.name}")
        try:
            with spk.player(samplerate=self.sample_rate, channels=1) as p:
                while not self._stop.is_set():
                    try:
                        chunk = self._q.get(timeout=0.3)
                    except queue.Empty:
                        self.idle_polls += 1
                        continue
                    if chunk is None:
                        break
                    with self._lock:
                        self._queued_samples -= chunk.size
                    p.play(np.clip(chunk, -1.0, 1.0))
                    self.played_seconds += chunk.size / self.sample_rate
        except Exception as e:
            self.on_log(f"配音播放异常: {e}")

    # ---- 统计 ----
    def stats(self):
        return (f"配音已播 {self.played_seconds:.1f}s"
                f",当前缓冲 {self.buffered_seconds:.1f}s"
                f",峰值缓冲 {self.peak_buffer_seconds:.1f}s"
                + (f",丢弃 {self.dropped_seconds:.1f}s" if self.dropped_seconds else ""))


class DubRecorder:
    """不播放,只把配音攒起来落成 wav(离线核对 / 压回视频用)。"""

    def __init__(self, sample_rate=TTS_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._chunks = []
        self._lock = threading.Lock()

    def feed(self, pcm):
        if pcm is None or len(pcm) == 0:
            return
        with self._lock:
            self._chunks.append(np.asarray(pcm, dtype=np.float32).reshape(-1))

    @property
    def duration(self):
        with self._lock:
            return sum(c.size for c in self._chunks) / self.sample_rate

    def save(self, path):
        import wave
        with self._lock:
            if not self._chunks:
                return None
            pcm = np.concatenate(self._chunks)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
        return path

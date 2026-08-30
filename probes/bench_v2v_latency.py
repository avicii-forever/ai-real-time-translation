# -*- coding: utf-8 -*-
"""语音转语音端到端延迟基准:量化每环节耗时。

测量:VAD说完 -> 文本显示 与 VAD说完 -> 语音播放 的延迟,及分解。
用法: python bench_v2v_latency.py --once 90
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))

import pipeline as pl
from pipeline import Pipeline
from audio.capture import LoopbackCapture
from config import SAMPLE_RATE

# 全局记录当前句时间点
STATE = {"utterance_start": None, "utterance_end": None}

# ---- 插桩:记录 VAD 句子起止 ----
_orig_process = pl.VAD.process
def timed_process(self, block, dt):
    evt = _orig_process(self, block, dt)
    if evt == "speech_start":
        STATE["utterance_start"] = time.time()
    elif evt == "speech_end":
        STATE["utterance_end"] = time.time()
    return evt
pl.VAD.process = timed_process

# ---- 插桩:omni_init / prefill / decode ----
from api.omni_client import OmniClient
_orig_omni_init = OmniClient.omni_init
_orig_prefill = OmniClient.prefill
_orig_decode = OmniClient.decode

def timed_omni_init(self, *a, **kw):
    t0 = time.time()
    r = _orig_omni_init(self, *a, **kw)
    print(f"    [环节] omni_init重置 {(time.time()-t0)*1000:.0f}ms")
    return r
def timed_prefill(self, *a, **kw):
    t0 = time.time()
    r = _orig_prefill(self, *a, **kw)
    print(f"      [环节] prefill {(time.time()-t0)*1000:.0f}ms")
    return r
def timed_decode(self, *a, **kw):
    t0 = time.time()
    r = _orig_decode(self, *a, **kw)
    print(f"      [环节] decode {(time.time()-t0)*1000:.0f}ms")
    return r
OmniClient.omni_init = timed_omni_init
OmniClient.prefill = timed_prefill
OmniClient.decode = timed_decode

# ---- 插桩:SFTP 上传 ----
from transport.sftp_uploader import SFTPUploader
_orig_upload = SFTPUploader.upload
def timed_upload(self, *a, **kw):
    t0 = time.time()
    r = _orig_upload(self, *a, **kw)
    print(f"      [环节] SFTP上传 {(time.time()-t0)*1000:.0f}ms")
    return r
SFTPUploader.upload = timed_upload

# ---- 插桩:TTS 播放(替换为计时桩)----
import audio.player as player_mod
def timed_play(files):
    t0 = time.time()
    # 实际播放(不真正出声,只计时)
    time.sleep(0.05)
    return True
player_mod.play_wav_files = timed_play
# 记录 TTS 拉取开始
_orig_play_tts = pl.Pipeline._play_tts
def timed_play_tts(self, round_idx):
    t0 = time.time()
    _orig_play_tts(self, round_idx)
    print(f"    [环节] TTS拉取+播放 {(time.time()-t0)*1000:.0f}ms (说完后 {(time.time()-STATE['utterance_end'])*1000:.0f}ms)")
pl.Pipeline._play_tts = timed_play_tts

# ---- 主测试 ----
import argparse
ap = argparse.ArgumentParser()
ap.add_argument("--once", type=float, default=0)
args = ap.parse_args()

pipe = Pipeline(
    source=LoopbackCapture(),
    on_status=lambda s: print(f"  [状态] {s}") if s in ("语句结束,翻译中",) else None,
    on_log=lambda s: None,  # 精简日志(已有插桩打印)
    on_translation=lambda t: print(f"  [译文] 说完后 {(time.time()-STATE['utterance_end'])*1000:.0f}ms: {t[:60]}"),
)
print("== V2V 延迟基准 ==")
print("  开始采集(播放中文音频)...")
pipe.start()

if args.once > 0:
    time.sleep(args.once)
    pipe.stop()
else:
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pipe.stop()
print("done")

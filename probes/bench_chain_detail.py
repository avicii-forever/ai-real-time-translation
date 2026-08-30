# -*- coding: utf-8 -*-
"""完整链路细节分解:精确计时 上传/prefill/decode/切片。"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))

import pipeline as pl
from pipeline import Pipeline
from audio.capture import LoopbackCapture
from config import SAMPLE_RATE

# 插桩:uploader.upload 计时
from transport.sftp_uploader import SFTPUploader
_orig_upload = SFTPUploader.upload
def timed_upload(self, wav_bytes, round_no=None, chunk_no=None):
    t0 = time.time()
    r = _orig_upload(self, wav_bytes, round_no, chunk_no)
    print(f"      [测] SFTP上传 {len(wav_bytes)/1024:.1f}KB = {(time.time()-t0)*1000:.0f}ms")
    return r
SFTPUploader.upload = timed_upload

# 插桩:omni_client.prefill / decode
from api.omni_client import OmniClient
_orig_prefill = OmniClient.prefill
_orig_decode = OmniClient.decode
def timed_prefill(self, audio_path, cnt, text=""):
    t0 = time.time()
    r = _orig_prefill(self, audio_path, cnt, text)
    print(f"      [测] prefill cnt={cnt} = {(time.time()-t0)*1000:.0f}ms")
    return r
def timed_decode(self, round_idx=-1, stream=True, timeout=240, on_content=None):
    t0 = time.time()
    r = _orig_decode(self, round_idx, stream, timeout, on_content)
    print(f"      [测] decode = {(time.time()-t0)*1000:.0f}ms")
    return r
OmniClient.prefill = timed_prefill
OmniClient.decode = timed_decode

pipe = Pipeline(source=LoopbackCapture(),
                on_log=lambda s: print(f"    {s}"))
print(">> 采集中(播放中文音频)...")
pipe.start()
time.sleep(60)
pipe.stop()

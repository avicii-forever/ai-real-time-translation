# -*- coding: utf-8 -*-
"""用 Windows SAPI 生成中文测试语音(16kHz mono wav)。"""
import sys, wave
import win32com.client

def sapi_tts(text, out_path, rate=0):
    v = win32com.client.Dispatch("SAPI.SpVoice")
    # 优先找一个中文语音
    tok = None
    for t in v.GetVoices():
        n = t.GetDescription()
        if "HuiHui" in n or "HUIFANG" in n.upper() or "huihui" in n.lower():
            tok = t; break
    if tok is None and v.GetVoices().Count > 0:
        tok = v.GetVoices().Item(0)
    if tok is not None:
        v.Voice = tok
    # 16kHz 16bit mono
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 6  # SAFT16kHz16BitMono
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format = fmt
    stream.Open(out_path, 3)  # SSFMCreateForWrite = 3
    v.AudioOutputStream = stream
    v.Rate = rate
    v.Speak(text)
    stream.Close()

if __name__ == "__main__":
    text = sys.argv[1]
    out = sys.argv[2]
    sapi_tts(text, out)
    w = wave.open(out)
    print(out, "rate=%d ch=%d frames=%d dur=%.1fs" % (w.getframerate(), w.getnchannels(), w.getnframes(), w.getnframes()/w.getframerate()))

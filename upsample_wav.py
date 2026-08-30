# -*- coding: utf-8 -*-
"""纯 Python 线性插值重采样 WAV(8k/16k -> 目标采样率),输出 16-bit mono wav。"""
import sys, wave, array

def resample(src, dst, target_rate=16000):
    w = wave.open(src)
    rate, nch, sw, nf = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    raw = w.readframes(nf)
    w.close()
    if sw != 2:
        raise ValueError("need 16-bit")
    if nch != 1:
        raise ValueError("need mono")
    samples = array.array('h', raw)
    if rate == target_rate:
        out = samples
    else:
        n_out = int(len(samples) * target_rate / rate)
        out = array.array('h', (0,)) * n_out
        for i in range(n_out):
            pos = i * rate / target_rate
            i0 = int(pos)
            i1 = min(i0 + 1, len(samples) - 1)
            frac = pos - i0
            out[i] = int(samples[i0] * (1 - frac) + samples[i1] * frac)
    o = wave.open(dst, 'wb')
    o.setnchannels(1); o.setsampwidth(2); o.setframerate(target_rate)
    o.writeframes(out.tobytes())
    o.close()

if __name__ == "__main__":
    resample(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 16000)
    w = wave.open(sys.argv[2])
    print(sys.argv[2], "rate=%d dur=%.1fs" % (w.getframerate(), w.getnframes()/w.getframerate()))

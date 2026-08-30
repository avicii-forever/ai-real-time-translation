# -*- coding: utf-8 -*-
"""WS duplex 单帧测试:init + 1 帧 24k 音频。"""
import base64, json, os, struct, time, wave, sys
import websocket

seg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audio_test", "ws_segs", "hsr_00.wav")
w = wave.open(seg); a=[s/32768.0 for s in struct.unpack(f"<{w.getnframes()}h", w.readframes(w.getnframes()))]; w.close()
b64 = base64.b64encode(struct.pack(f"<{len(a)}f", *a)).decode()  # 24k float32 PCM

ws = websocket.create_connection("ws://127.0.0.1:28099/backend", timeout=60)
ws.settimeout(60)
init = {"type":"session.init","payload":{"mode":"full_duplex","use_tts":False,
        "system_prompt":"请把用户输入的中文语音翻译成英文。",
        "config":{"media_type":1,"force_listen_count":0,"max_new_speak_tokens_per_chunk":128}}}
ws.send(json.dumps(init))
r = ws.recv()
print("init:", r[:120])

print("send 1 frame (24k, %.1fs)..." % (len(a)/24000))
ws.send(json.dumps({"type":"input.append","input":{"audio_base64":b64}}))
t0=time.time()
while time.time()-t0 < 60:
    try: r=ws.recv()
    except Exception as e: print("recv end:", e); break
    try: ev=json.loads(r)
    except Exception: continue
    t=ev.get("type","")
    print(f"  [{time.time()-t0:.1f}s] {t}", str(ev)[:150])
    if t in ("response.done","session.closed"): break
ws.close()

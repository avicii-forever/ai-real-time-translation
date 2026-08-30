# -*- coding: utf-8 -*-
"""最小 WS 测试:init + 1 帧,逐步打印卡点。"""
import base64, json, os, struct, sys, time, wave
import websocket
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))
from api.omni_client import OmniClient

# 先用 HTTP 确认后端活着
c = OmniClient()
r = c.omni_init(output_dir="/workspace/llama.cpp-omni/tools/omni/output_ws_min")
print("HTTP omni_init OK (复用此 session 可能影响 WS, 先不管)")

seg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audio_test", "ws_segs", "hsr_00.wav")
w = wave.open(seg); a=[s/32768.0 for s in struct.unpack(f"<{w.getnframes()}h", w.readframes(w.getnframes()))]; w.close()
b64 = base64.b64encode(struct.pack(f"<{len(a)}f", *a)).decode()  # 原始24k数据

ws = websocket.create_connection("ws://127.0.0.1:28099/backend", timeout=60)
ws.settimeout(60)
print("WS connected")
init = {"type":"session.init","payload":{"mode":"full_duplex","use_tts":False,
        "system_prompt":"请把用户输入的中文语音翻译成英文。",
        "config":{"media_type":1,"force_listen_count":0,"max_new_speak_tokens_per_chunk":64}}}
ws.send(json.dumps(init))
print("init sent, recv...")
t0=time.time()
try:
    r=ws.recv(); print(f"init resp ({time.time()-t0:.1f}s):", (r or "")[:150])
except Exception as e:
    print("init recv FAILED:", e); ws.close(); sys.exit(1)

print("sending 1 frame...")
ws.send(json.dumps({"type":"input.append","input":{"audio_base64":b64}}))
t0=time.time()
while time.time()-t0 < 60:
    try: r=ws.recv()
    except Exception as e: print("recv end:", e); break
    try: ev=json.loads(r)
    except Exception: continue
    t=ev.get("type","")
    print(f"  [{time.time()-t0:.1f}s] {t} {str(ev)[:120]}")
    if t in ("response.done","session.closed"): break
ws.close()
print("done")

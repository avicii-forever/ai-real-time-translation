# -*- coding: utf-8 -*-
"""英文语音 -> 中文 翻译验证(WS duplex)。"""
import base64, json, os, struct, sys, time, wave
import websocket
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))
import config as cfg

vc, ap = cfg.make_prompts("English", "中文")
print("提示词:", vc[:50].replace(chr(10), "|"), "...")
print("WS init system_prompt 用 voice_clone 前缀...")

# 读英文 24k wav,切成 1.5s 帧
seg = os.path.join("audio_test", "en_segs", "en_sample_24k.wav")
w = wave.open(seg); a = [s/32768.0 for s in struct.unpack(f"<{w.getnframes()}h", w.readframes(w.getnframes()))]; w.close()
frame_len = int(24000 * 1.5)
frames = [a[i:i+frame_len] for i in range(0, len(a), frame_len)]
print(f"音频 {len(a)/24000:.1f}s, 切 {len(frames)} 帧")

ws = websocket.create_connection("ws://127.0.0.1:28099/backend", timeout=120)
ws.settimeout(120)
# 注意:WS 只认 system_prompt(会进 voice_clone 前缀),assistant 用默认
init = {"type":"session.init","payload":{"mode":"full_duplex","use_tts":False,
        "system_prompt":vc,
        "config":{"media_type":1,"force_listen_count":0,"max_new_speak_tokens_per_chunk":512,
                  "listen_prob_scale":0.01}}}
ws.send(json.dumps(init))
print("init:", ws.recv()[:90])

all_text = []
for i, fr in enumerate(frames):
    b64 = base64.b64encode(struct.pack(f"<{len(fr)}f", *fr)).decode()
    ws.send(json.dumps({"type":"input.append","input":{"audio_base64":b64}}))
    t0=time.time()
    while time.time()-t0 < 15:
        try: r=ws.recv()
        except Exception: break
        try: ev=json.loads(r)
        except Exception: continue
        if ev.get("type")=="response.output.delta" and ev.get("kind")=="text":
            txt=ev.get("text",""); all_text.append(txt); print(f"  帧{i+1} [{time.time()-t0:.1f}s] {txt!r}")
        elif ev.get("type")=="response.done": break
    print(f"  帧{i+1} 完成")

# 空帧收尾
silence = base64.b64encode(struct.pack("<%df" % int(24000*0.5), *([0.0]*int(24000*0.5)))).decode()
ws.send(json.dumps({"type":"input.append","input":{"audio_base64":silence}}))
t0=time.time()
while time.time()-t0 < 15:
    try: r=ws.recv()
    except Exception: break
    try: ev=json.loads(r)
    except Exception: continue
    if ev.get("type")=="response.output.delta" and ev.get("kind")=="text":
        all_text.append(ev.get("text","")); print("  补尾:", ev.get("text",""))
    elif ev.get("type")=="response.done": break
ws.close()
print()
print("== 中文译文 ==")
print("".join(all_text).replace("_"," ").strip() or "(空)")
print()
print("英文原文: Good morning everyone. Today I want to talk about the importance of learning new languages.")

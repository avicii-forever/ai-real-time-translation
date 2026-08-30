# -*- coding: utf-8 -*-
"""WS full_duplex 翻译验证 v4:24k 音频 + 更长超时。"""
import base64, json, os, struct, sys, time, wave
import websocket

BASE_WS = "ws://127.0.0.1:28099/backend"
SEG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audio_test", "ws_segs")
SEGS = [os.path.join(SEG_DIR, f"hsr_{i:02d}.wav") for i in range(6)]

SYSTEM_PROMPT = ("请把用户输入的中文语音逐句翻译成英文,只输出英文译文本身,"
                 "不要添加任何解释、注释或额外内容。")

def wav_to_f32_b64(path, rate):
    w = wave.open(path)
    src_rate, nch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
    raw = w.readframes(w.getnframes()); w.close()
    a = [s/32768.0 for s in struct.unpack(f"<{len(raw)//2}h", raw)]
    if nch>1: a=[sum(a[i*nch:(i+1)*nch])/nch for i in range(len(a)//nch)]
    if src_rate != rate:
        n_out=int(len(a)*rate/src_rate); out=[]
        for i in range(n_out):
            pos=i*src_rate/rate; i0=int(pos); i1=min(i0+1,len(a)-1); fr=pos-i0
            out.append(a[i0]*(1-fr)+a[i1]*fr)
        a=out
    return base64.b64encode(struct.pack(f"<{len(a)}f",*a)).decode()

def main():
    print("== WS duplex v4 (24k) ==")
    ws = websocket.create_connection(BASE_WS, timeout=45)
    ws.settimeout(45)
    init = {"type":"session.init","payload":{"mode":"full_duplex","use_tts":False,
            "system_prompt":SYSTEM_PROMPT,
            "config":{"media_type":1,"force_listen_count":0,"max_new_speak_tokens_per_chunk":64}}}
    ws.send(json.dumps(init))
    t0=time.time()
    try:
        r=ws.recv(); print("init:", (r or "")[:160])
    except Exception as e:
        print("init recv err:", e); return
    print(f"init latency {time.time()-t0:.1f}s")

    for i, seg in enumerate(SEGS):
        b64 = wav_to_f32_b64(seg, 24000)
        ws.send(json.dumps({"type":"input.append","input":{"audio_base64":b64}}))
        print(f"[发帧{i+1}]", os.path.basename(seg))

    all_text=[]; t0=time.time(); got=0
    while time.time()-t0 < 120:
        try:
            r=ws.recv()
        except Exception as e:
            print("recv end:", e); break
        try: ev=json.loads(r)
        except Exception: continue
        t=ev.get("type","")
        if t=="response.output.delta":
            k=ev.get("output",{}).get("delta",{}).get("kind","")
            if k=="text":
                txt=ev["output"]["delta"].get("text",""); print(f"  [text] {txt!r}"); all_text.append(txt); got+=1
            elif k=="listen":
                print("  [listen]")
        elif t=="response.done":
            ft=ev.get("output",{}).get("full_text","")
            if ft: all_text.append(ft)
            print("  [done]", ft[:70]); got+=1
        elif t=="session.closed":
            print("  [closed]", ev.get("reason","")); break
    ws.close()
    print(f"\n== 事件计数: {got} ==")
    print("译文:", "".join(all_text).strip() or "(空)")

if __name__=="__main__":
    main()

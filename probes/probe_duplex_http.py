# -*- coding: utf-8 -*-
"""HTTP duplex_mode 验证:omni_init(duplex=true,翻译提示词) -> prefill -> decode。"""
import json, time, urllib.request

BASE = "http://127.0.0.1:28099"
OUT = "/workspace/llama.cpp-omni/tools/omni/output_duplex_http"
VC = '<|im_start|>system\n你是一个实时语音翻译助手。请把用户输入的中文语音逐句翻译成英文,只输出英文译文本身,不要添加任何解释、注释或额外内容。\n<|audio_start|>'
AS = '<|audio_end|>请把上面的语音翻译成英文。<|im_end|>\n<|im_start|>user\n'
SEGS = [f"/workspace/llama.cpp-omni/tools/omni/assets/my_test/segments/hsr_{i:02d}.wav" for i in range(6)]

def post(p, b, timeout=180):
    req = urllib.request.Request(BASE+p, data=json.dumps(b).encode(), headers={'Content-Type':'application/json'}, method='POST')
    return urllib.request.urlopen(req, timeout=timeout).read().decode(errors='replace')

t0=time.time()
r = post('/v1/stream/omni_init', {'media_type':1,'use_tts':False,'duplex_mode':True,'output_dir':OUT,'voice_clone_prompt':VC,'assistant_prompt':AS})
print(f"omni_init(duplex=true) -> {r} ({time.time()-t0:.1f}s)")
r = post('/v1/stream/prefill', {'audio_path_prefix':'','cnt':0})
print(f"prefill cnt=0 -> {r}")
for i, s in enumerate(SEGS):
    t1=time.time()
    r = post('/v1/stream/prefill', {'audio_path_prefix':s,'cnt':i+1})
    print(f"prefill cnt={i+1} ({time.time()-t1:.2f}s) -> {r}")
t2=time.time()
# decode SSE
body={'stream':True,'debug_dir':OUT}
req = urllib.request.Request(BASE+'/v1/stream/decode', data=json.dumps(body).encode(), headers={'Content-Type':'application/json'}, method='POST')
parts=[]
with urllib.request.urlopen(req, timeout=240) as resp:
    for raw in resp:
        line=raw.decode(errors='replace').strip()
        if not line.startswith('data:'): continue
        p=line[5:].strip()
        if p=='[DONE]': break
        try: d=json.loads(p)
        except: continue
        if d.get('content'): parts.append(d['content'])
        if d.get('end_of_turn'): break
print(f"decode {time.time()-t2:.1f}s")
print("译文:", ''.join(parts).strip() or '(空)')

# -*- coding: utf-8 -*-
"""edge-tts 高铁票句切成 1.5s 小段(6段) -> 逐段 prefill -> decode。判断段长是否是决定因素。"""
import json, time, urllib.request

BASE = "http://127.0.0.1:28099"
OUT_DIR = "/workspace/llama.cpp-omni/tools/omni/output_probe_seg"
SEGS = [f"/workspace/llama.cpp-omni/tools/omni/assets/my_test/segments/hsr_{i:02d}.wav" for i in range(6)]

VOICE_CLONE_PROMPT = (
    "<|im_start|>system\n"
    "你是一个实时语音翻译助手。请把用户输入的中文语音逐句翻译成英文,"
    "只输出英文译文本身,不要添加任何解释、注释或额外内容。\n"
    "<|audio_start|>"
)
ASSISTANT_PROMPT = "<|audio_end|>请把上面的语音翻译成英文。<|im_end|>\n<|im_start|>user\n"

def post(path, payload, timeout=180):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(errors="replace")

def main():
    print("== edge-tts 高铁票句, 切6段1.5s -> 逐段prefill -> decode ==")
    r = post("/v1/stream/omni_init", {"media_type":1, "use_tts":False, "output_dir":OUT_DIR,
        "voice_clone_prompt":VOICE_CLONE_PROMPT, "assistant_prompt":ASSISTANT_PROMPT})
    print(f"[1] omni_init -> {r}")
    r = post("/v1/stream/prefill", {"audio_path_prefix":"", "cnt":0})
    print(f"[2] prefill cnt=0 -> {r}")
    for i, w in enumerate(SEGS):
        t0 = time.time()
        r = post("/v1/stream/prefill", {"audio_path_prefix":w, "cnt":i+1})
        print(f"[2] prefill cnt={i+1} {w.split('/')[-1]} -> {r} ({time.time()-t0:.1f}s)")

    print("[3] decode (SSE) ...")
    body = {"stream": True, "debug_dir": OUT_DIR}
    req = urllib.request.Request(BASE + "/v1/stream/decode", data=json.dumps(body).encode(),
                                 headers={"Content-Type":"application/json"}, method="POST")
    parts = []
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=240) as resp:
        for raw in resp:
            line = raw.decode(errors="replace").strip()
            if not line.startswith("data:"): continue
            p = line[5:].strip()
            if p == "[DONE]": break
            try: d = json.loads(p)
            except Exception: continue
            if d.get("content"): parts.append(d["content"])
            if d.get("end_of_turn"): break
    text = "".join(parts)
    print(f"[4] elapsed={time.time()-t0:.1f}s")
    print()
    print("== 英文译文 ==")
    print(text.strip() if text.strip() else "(空返回)")
    print()
    print("中文原文:")
    print("  请帮我预订明天早上从上海到北京的高铁票,我想乘坐早上八点那趟列车出发。")

if __name__ == "__main__":
    main()

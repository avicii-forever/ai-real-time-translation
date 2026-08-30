#!/usr/bin/env python3
"""CLI 翻译探针 —— 验证 omni 后端「音频 → 翻译文本」链路与翻译质量。

用法:
    python probe_translate.py                       # 用服务端自带测试音频(内容未知,只验证链路)
    python probe_translate.py <本地wav路径>          # 上传本地音频并翻译(验证真实质量)

后端: llama-omni-server(OpenLibing Ascend 910B),经 SSH 隧道暴露在 127.0.0.1:28099。
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:28099"

# 服务端自带测试资产(内容未知,只能验证链路是否通,不能判断翻译对不对)
SERVER_TEST_ASSETS = (
    "/workspace/llama.cpp-omni/tools/omni/assets/test_case/audio_test_case/audio_test_case_0000.wav",
    "/workspace/llama.cpp-omni/tools/omni/assets/test_case/audio_test_case/audio_test_case_0001.wav",
)

# 翻译提示词(media_type=1 音频路径,必须以 "<|" 开头,否则音频 anchor 丢失)
VOICE_CLONE_PROMPT = (
    "<|im_start|>system\n"
    "你是一个实时语音翻译助手。请把用户输入的中文语音逐句翻译成英文,"
    "只输出英文译文本身,不要添加任何解释、注释或额外内容。\n"
    "<|audio_start|>"
)
ASSISTANT_PROMPT = "<|audio_end|>请把上面的语音翻译成英文。<|im_end|>\n<|im_start|>user\n"


def post(path, payload, timeout=120):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(errors="replace")


def omni_init(output_dir):
    print("[1/3] omni_init (media_type=1, use_tts=false) ...")
    body = {
        "media_type": 1,
        "use_tts": False,
        "output_dir": output_dir,
        "voice_clone_prompt": VOICE_CLONE_PROMPT,
        "assistant_prompt": ASSISTANT_PROMPT,
    }
    t0 = time.time()
    resp = post("/v1/stream/omni_init", body)
    print(f"      -> {resp}  ({time.time()-t0:.1f}s)")
    return resp


def prefill(audio_path, cnt, text=""):
    body = {"audio_path_prefix": audio_path, "cnt": cnt, "text": text}
    resp = post("/v1/stream/prefill", body)
    return resp


def decode(debug_dir):
    body = {"stream": True, "debug_dir": debug_dir}
    req = urllib.request.Request(
        BASE + "/v1/stream/decode",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    parts = []
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        for raw in r:
            line = raw.decode(errors="replace").strip()
            if not line.startswith("data:"):
                continue
            p = line[5:].strip()
            if p == "[DONE]":
                break
            try:
                d = json.loads(p)
            except Exception:
                continue
            if d.get("content"):
                parts.append(d["content"])
            if d.get("end_of_turn"):
                break
    return "".join(parts), time.time() - t0


def main():
    output_dir = "/workspace/llama.cpp-omni/tools/omni/output_probe"

    # 确定音频源
    if len(sys.argv) > 1:
        local = sys.argv[1]
        # TODO: 本地文件需要 SFTP 上传(Phase 2 的 remote_client)。当前先只支持服务端资产。
        print(f"本地文件上传尚未实现(Phase 2),请先用服务端测试资产验证链路。")
        print(f"当前命令请改成不带参数运行。")
        sys.exit(2)

    print("== 实时翻译链路探针 ==")
    omni_init(output_dir)

    print("[2/3] prefill 音频 chunks ...")
    # cnt=0: 系统提示词初始化(不含用户音频);用户音频从 cnt>=1 开始
    print(f"      prefill cnt=0 (system init) -> {prefill('', 0)}")
    for i, path in enumerate(SERVER_TEST_ASSETS):
        cnt = i + 1
        t0 = time.time()
        resp = prefill(path, cnt)
        print(f"      prefill cnt={cnt} {path.split('/')[-1]} -> {resp}  ({time.time()-t0:.1f}s)")

    print("[3/3] decode (SSE) ...")
    text, elapsed = decode(output_dir)
    print(f"      elapsed={elapsed:.1f}s")
    print()
    print("== 译文 ==")
    print(text.strip() if text.strip() else "(空返回)")
    print()


if __name__ == "__main__":
    main()

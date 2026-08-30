# -*- coding: utf-8 -*-
"""假的 /backend WS duplex 服务 —— 只为在后端节点不可达时验证客户端管线。

复刻真后端的事件流:
  收 session.init            -> 回 session.created
  收 input.append(每帧音频)  -> 回若干 response.output.delta(kind=text)
                                最后回一个 response.done(text=本轮累计)

它不做真翻译,只按帧数吐预置的中文片段(带下划线转义,模仿真后端风格),
用来验证:帧节奏、流式回调、文本层断句、字幕落盘、断线重连。

用法:
    python probes/mock_ws_duplex.py --port 28099
"""
import argparse
import asyncio
import json

import websockets

# 模仿真后端:空格用下划线转义
FRAGMENTS = [
    "今_天", "我_们_来_讲", "GPU", "和", "TPU", "的_区_别。",
    "首_先", "看_一_下", "内_存_带_宽", "这_个_指_标。",
    "矩_阵_乘_法", "是_最_主_要_的", "计_算_负_载,",
    "所_以", "我_们_需_要", "关_注", "算_力_利_用_率。",
    "接_下_来", "讨_论", "张_量_核_心", "如_何_工_作。",
]


async def handler(ws):
    frame_idx = 0
    turn_text = []
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        t = msg.get("type")

        if t == "session.init":
            print(f"[mock] session.init  prompt="
                  f"{msg.get('payload', {}).get('system_prompt', '')[:60]!r}")
            await ws.send(json.dumps({"type": "session.created",
                                      "session_id": "mock-1"}))
            frame_idx = 0
            turn_text = []

        elif t == "input.append":
            # 每帧吐 1~2 个片段,模拟边听边译
            n = 1 if frame_idx % 3 else 2
            for _ in range(n):
                frag = FRAGMENTS[frame_idx % len(FRAGMENTS)]
                frame_idx += 1
                turn_text.append(frag)
                await ws.send(json.dumps({
                    "type": "response.output.delta",
                    "kind": "text",
                    "text": frag,
                }, ensure_ascii=False))
                await asyncio.sleep(0.15)   # 模拟解码耗时
            await ws.send(json.dumps({
                "type": "response.done",
                "text": "".join(turn_text),
            }, ensure_ascii=False))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=28099)
    args = ap.parse_args()
    print(f"[mock] listening ws://127.0.0.1:{args.port}/backend")
    async with websockets.serve(handler, "127.0.0.1", args.port,
                                max_size=None):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

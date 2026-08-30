# -*- coding: utf-8 -*-
"""最小 WS duplex session.init 探针:量冷加载 + stream_prefill 到底要多久,
确认 session.created 会不会回(之前 300s 测试卡死,疑似 stream_prefill 挂住)。

只做 WS,不做 HTTP omni_init(避免抢单 session)。

用法:
    cd client && PYTHONIOENCODING=utf-8 python ../probes/probe_init_timing.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))

import websocket
import config as cfg


def main():
    url = f"ws://{cfg.BACKEND_HOST}:{cfg.BACKEND_PORT}/backend"
    print(f"连接 {url} ...", flush=True)
    ws = websocket.create_connection(url, timeout=cfg.WS_CONNECT_TIMEOUT)
    ws.settimeout(cfg.WS_CONNECT_TIMEOUT)
    print("WS 握手完成,发 session.init ...", flush=True)

    init = {
        "type": "session.init",
        "payload": {
            "mode": "full_duplex",
            "use_tts": False,
            "system_prompt": cfg.VOICE_CLONE_PROMPT,
            "config": {
                "media_type": 1,
                "force_listen_count": 0,
                "max_new_speak_tokens_per_chunk": 512,
                "listen_prob_scale": cfg.LISTEN_PROB_SCALE,
            },
        },
    }
    ws.send(json.dumps(init))
    t0 = time.time()
    print(f"init 已发,等 session.created(超时 {cfg.WS_CONNECT_TIMEOUT}s)...", flush=True)

    # 持续打印中间事件,标出每个事件到的时间
    got_created = False
    while time.time() - t0 < cfg.WS_CONNECT_TIMEOUT:
        try:
            r = ws.recv()
        except Exception as e:
            print(f"[{time.time() - t0:6.1f}s] recv 异常: {type(e).__name__}: {e}", flush=True)
            break
        try:
            ev = json.loads(r)
        except Exception:
            continue
        t = ev.get("type", "")
        print(f"[{time.time() - t0:6.1f}s] <{t}> {str(ev)[:120]}", flush=True)
        if t == "session.created":
            got_created = True
            break
        if t == "session.closed":
            break

    if got_created:
        print(f"\n✅ session.created 收到,耗时 {time.time() - t0:.1f}s", flush=True)
    else:
        print(f"\n❌ {cfg.WS_CONNECT_TIMEOUT}s 内未收到 session.created", flush=True)
    ws.close()


if __name__ == "__main__":
    main()

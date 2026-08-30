# -*- coding: utf-8 -*-
"""后端 REST 封装:omni_init / prefill / decode(SSE)。

后端暴露在 BACKEND_BASE(SSH 隧道转发 127.0.0.1:28099)。
"""
import json
import time
import urllib.request
import urllib.error

from config import (
    BACKEND_BASE, VOICE_CLONE_PROMPT, ASSISTANT_PROMPT,
    REMOTE_OUTPUT_DIR, USE_TTS,
)


class OmniError(Exception):
    pass


class OmniClient:
    def __init__(self, base=BACKEND_BASE, timeout=120):
        self.base = base
        self.timeout = timeout
        self._sess_output_dir = REMOTE_OUTPUT_DIR

    # ---- 底层 ----
    def _post(self, path, payload, timeout=None, raw=False):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                body = r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace") if hasattr(e, "read") else str(e)
            raise OmniError(f"HTTP {e.code}: {detail}")
        except (urllib.error.URLError, OSError) as e:
            raise OmniError(f"后端连接失败({self.base}): {e} —— 请确认 SSH 隧道已建立")
        return body

    # ---- API ----
    def omni_init(self, media_type=1, use_tts=None, output_dir=None):
        """初始化 omni 会话(首次 ~3-5s 加载模型)。可重复调用以重置会话。

        use_tts 默认取 config.USE_TTS(翻译后合成英文语音)。
        """
        if use_tts is None:
            use_tts = USE_TTS
        self._sess_output_dir = output_dir or self._sess_output_dir
        body = {
            "media_type": media_type,
            "use_tts": use_tts,
            "output_dir": self._sess_output_dir,
            "voice_clone_prompt": VOICE_CLONE_PROMPT,
            "assistant_prompt": ASSISTANT_PROMPT,
        }
        resp = self._post("/v1/stream/omni_init", body, timeout=180)
        try:
            d = json.loads(resp)
        except Exception:
            raise OmniError(f"omni_init 响应解析失败: {resp[:200]}")
        if not d.get("success"):
            raise OmniError(f"omni_init 失败: {resp[:200]}")
        return d

    def prefill(self, audio_path, cnt, text=""):
        """灌入一段音频(服务端 WAV 路径)。cnt=0 系统初始化;用户音频 cnt>=1。"""
        body = {"audio_path_prefix": audio_path, "cnt": cnt, "text": text}
        resp = self._post("/v1/stream/prefill", body, timeout=120)
        try:
            d = json.loads(resp)
        except Exception:
            raise OmniError(f"prefill 响应解析失败: {resp[:200]}")
        if not d.get("success"):
            raise OmniError(f"prefill 失败: {resp[:200]}")
        return d

    def decode(self, round_idx=-1, stream=True, timeout=240, on_content=None):
        """SSE 解码,返回完整译文。on_content 可选回调增量接收 content。"""
        body = {"stream": stream, "debug_dir": self._sess_output_dir, "round_idx": round_idx}
        req = urllib.request.Request(
            self.base + "/v1/stream/decode",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        parts = []
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
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
                        if on_content:
                            on_content(d["content"])
                    if d.get("end_of_turn"):
                        break
        except urllib.error.URLError as e:
            raise OmniError(f"decode 连接失败: {e}")
        return "".join(parts), time.time() - t0

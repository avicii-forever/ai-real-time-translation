# -*- coding: utf-8 -*-
"""WS full_duplex 翻译客户端。

后端已 patch(24k / 提示词注入 / hard-listen + 连续生成),可流式翻译:
- 采集线程:每帧音频 -> base64 float32 PCM -> input.append
- 接收线程:收 text_delta 累积,response.done 结束

用法(配合采集):
    wc = WSDuplexClient(system_prompt=...)
    wc.connect()
    wc.push_audio(float32_samples)   # 每帧
    wc.push_silence()                # 语句结束触发收尾
    text = wc.finish_turn()          # 收完整译文
"""
import base64
import json
import struct
import threading
import time

import numpy as np
import websocket

from config import (BACKEND_HOST, BACKEND_PORT, WS_CONNECT_TIMEOUT,
                    DUPLEX_SLIDE_KEEP_TOKENS, DUPLEX_SLIDE_TRIGGER,
                    LISTEN_PROB_SCALE)


def decode_pcm_b64(b64):
    """后端 TTS 音频事件的载荷:base64 的 float32 小端 PCM -> np.float32 1-D。"""
    if not b64:
        return np.empty(0, dtype=np.float32)
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return np.empty(0, dtype=np.float32)
    return np.frombuffer(raw[:len(raw) - len(raw) % 4], dtype="<f4")


class WSDuplexClient:
    def __init__(self, system_prompt=None, listen_prob_scale=None,
                 force_listen_count=0, max_chunk_tokens=512,
                 temperature=None, top_p=None, top_k=None, use_tts=False,
                 slide_keep_tokens=None, slide_trigger=None):
        self.url = f"ws://{BACKEND_HOST}:{BACKEND_PORT}/backend"
        self.system_prompt = system_prompt or (
            "你是一个实时语音翻译助手。请把用户输入的中文语音翻译成英文,"
            "只输出英文译文本身,不要添加任何解释、注释或额外内容。"
        )
        # listen_prob_scale:None = 用 config.LISTEN_PROB_SCALE(默认 0.5)。
        # <=0.05 会触发后端 hard-listen —— 别用,见 config.LISTEN_PROB_SCALE 注释。
        self.listen_prob_scale = (LISTEN_PROB_SCALE
                                  if listen_prob_scale is None else listen_prob_scale)
        self.force_listen_count = force_listen_count
        self.max_chunk_tokens = max_chunk_tokens
        # 采样参数(后端 ws_handler 支持 temperature/top_p/top_k 三个;
        # repeat_penalty 只能在服务端命令行给,见 start_server.sh)
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        # use_tts=True 时后端会把合成语音以 response.output.delta(kind=audio)
        # 流式推回来(base64 float32 PCM,24kHz)。注意:切换 use_tts 会让后端
        # 无法复用 shared_octx(要求 duplex/use_tts 都匹配),等于一次冷加载。
        self.use_tts = use_tts
        # duplex KV 滑窗(需要后端 duplex_slide_config 补丁;老后端会忽略这两个键)
        self.slide_keep_tokens = (DUPLEX_SLIDE_KEEP_TOKENS
                                  if slide_keep_tokens is None else slide_keep_tokens)
        self.slide_trigger = (DUPLEX_SLIDE_TRIGGER
                              if slide_trigger is None else slide_trigger)

        self._ws = None
        self._lock = threading.Lock()
        self._texts = []
        self._turn_done = threading.Event()
        self._session_ok = threading.Event()
        self._disconnected = threading.Event()  # 连接是否已断开(供 pipeline 检测重连)

        # 接收线程
        self._recv_thread = None
        self._running = False
        self._on_text = None       # 流式回调(text 片段)
        self._on_audio = None      # 配音回调:收到一段 TTS PCM(np.float32 1-D)
        self._on_event = None      # 原始事件回调(调试用)
        self._last_done_text = ""

    # ---- 生命周期 ----
    def connect(self, timeout=None):
        """建立 WS 连接 + session.init。会先关闭旧连接并复位状态(支持重连)。

        timeout 要足够长:后端服务重启后第一次 session.init 会**冷加载 19.7GB 模型**,
        实测要 ~83s(之前默认 60s 会超时,而且超时后连接没关,把后端单 session
        占死,后续全部 "session.init rejected — active session exists")。
        """
        if timeout is None:
            timeout = WS_CONNECT_TIMEOUT
        self.close()
        self._texts = []
        self._last_done_text = ""
        self._turn_done.clear()
        try:
            self._ws = websocket.create_connection(self.url, timeout=timeout)
            self._ws.settimeout(timeout)
            conf = {
                "media_type": 1,
                "force_listen_count": self.force_listen_count,
                "max_new_speak_tokens_per_chunk": self.max_chunk_tokens,
                "listen_prob_scale": self.listen_prob_scale,
            }
            for k, v in (("temperature", self.temperature),
                         ("top_p", self.top_p), ("top_k", self.top_k)):
                if v is not None:
                    conf[k] = v
            if self.slide_keep_tokens:
                conf["duplex_slide_keep_tokens"] = int(self.slide_keep_tokens)
            if self.slide_trigger:
                conf["duplex_slide_trigger"] = int(self.slide_trigger)
            init = {"type": "session.init", "payload": {
                "mode": "full_duplex",
                "use_tts": self.use_tts,
                "system_prompt": self.system_prompt,
                "config": conf,
            }}
            with self._lock:
                self._ws.send(json.dumps(init))
            # 等 session.created
            r = self._ws.recv()
        except Exception:
            # 必须先关掉半开的连接再置 None:否则后端那个单 session 一直被占,
            # 之后所有 init 都会被 "active session exists" 拒掉(踩过)。
            self._force_close()
            self._mark_disconnected()
            raise
        if "session.created" not in r:
            self._force_close()
            self._mark_disconnected()
            raise RuntimeError(f"session.init failed: {r[:200] or '(空响应,连接被后端关闭)'}")
        self._running = True
        self._disconnected.clear()
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        return self

    def close(self):
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _force_close(self):
        """关掉底层 socket 并置空 —— 确保后端释放它那唯一的 session。"""
        self._running = False
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            try:
                self._ws.sock and self._ws.sock.close()
            except Exception:
                pass
        self._ws = None

    # ---- 发送 ----
    def push_audio(self, float32_samples, sample_rate=24000):
        """推一帧音频(float32 1-D)。后端按 24k 写 wav(已 patch)。"""
        if len(float32_samples) == 0:
            return
        b64 = base64.b64encode(
            struct.pack(f"<{len(float32_samples)}f", *float32_samples)
        ).decode()
        self._send({"type": "input.append", "input": {"audio_base64": b64}})

    def push_silence(self, seconds=0.5):
        """推静音帧,触发模型收尾(把最后的译文补完)。"""
        n = int(24000 * seconds)
        self.push_audio([0.0] * n)

    def _send(self, msg):
        try:
            with self._lock:
                if self._ws is None:
                    raise RuntimeError("WS not connected")
                self._ws.send(json.dumps(msg))
        except Exception:
            self._mark_disconnected()
            raise

    # ---- 接收 ----
    def _recv_loop(self):
        while self._running:
            try:
                r = self._ws.recv()
            except Exception:
                self._running = False
                self._disconnected.set()
                break
            try:
                ev = json.loads(r)
            except Exception:
                continue
            t = ev.get("type", "")
            if self._on_event:
                try:
                    self._on_event(ev)
                except Exception:
                    pass
            if t == "response.output.delta" and ev.get("kind") == "text":
                txt = ev.get("text", "")
                if txt:
                    self._texts.append(txt)
                    self._last_done_text = "".join(self._texts)
                    if self._on_text:
                        self._on_text(txt)
            elif t == "response.output.delta" and ev.get("kind") == "audio":
                if self._on_audio:
                    pcm = decode_pcm_b64(ev.get("audio") or "")
                    if pcm.size:
                        self._on_audio(pcm)
            elif t == "response.done":
                full = ev.get("text", "")
                # done 的 full_text 是完整译文,用它覆盖而非追加(避免与 deltas 重复)
                if full:
                    self._texts = [full]
                self._last_done_text = "".join(self._texts)
                self._turn_done.set()
            elif t == "session.closed":
                self._running = False
                break

    # ---- 轮次 ----
    def reset_turn(self):
        """新一句开始:清空累积文本,复位完成标志。"""
        with self._lock:
            self._texts = []
            self._last_done_text = ""
            self._turn_done.clear()

    def wait_turn_done(self, timeout=30):
        """等本帧/本句的 response.done。返回累积译文。

        断线时提前返回(不等满 timeout),让 pipeline 尽快进入重连。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._turn_done.wait(0.2):
                break
            if self._disconnected.is_set():
                break
        return self._last_done_text

    # ---- 断线检测 ----
    @property
    def disconnected(self):
        return self._disconnected.is_set()

    def _mark_disconnected(self):
        self._running = False
        self._disconnected.set()

    @property
    def current_text(self):
        return "".join(self._texts)

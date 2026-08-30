# -*- coding: utf-8 -*-
"""客户端全局配置。"""
import os

# ---- 后端(经 SSH 隧道暴露在本机)----
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 28099
BACKEND_BASE = f"http://{BACKEND_HOST}:{BACKEND_PORT}"

# ---- SSH 隧道别名(打包 exe 后"一键连接"用)----
SSH_ALIAS = "<SSH别名>"

# ---- duplex KV 滑窗(需要后端 patches/duplex_slide_config.py 补丁)----
# ⚠️ 默认必须是 0/0 —— 实测调这两个参数只会让情况更糟:
#   keep=768 trigger=0    -> force slide 一刀砍 1681 token,80s 后模型崩
#   keep=768 trigger=1280 -> 每 9s 在生成中途盲切,123s 后输出 "ABAABAAB" 乱码
# 根因是 hard-listen 下 rounds 恒为 0(整个会话是一个永不结束的 turn),
# 滑窗只能盲切尾部并整体左移位置,切多切勤都会破坏 KV 对齐。
# 0 = 用后端默认行为(与未打补丁的原版逐位一致)。
DUPLEX_SLIDE_KEEP_TOKENS = 0
DUPLEX_SLIDE_TRIGGER = 0

# ---- WS 连接超时 ----
# 服务重启后第一次 session.init 要冷加载 19.7GB 模型:热盘 ~83s,
# **整机重启后冷盘实测 ~285s**(8/27)。超时会把半开连接留给后端的单 session,
# 之后全部 init 被 "active session exists" 拒掉,所以宁可等长一点。
WS_CONNECT_TIMEOUT = 320
# 热连接超时:模型已常驻(shared_octx 复用)时 init 只要 ~5-8s。
# 滚动回收/断线重连走这个短超时 —— 真卡住时快速失败重试,
# 而不是让实时界面静默等 320s。
WS_RECONNECT_TIMEOUT = 60

# ---- 音频参数(后端硬约束:24kHz + 1-2s 切片)----
SAMPLE_RATE = 24000       # 采集/切片目标采样率(Hz)
SLICE_SECONDS = 1.5       # 单块切片时长
SILENCE_END_SECONDS = 1.5 # 静音多少秒判定语句结束
MAX_UTTERANCE_SECONDS = 8  # 单句最长(VAD 触顶时分段,防超长退化)
VAD_THRESHOLD = 0.012     # 能量阈值(自适应时会调整)

# ---- 远端 SFTP 路径 ----
REMOTE_PROJECT = "/workspace/llama.cpp-omni"
REMOTE_ASSET_DIR = f"{REMOTE_PROJECT}/tools/omni/assets/my_test"
REMOTE_OUTPUT_DIR = f"{REMOTE_PROJECT}/tools/omni/output_client"
# 固定文件名:每轮覆盖
REMOTE_LIVE_PREFIX = "live"

# ---- 嵌套 SSH 配置(与 remote_config.json 一致)----
SSH_CONFIG = {
    "gateway": {
        "host": "<网关IP>",
        "port": 2222,
        "user": "jump",
        "identity_file": os.path.expanduser("~/.ssh/id_rsa"),
    },
    "node": {
        # 注意:OpenLibing 容器重启会重新分配 IP(8/26 .56.217→.55.165,8/27 →.45.55.250,又 →.44.57.82),
        # 以 ~/.ssh/config 里 SSH_ALIAS 那条为准,这里只给 SFTP 直连用。
        "host": "<节点IP>",
        "port": 22,
        "user": "root",
        "identity_file": os.path.expanduser("~/.ssh/id_rsa"),
    },
}

# ---- TTS ----
USE_TTS = True            # 翻译后合成英文语音并播放
TTS_PLAY = True           # 本地播放 TTS 语音(False 只显示文本)
REMOTE_TTS_DIR_FMT = REMOTE_OUTPUT_DIR + "/round_{:03d}/tts_wav"  # 服务端 TTS wav 目录
TTS_DONE_FLAG = "generation_done.flag"
TTS_WAIT_MAX = 60         # 等 TTS 落盘的最长秒数

# ---- 翻译提示词(语言对可配置)----
# 注意:不要加"不加前缀/不要客套"这类负面强调,实测会导致模型病态重复("Please\n\nPlease");
# 简版("只输出译文本身")输出最干净。
#
# 另见 LISTEN_PROB_SCALE:0.01(hard-listen)曾让模型"永远在说、停不下来",
# 跑久了 token 增速飙到 ~53/s、全是口水词并漂成英文。实测 0.5 恢复自然收句、
# 翻译质量回升(30s 段从 1.7 字/s 的离题输出恢复到 4.5 字/s 的准确译文)。
LISTEN_PROB_SCALE = 0.5

# ---- 滚动 session(无限期流/会议)----
# 每个 session 只跑这么久就干净回收、再开新的。
#
# 2026-08-29 实测:emb_cache 复用修复后回收 ~0.1s,但英文漂移**不是会话时长
# 问题** —— 80s 档在 26s 就掺英文转写(间歇性「转写 vs 翻译」切换),和会话
# 时长无关。调短只会让字幕更少(每次回收 ~14s 空窗),不解决漂移。
# 故保持 110s(字幕覆盖最好),漂移另寻他法(提示词/listen_prob_scale)。
MAX_SESSION_SECONDS = 110
LANG_NAMES = {
    "中文": "中文",
    "English": "英文",
    "日本語": "日文",
    "한국어": "韩文",
    "Français": "法文",
    "Deutsch": "德文",
    "Español": "西班牙文",
    "Русский": "俄文",
}
DEFAULT_SRC_LANG = "中文"     # 默认源语言
DEFAULT_TGT_LANG = "English"  # 默认目标语言


def make_prompts(src_lang="中文", tgt_lang="English"):
    """按语言对生成翻译提示词(media_type=1 必须 <| 开头)。

    例:src=中文,tgt=English ->
      "把用户输入的中文语音逐句翻译成英文,只输出英文译文本身..."
    """
    s = LANG_NAMES.get(src_lang, src_lang)
    t = LANG_NAMES.get(tgt_lang, tgt_lang)
    voice_clone = (
        "<|im_start|>system\n"
        f"你是一个实时语音翻译助手。请把用户输入的{s}语音逐句翻译成{t},"
        "只输出译文本身,不要添加任何解释、注释或额外内容。\n"
        "<|audio_start|>"
    )
    assistant = f"<|audio_end|>请把上面的语音翻译成{t}。<|im_end|>\n<|im_start|>user\n"
    return voice_clone, assistant


# 默认提示词(中→英,兼容旧代码)
VOICE_CLONE_PROMPT, ASSISTANT_PROMPT = make_prompts(DEFAULT_SRC_LANG, DEFAULT_TGT_LANG)

# ---- GUI ----
GUI_TITLE = "AI 实时翻译"
GUI_SIZE = "720x520"

# AI 实时翻译

实时语音翻译(语音转语音):采集系统声音 / 麦克风 → 远端昇腾 910B 上的
**MiniCPM-o 4.5** → **边说边出译文**,可选**中文配音**实时播放。

- 后端:基于 [tc-mb/llama.cpp-omni](https://github.com/tc-mb/llama.cpp-omni) fork,
  走 **WS full_duplex** 流式协议(逐帧出译文,不是「说完一整句再翻」)。
- 客户端:Python + tkinter 桌面 GUI,另有 CLI 测试脚本,可打包成 Windows exe 双击即用。

---

## 功能特性

| 功能 | 说明 |
|---|---|
| WS duplex 流式翻译 | 边说边翻,每 1.5s 出译文片段,说完即基本完整 |
| 有声翻译(TTS 配音) | 后端合成中文语音流式推回,边翻边播;压缩比 0.57-0.73x,不掉队 |
| 滚动 session | 长会议/长音频自动回收会话,防止上下文无限膨胀 |
| emb_cache 复用 | 后端补丁:会话回收从 ~65s 降到 ~0.1s(不再重读 19.7GB 权重) |
| 字幕弹窗 | 半透明置顶弹窗(类 QQ 音乐歌词),可拖动、可锁定点击穿透 |
| 断线重连 + 看门狗 | 客户端断线自动重连;服务端看门狗自动重启 |
| 一键连接隧道 | exe 启动自动建 SSH 隧道,无需手动 |
| 多语言对 | 中文/English/日/韩/法/德/西/俄,可配置 |

---

## 架构

```
本地 (Windows)
  ├─ client/main.py          GUI(tkinter)
  ├─ client/pipeline_ws.py   WS duplex 采集→推帧→收译文(边说边译)
  ├─ client/pipeline_media.py 连续推流管线(视频/讲座字幕 + 配音)
  ├─ client/api/ws_duplex_client.py  WS full_duplex 客户端
  └─ client/audio/           soundcard 采集 / VAD / 切片 / 配音播放
        │  SSH 隧道 (127.0.0.1:28099 → 远端 28099)
        ▼
远端 (昇腾 910B)
  llama-omni-server  (llama.cpp-omni fork)
  MiniCPM-o 4.5 F16  (LLM + audio/vision/tts/token2wav 子模型)
```

---

## 目录结构

```
├─ client/              桌面客户端(GUI + pipeline + WS 协议)
├─ probes/              后端/链路探针与基准测试
├─ patches/             后端 llama.cpp-omni 补丁(24kHz/提示词注入/hard-listen/emb_cache 复用)
├─ scripts/             部署脚本(重启后端、看门狗、换 IP 同步)
├─ docs/                部署记录
├─ pack.py              打包 exe
├─ PROJECT_STATUS.md    项目状态总览
└─ 测试报告_*.md        测试报告
```

---

## 快速开始(客户端)

### 依赖

```bash
pip install soundcard paramiko numpy websocket-client
```

### 运行

```bash
# 1. 建 SSH 隧道(或让 exe 自动建)
ssh -N -L 28099:127.0.0.1:28099 <SSH别名>

# 2. 启动 GUI(默认 duplex 模式)
cd client
python main.py
```

### 无 GUI 测试(CLI)

```bash
cd client
# 文本流式翻译(连续推流)
python cli_test_video.py --wav ../audio_test/cs336/seg_000500.wav --t 60

# 有声翻译(配音,落 wav)
python cli_test_video.py --wav ../audio_test/cs336/seg_000500.wav --t 60 --dub

# 滚动 session(每 110s 回收一次会话)
python cli_test_video.py --wav ../audio_test/cs336/seg_1200s_300s.wav --t 300 --max-session 110
```

> Windows 控制台跑 CLI 需设 `PYTHONIOENCODING=utf-8`(本机控制台 GBK 编码)。

---

## 打包 exe

```bash
python pack.py
# 产物:dist/AI实时翻译.exe(onefile + windowed)
```

双击即用:自动建 SSH 隧道 → 连后端 → 翻译。前提是运行机有 `~/.ssh/config`(含
`<SSH别名>` 别名)和对应私钥。

---

## 后端部署

后端在昇腾 910B 节点上跑 llama-omni-server,详见
[`docs/omni_inference_deployment.md`](docs/omni_inference_deployment.md)。

关键点:
- 权重:MiniCPM-o-4_5 F16 全家桶(LLM 16.4GB + audio/vision/tts/token2wav);
- 启动参数:`-c 3072 --repeat-penalty 1.15 --repeat-last-n 256`(必开 repeat-penalty,否则长会话退化);
- **24kHz + 短切片**是硬约束(16kHz / 超长片段会翻错);
- 后端补丁见 `patches/`,其中 emb_cache 复用补丁让滚动回收降到亚秒级。

---

## 已知问题

1. **英文漂移**:翻译中间歇性转成英文转写(与 `listen_prob_scale`/提示词有关,已确认**与会话时长无关**,是「转写 vs 翻译」切换的翻译质量问题,方向在提示词 / 采样参数)。
2. **loopback 回授**:采集源选「系统声音」且配音从同一扬声器播放时,配音会被采回形成回授环;建议「麦克风 + 耳机」。

---

## 相关文档

- [`PROJECT_STATUS.md`](PROJECT_STATUS.md) — 项目状态总览
- [`docs/omni_inference_deployment.md`](docs/omni_inference_deployment.md) — 后端部署记录
- [`使用说明.md`](使用说明.md) — 桌面客户端使用说明
- `测试报告_*.md` — 各次测试报告

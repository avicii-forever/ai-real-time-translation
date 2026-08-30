# 语音转语音实时翻译 · 实现计划

> 状态:待批准
> 日期:2026-08-25

## 目标

把当前"中→英文本翻译"升级为**语音转语音(Voice-to-Voice)实时翻译**:
- 输入:中文语音(系统声音/麦克风)
- 输出:**英文译文语音(TTS 播放)+ 英文文本显示**
- 语言对:先做 **中→英**(后端英文 TTS 已验证)

## 现状(已验证)

- HTTP 链路(omni_init → prefill → decode)完全跑通,use_tts=false
- 后端 TTS 全链路(APM→LLM→TTS→token2wav)在新节点**能产出语音**(CLI 验证过 13 段中文 TTS)
- TTS 语音落盘:`<output_dir>/round_XXX/tts_wav/wav_N.wav`(24kHz float)
- 客户端已有:采集/VAD/切片/SFTP上传/翻译/文本显示

## 改造点

### 1. 后端:打开 use_tts=true
- omni_init 改 `use_tts=true`(后端验证时已确认声码器不 crash)
- 翻译提示词不变(先翻成英文文本,再由 TTS 读英文)

### 2. 客户端:decode 后拉取并播放 TTS 语音
- decode 完成后,SFTP 从 `output_dir/round_XXX/tts_wav/` 拉取 `wav_*.wav`
- 本地合并 + 播放(winsound/sounddevice)
- 播放的同时保留文本显示

### 3. 播放实现
- 本地 `winsound.PlaySound`(已验证可播 24k wav)或 sounddevice 流式播
- 拉取多个 wav_N 按顺序合并成一个 wav 播放(或逐段播)

## 目录/文件改动

```
client/
├─ api/omni_client.py   # omni_init 默认 use_tts=true;新增 list_round_wavs()
├─ transport/sftp_uploader.py # 新增 pull(dir, local) 拉取 TTS wav
├─ audio/player.py      # 新增:合并 wav + winsound 播放
├─ pipeline.py          # _translate_batch: decode 后拉 TTS + 播放
└─ main.py              # 译文显示 + "语音输出"开关
```

## 关键风险

1. **TTS 生成耗时**:声码器 RTF≈1.3x(实测),1s 音频约 1.3s 生成。中文语音翻译后英文 TTS 时长未知,播放会有延迟
2. **wav 文件时序**:`tts_wav/` 文件是异步落盘,decode 返回时可能没写完 → 需轮询等待 generation_done.flag 或超时
3. **语音质量**:新节点验证过 TTS 能出音频,但**英文译文**的 TTS 音质需实测(之前 CLI 测的是中文 TTS)
4. **多轮累积 decode 慢**(已知):语音场景下每句 decode 变慢,可能加剧延迟

## 验证方式

1. 远端先跑:HTTP + use_tts=true + 6 段中文音频 → decode → 检查 `tts_wav/` 是否产出英文语音
2. 本地:客户端完整链路,播放中文 → 收到译文文本 + 播放英文 TTS

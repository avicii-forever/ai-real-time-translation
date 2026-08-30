# AI 实时翻译 · 语音转语音(V2V)实时翻译

采集系统声音/麦克风 → 远端昇腾 910B 上的 MiniCPM-o 4.5 → **边说边出英文译文**(duplex 流式)。

## 两种模式

| 模式 | 延迟 | 说明 |
|---|---|---|
| **duplex**(默认) | **边说边翻**(每帧 1.5s 出译文片段) | WS full_duplex 流式,说完即基本完整 |
| **http** | 说完+处理(~20s) | 整句翻译 + TTS 语音(旧方案) |

GUI 顶部可切换模式。

## 依赖

```bash
pip install soundcard paramiko numpy websocket-client
```

## 启动

```bash
# 1. 建 SSH 隧道
ssh -N -L 28099:127.0.0.1:28099 <SSH别名>

# 2. 启动客户端(GUI 默认 duplex 模式)
cd client
python main.py
```

## 无 GUI 测试

```bash
python cli_test_ws.py --once 60   # duplex 流式(推荐)
python cli_test.py --once 60      # http 整句
```

## 架构(duplex 模式)

```
声音 → capture(24kHz) → VAD 判断有声/静音
    → 有声段切 1.5s 帧 → WS input.append(audio_b64 float32 PCM)
    → 后端逐帧 prefill+decode → text_delta 流式回传
    → VAD 静音结束 → push_silence 收尾 → 完整译文
    → 客户端 _fix_output(下划线转空格,去重) → 显示
```

## 后端 patch(已应用,昇腾节点)

1. `ws_handler.cpp`:`write_audio_wav` 采样率 16000→24000(APM 需 24k)
2. `ws_handler.cpp`:duplex 模式下 system_prompt 注入 voice_clone 前缀(翻译指令)
3. `omni.cpp`:
   - `listen_prob_scale<=0.05` 时把 `<|listen|>` logit 设 -inf(hard-listen)
   - `is_end_token` 在 hard-listen 下忽略 chunk_eos,连续生成到 EOS

**配置关键**:WS init 需传 `listen_prob_scale: 0.01`(触发 hard-listen)。

## 配置

`client/config.py`:后端地址、采样率、切片、提示词等。

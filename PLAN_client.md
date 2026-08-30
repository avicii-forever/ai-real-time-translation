# 实时翻译桌面客户端 · 实现计划

> 状态:待批准
> 日期:2026-08-25

## 背景

后端链路已在昇腾 910B 新节点验证通过(见 PROJECT_STATUS.md)。关键约束:
- **24kHz** 音频、**1-2s 切片流式**(长段/16k 会翻不对)
- REST:`omni_init` → `prefill`(逐块)→ `decode`(SSE)
- 每块音频需以**服务端 WAV 路径**传给 prefill

## 技术栈(用户已确认)

Python 全套:tkinter GUI + soundcard(loopback 系统声音 + 麦克风)+ paramiko(嵌套 SSH/SFTP)+ requests/flask。

## 目录结构

```
E:\ai-real-time-translation\client\
├─ main.py            # 入口 + tkinter GUI(启动/停止/源选择/译文显示)
├─ config.py          # 配置(后端、音频参数、远端路径)
├─ api\
│  └─ omni_client.py  # REST 封装:omni_init / prefill / decode(SSE)
├─ audio\
│  ├─ capture.py      # soundcard 采集(loopback + mic,float32→24kHz PCM)
│  ├─ slicer.py       # 1.5s 切片 + WAV 内存编码(16-bit mono 24kHz)
│  └─ vad.py          # 能量 VAD:检测语句起止
├─ transport\
│  └─ sftp_uploader.py # paramiko 嵌套 SSH/SFTP,固定路径覆盖上传
└─ pipeline.py        # 采集→切片→上传→prefill→decode→译文 状态机
```

## 核心流程

### 启动
1. GUI 选择输入源(系统声音 / 麦克风)
2. 建立 paramiko 嵌套 SSH(网关→节点)+ SFTP 长连接
3. 检查隧道(或自动建 `ssh -L 28099` 后台)
4. `omni_init`(首次,3-5s 加载模型)

### 每轮对话(自动循环)
```
用户开始说话(VAD 能量触发)
  └─▶ 采集 24kHz
        └─▶ 每 1.5s 切块 → WAV 内存编码
              └─▶ SFTP 覆盖上传 live_<n>.wav(远端固定路径)
                    └─▶ prefill(cnt=1,2,3...)
        └─▶ VAD 静音超过阈值 → 轮次结束
              └─▶ decode(SSE) → 增量显示译文
                    └─▶ round_idx++ 等待下一轮
```

### 关键技术点
- **采样率**:soundcard 采集后重采样到 24kHz(用 numpy,线性插值)
- **切片**:1.5s 固定窗;音频不足时 pad 静音(VAD 边界对齐)
- **上传**:SFTP 覆盖 `live.wav`,单连接复用,延迟低
- **SSE**:urllib 流式读 `data:` 行,解析 `content` 增量显示
- **VAD**:RMS 能量 + 静音时长双阈值,自适应底噪
- **错误处理**:隧道断开自动重建、后端超时重试、VAD 失灵兜底(最大语句长度)

### GUI
- 输入源下拉 + 开始/停止按钮
- 实时状态(采集中/翻译中/空闲)
- 译文滚动区(原文可留英文/中文,先输出英文译文)
- 日志区(debug 信息)

## 阶段划分

1. **P1 核心链路**(不含 GUI):config + api + audio(capture/slicer/vad)+ transport + pipeline
   - 验证:CLI 脚本跑通"本机放音 → 自动切块翻译 → 打印译文"
2. **P2 GUI**:tkinter 界面 + 线程整合
3. **P3 打磨**:隧道自管理、断线重连、参数可调、日志

## 验证方式

- P1:本地播放中文音频(edge-tts 生成的 24k wav)→ 客户端自动采集 → 翻译 → 对照原文
- P2:GUI 手动操作全流程
- 真实系统声音:播放视频/音频,客户端 loopback 采集翻译

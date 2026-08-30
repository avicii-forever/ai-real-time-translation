# ai-real-time-translation · 项目状态

> 更新:2026-08-29

## 迁新实例 + emb_cache 复用验证通过(2026-08-29)

旧实例 NPU 状态坏(session 激活 SIGSEGV,3427MB 无主显存)救不回,迁到新实例
`DevEnv_775164`(<节点IP>,**910B4 / 32GB HBM**)。部署:权重在共享盘
`/workspace/shared_assets/models/OpenBMB/MiniCPM-o-4_5-gguf/`(软链到
`/workspace/MiniCPM-o-4_5-gguf`),源码从旧实例 tar 中转,编译通过。

**emb_cache 复用修复验证成功**(`omni.cpp` `duplex_stop_threads` 加 `free_pipeline` 参数):
回收时保留 DuplexPipeline 和 emb_cache,不再重读 19.7GB 权重。结果:

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 回收服务端耗时 | ~65s | **~0.1s** |
| 回收字幕间隔 | ~73s | **~14s** |
| 300s 字幕数 | 58 | **94** |
| 热 session.init | ~8s | **0.4s** |

**仍剩英文漂移**:~90s 就开始掺英文转写,`MAX_SESSION_SECONDS` 已从 110 调 **80**
(回收够快了,放心提前换)。80s 档待实测确认漂移消失。

新实例注意:NPU 逻辑 ID=0(旧是 3),冷加载 ~431s(共享盘首读慢),session 激活不再段错误。

## 滚动 session 进实时管线(2026-08-28)

昨天滚动回收只做进了 `pipeline_media.py`(视频路径),GUI 走的实时路径
`pipeline_ws.py` 还没有 —— 长会议照样会漂。今天补齐:

- `pipeline_ws.WSPipeline` 加 `max_session_seconds`(默认 `config.MAX_SESSION_SECONDS`),
  **只在 VAD 静音边界回收**,不切断正在说的句子。
- `WSPipeline.stop()` 从 `ws.close()` 改成 `ws._force_close()` —— 后端是单 session,
  socket 不真断它不会 `omni_prepare_for_reuse`,下次 start 会撞 "active session exists"。
- 回收/重连走新的 `WS_RECONNECT_TIMEOUT=60`(模型已常驻,init 只要 5-8s),
  不再用冷加载那个长超时把界面晾住;`WS_CONNECT_TIMEOUT` 200→**320**
  (整机重启冷盘实测 285s,200 不够)。
- 视频管线的回收也换成短超时。

**离线验证**(`probes/probe_roll_live.py`,mock 后端 + 文件音源,不需要节点):
70s / roll=20s → 建立 3 个会话,回收发生在 24s、50s,**全部落在静音边界**,PASS。

**⚠️ 真机验证结果(2026-08-28)**:300s 连续推流 + `max-session 110`,
58 条字幕、无英文漂移 —— 滚动**确实挡住了漂移**;但**每次回收丢 ~65s 音频**:
`session.init` 里 `create llm & tts thread` 到 `encoder_thread started` 要 ~65s,
(模型权重常驻,但 duplex 的 VPM/APM encoder 线程每次新 session 都要重建,躲不掉)。
`MAX_SESSION_SECONDS=110` 下 65s/110s ≈ **丢 37% 音频** —— 对连续字幕不可接受。
详细:见 memory `duplex-rolling-recycle-cost`。治本方向是后端复用 encoder 线程、
只重置 KV/轮次,否则滚动对讲座字幕这条路走不通。

## 视频流式中文字幕 + 实时中文配音(2026-08-27 完成)

英文视频(CS336 讲座)→ **流式中文字幕** + **实时中文配音**,端到端跑通。

**关键指标**(CS336,单会话):

| 指标 | 值 |
|---|---|
| 首字延迟(推流起算) | 2.3s |
| 配音比文本延迟 | 1.2s |
| 字幕产出 | 56 条 / 180s |
| 配音压缩比 | 0.73x(60s 英文 → 43.5s 中文,不会掉队) |
| session.init | 26-184s(冷启动 + 排队,一次性) |

**两个必须知道的后端约束**:

1. **`--repeat-penalty` 必须开**。原来 `-c 4096` + 默认 `--repeat-penalty 1.00`(关闭),
   模型跑到 ~105s 退化成 `乘乘乘乘…` 死循环。现为
   `-c 16384 --repeat-penalty 1.15 --repeat-last-n 256`(备份 `start_server.sh.bak.20260826`)。
   同一段音频:修前 1 条垃圾字幕 → 修后 56 条可用字幕。
2. **单 session,别 churn**。拆除 `omni_prepare_for_reuse` 要 60-90s,新 init 得排队。
   一个视频从头到尾只连一次;`--segment` 保持 0。

**协议补充**:上行只有 `session.init` / `input.append`,**没有** in-session reset。
配音音频走 `response.output.delta` + `kind="audio"`,载荷是 base64 float32 PCM @ 24kHz。

**新增代码**:
```
client/pipeline_media.py       # 连续推流管线(不用 VAD),文本层断句 + 配音接线
client/audio/file_source.py    # FileCapture:视频音轨按播放速度喂进管线
client/audio/dub_player.py     # DubPlayer(队列+播放线程) / DubRecorder(落盘)
client/text_utils.py           # duplex 输出清洗(下划线->空格、去 CJK 间空格)
client/cli_test_video.py       # 端到端测试,--dub 开配音,落 srt/txt/dub.wav
probes/{mock_ws_duplex,trace_duplex,bench_duplex,probe_duplex_tts}.py
```

**跑测试必须带 `PYTHONIOENCODING=utf-8`**(本机控制台 GBK)。

**未解决**:
- 跑到 ~160s 后模型会丢掉"翻成中文"指令,漂移成英文转写
- 译文口语化啰嗦(讲座本身 um/sorry/you know 很多,模型忠实翻了出来)
- **loopback 回授**:若用 loopback 采集视频声、配音又播到同一扬声器,配音会被采回去。
  必须走"读文件音轨 + 视频静音播放",不能用 loopback。

## 长会话退化定位 + listen_prob_scale 调优(2026-08-27)

**结论:`listen_prob_scale` 从 0.01 改成 0.5,短会话翻译质量恢复;长会话漂移仍未解。**

关键发现:
1. hard-listen(0.01)让模型永远进不了 LISTEN 分支 → `slide_last_was_listen` 恒 false、
   `text_streaming` 恒 true、`rounds` 恒 0。**整个会话是一个永不结束的 turn,轮次边界没了。**
2. 后端 KV 滑窗依赖轮次边界;边界没了,滑窗只能在生成中途盲切(实测:切深 1681 token 会崩、
   切勤 9s 一次会出 `ABAABAAB` 乱码)。所以"调滑窗参数"是死路,已验证。
3. `listen_prob_scale` 逐档实测:0.01 退化 / 0.3 句尾漂英文 / **0.5 好** / 0.7 沉默。
   客户端默认已改 0.5(config.LISTEN_PROB_SCALE)。
4. 代价:0.5 首字延迟 ~18.5s(模型先听再开口),vs 0.01 的 ~2.3s。
5. **长会话(180s)在 0.5 下前 90s 干净、之后英文逐渐掺入 —— 未解决。**

服务端当前状态:`-c 3072 --repeat-penalty 1.15 --repeat-last-n 256`(备份
`start_server.sh.bak.20260826`)。源码 revert 到未打补丁状态(滑窗补丁
`patches/duplex_slide_config.py` 可 --revert,`.bak.slide_config` 备份在节点上)。

## 历史记录(2026-08-25)

## 当前结论

- **目标**:实时翻译桌面应用(系统声音 + 麦克风两路),后端用 OpenLibing Ascend 910B 节点上的 MiniCPM-o 4.5。
- **✅ 后端已迁移到新节点 `<SSH别名>`(<节点IP>),音频→翻译链路验证通过。**
- **✅ 桌面客户端已完成(Python + tkinter),系统声音/麦克风实时采集翻译全流程跑通。**

## 环境记录

### 当前推理节点(新,迁移完成)

- **SSH 别名**:`<SSH别名>`(Host <节点IP>:22,ProxyJump 网关 <网关IP>:2222)
- **硬件**:Ascend 910B3,**NPU Health = OK**(旧节点 AIV `80CB8001` 告警已消失)
- **服务**:`llama-omni-server` 在 `:28099`,`/health` → `{"engine":"comni","status":"ok"}`
- **部署**:commit `6e9ae1a` + 3 个本地 patch,权重在 `/workspace/MiniCPM-o-4_5-gguf/`
- **启动**:`/workspace/llama.cpp-omni/start_server.sh`;日志 `server.log`
- 部署全记录:`docs/omni_inference_deployment.md`;本地补丁:`patches/`
- 本机隧道:`ssh -N -L 28099:127.0.0.1:28099 <SSH别名>`
- **⚠️ 服务重启后需重新建隧道**

### 复用基础设施

- `E:\ai-dubber\scripts\remote.py` + `remote_config.json`(已指向新节点 <节点IP>)
- paramiko 上传用 Windows 路径(`E:\...`)

## WS duplex 翻译打通(2026-08-25 深夜)

**核心突破**:三个后端 patch 让 WS full_duplex 能做翻译(边说边出译文):

1. **采样率 24k**:`ws_handler.cpp write_audio_wav` 16000→24000(APM 硬需求)
2. **提示词注入**:WS init 的 `system_prompt` 同时设 voice_clone 前缀(翻译指令进系统提示词),duplex 分支补丁
3. **hard-listen + 连续生成**(omni.cpp):
   - `listen_prob_scale<=0.05` 时把 `<|listen|>` logit 设 -inf(禁 listen)
   - `is_end_token` 在 hard-listen 下忽略 chunk_eos,像单工连续生成到 EOS

**效果**:WS duplex 逐帧流式翻译,每帧音频 0.4-0.5s 出译文片段。
实测 6 帧(8.2s 中文)→ 逐帧输出完整译文:
`Ok, I will reserve a flight from Shanghai to Beijing tomorrow morning. I want to take the train departing at 8 a.m.`
(空帧触发收尾补齐最后片段)

**对比延迟**:
- HTTP 串行:说完 8.2s + 处理 12s ≈ 20s 才见译文
- **WS duplex:说完即基本完整(边采边出)**,延迟降低 ~15 倍

**客户端已改造完成**:新增 `api/ws_duplex_client.py`(WS 协议)+ `pipeline_ws.py`(边说边翻)+ GUI 模式切换(duplex/http)。实测:
- 每帧 1.5s 音频推入,译文从第 1 帧就开始滚动(`I have a` → `I have a reservation for a train from Shanghai to Beijing tomorrow morning at 8am.`)
- 说完即基本完整(不用等整句+处理)
- 下划线(空格)转义、done 去重已处理
- CLI + GUI 端到端验证通过
(已完成)

## 语音转语音(V2V)实时翻译(2026-08-25 完成)

- **中文语音进 → 英文文本 + 英文 TTS 语音播放出**,端到端验证连续 3 句全部成功
- 客户端改动:`omni_init(use_tts=true)`、decode 后从 `round_000/tts_wav/` 拉取 wav 合并播放、GUI 加"语音输出"开关
- **后端多轮 TTS bug(重要)**:同一 session 连续两轮时,第二轮 TTS 不产出(simplex_round_idx 递增 + TTS 线程状态竞态)。**规避方案:每轮翻译前 omni_init + 清远端输出目录**(验证每次 TTS 都完整)。
- WS duplex 翻译:协议本身可用(init/帧/事件都通),但 `write_audio_wav` 写死 16kHz 与 APM 的 24kHz 需求冲突(16k 翻不对)。已改后端采样率为 24000 重编译,但 duplex 的 listen 状态机导致模型倾向听而非翻译,纯翻译场景不匹配。**结论:走 HTTP + 每轮重置 session 是当前最可靠路径**。
- 每轮成本:omni_init ~6s + prefill + decode 1.5s + TTS ~2-8s ≈ 8-15s/句

## 桌面客户端(`client/`)

### 结构
```
client/
├─ main.py            # tkinter GUI 入口
├─ config.py          # 后端/音频/SSH 配置
├─ pipeline.py        # 采集→切片→上传→prefill→decode 状态机
├─ cli_test.py        # CLI 测试(无 GUI)
├─ gui_test.py        # GUI 自动化测试
├─ api/omni_client.py # REST 封装(omni_init/prefill/decode SSE)
├─ audio/
│  ├─ capture.py      # soundcard loopback + 麦克风
│  ├─ vad.py          # 相对阈值 VAD(v4)
│  └─ slicer.py       # 1.5s 切片 + WAV 编码
└─ transport/sftp_uploader.py  # 嵌套 SSH/SFTP 固定路径上传
```

### 使用
```bash
# 1. 建隧道
ssh -N -L 28099:127.0.0.1:28099 <SSH别名>
# 2. 跑 GUI(或 CLI 测试)
cd client && python main.py          # GUI
cd client && python cli_test.py --once 30   # CLI,采 30s
```

### 已验证(端到端)
- **系统声音 loopback 采集 24kHz → VAD 切句(8s/句)→ 1.5s 切片 → SFTP → prefill → decode → 英文**
- 译文示例:`May I book you a high-speed train ticket from Shanghai to Beijing tomorrow morning?`(高铁票句,内容正确)
- GUI 用 queue 线程安全更新,无 tkinter 跨线程错误

## 🔑 关键设计约束(应用端必须遵守)

| 输入 | 结果 |
|---|---|
| **24kHz**,切成 1-2s 短块流式 | ✅ 翻译正确 |
| **16kHz** | ❌ 全失败 |
| 单段 >8s 或 >6 块(~9s) | ⚠️ 退化 |
| 超长(合并 16.6s) | ❌ 错乱 |

- **单次翻译音频 ≤6 块(≈8-9s)**,超长按 6 块分批(已实现)
- 采样率 24kHz;`use_tts=false`

## 运维

- **服务会随节点网络波动挂掉**(2026-08-25 隧道 No route to host + 服务消失)。恢复流程:
  1. `ssh <SSH别名>` 测试节点可达
  2. 远端执行 `scripts/restart_backend.sh`(重启 llama-omni-server)
  3. 本地建隧道:`ssh -N -L 28099:127.0.0.1:28099 <SSH别名>`
  4. `curl 127.0.0.1:28099/health` 验证
- 一键脚本:`scripts/connect.ps1`(隧道)、`scripts/restart_backend.sh`(远端服务重启)
- **旧节点 `openlibing-omni`(<节点IP>)已不可达**(SSH banner 超时),确认弃用

## 资源释放决策(2026-08-26)

- **模型(19.7GB)常驻显存是后端复用设计**(shared_octx 复用,下次翻译免加载)。翻译完成不自动释放 —— 这是有意的。
- **释放方式:重启服务**。已优化 `scripts/restart_backend.sh`(pkill -x 精确杀 + setsid 脱离会话 + 健康等待),不再有"pkill 断 SSH 服务起不来"问题。
- 已验证:重启后 NPU 显存 17.6GB → 105MB(模型懒加载),旧进程成僵尸(不占显存,无碍)。
- 旧进程僵尸条目:系统 init 会回收,不影响运行;无需处理。

## 问题记录

- **旧节点 AIV 告警**:已随迁移解决(新节点 Health OK)
- **soundcard**:recorder 需 `with` 进入才能 record;loopback 用 `all_microphones(include_loopback=True)` 里 isloopback 项
- **VAD 演进**:v1 绝对阈值(底噪误判)→ v4 相对阈值(峰值×0.30 判定语句结束),对高底噪环境(本机 loopback 底噪 RMS≈0.035)有效
- **多 session 污染**:客户端重复 start/stop 会创建多个 omni_init 争抢后端单 session,导致 decode 空。真实使用单次 start 即可
- **模型输出风格**:会带 "Me:" 前缀/客套,客户端 `_clean_output` 已剥除;提示词已强化

## 待办

- [ ] 音频播放/扬声器输出端(可选:翻译结果 TTS 播报)
- [x] 断线重连(duplex WS + SSH 隧道已做;HTTP/SFTP 路径仍未加)
- [x] 参数 UI 化(切片时长/VAD 灵敏度;GUI 设置卡新增"切片时长 1.0/1.5/2.0s + VAD 灵敏度 高/中/低",运行时写回 config)
- [x] PyInstaller 打包(`dist/AI实时翻译.exe`,一键连隧道 + 断线重连 + 隧道保活)

## 测试素材(远端)

- `/workspace/llama.cpp-omni/tools/omni/assets/my_test/`:`edge_*_24k.wav`、`segments/hsr_*.wav`、`live_*.wav`(客户端实时上传)
- 本地:`E:\ai-real-time-translation\audio_test\`、`make_zh_wav.py`、`upsample_wav.py`

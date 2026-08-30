# OpenLibing omni 推理服务部署记录

> 记录日期:2026-08-25
> 用途:实时翻译桌面应用(ai-real-time-translation)的后端推理服务
> 本文档记录**当前节点 `openlibing-omni`(Ascend 910B3)上的实际部署**,作为迁移到新机器时的参考基线。

---

## 0. 拓扑与访问

```
本地 (Windows)  ──SSH──▶  OpenLibing 网关  <网关IP>:2222  (user=jump, direct-tcpip only)
                             │
                             ▼  nested SSH
                    推理节点  <节点IP>:22  (user=root, openlibing-omni)
                             │
                        容器 openEuler 22.03 SP4 / aarch64
                             │
                        llama-omni-server :28099
                             │
本地 SSH 隧道 127.0.0.1:28099 ──▶ 远端 28099 (HTTP /health 可达)
```

- SSH 配置在 `C:\Users\chw\.ssh\config`,Host 别名 `openlibing-omni`(ProxyJump 网关)。
- paramiko 版连接/上传实现参考:`E:\ai-dubber\scripts\remote.py` + `remote_config.json`(密钥路径、嵌套 SSH、SFTP)。
- 隧道:`ssh -N -L 28099:127.0.0.1:28099 openlibing-omni`

---

## 1. 节点硬件与系统

| 项 | 值 |
|---|---|
| 硬件 | 单卡 Ascend **910B3**,64GB HBM(本节点 NPU 逻辑 ID = 7) |
| 系统 | 容器内 openEuler 24.03 (LTS-SP3),aarch64,内核 5.10.0 |
| CPU | 256 核(aarch64) |
| 内存 | 200 GB(可用 ~197GB) |
| 磁盘 | 300GB overlay,当前用量很少 |

---

## 2. 推理栈版本

| 层 | 版本 |
|---|---|
| 昇腾驱动 (driver) | **25.2.0**,ascendhal 7.35.23(路径 `/usr/local/Ascend/driver/`) |
| CANN | **9.1.0-beta.3**(`/usr/local/Ascend/cann-9.1.0-beta.3/`,另有 ascend-toolkit latest) |
| 推理框架 | **tc-mb/llama.cpp-omni** fork,commit `6e9ae1a` "fix force listen (#94)" |
| GGUF 权重 | MiniCPM-o 4.5 **F16** 全家桶(见 §6) |
| 构建 | CMake Release,`GGML_CANN=ON`(后端:ggml-cpu + ggml-cann) |

**昇腾环境变量**由 `source /usr/local/Ascend/cann-9.1.0-beta.3/set_env.sh` 提供(设置 LD_LIBRARY_PATH、ASCEND_HOME 等)。

---

## 3. 源码与本地补丁(关键,迁移时必须带上)

源码目录:`/workspace/llama.cpp-omni`(git,remote 走 gh-proxy:`https://v4.gh-proxy.org/https://github.com/tc-mb/llama.cpp-omni.git`)。

**当前 HEAD 之上有 3 个未提交的本地改动**(`git status` 显示 M),这些是行为修正,不是上游自带:

### 3.1 `ggml/src/ggml-cann/ggml-cann.cpp`(+14 行)— 昇腾后端修复
1. **`GGML_OP_SQR` 实现**(Block 1 修复之一):aclnn 没有独立 square 算子,用 `x² = x·x` 实现:
   - 把 `dst->src[1]` 临时别名为 `dst->src[0]` → `ggml_cann_binary_op<aclnn_mul>`,然后**恢复 src[1]**(避免 flow-matching 图反复重算时踩到陈旧 src[1])。
2. **`ggml_backend_cann_set_tensor_async` / `get_tensor_async`**:调用前 `ggml_cann_set_device(cann_ctx->device)` —— 把 CANN device context 显式绑定到后端设备(对应报告 Blocker 1 的「CANN 上下文多线程」问题,在 T2W 工作线程中尤其关键)。
3. **`ggml_backend_cann_free`**:删掉 `aclrtResetDevice`,保留 `aclrtSynchronizeDevice` + set_device(重复 omni_init 不再崩)。

### 3.2 `tools/omni/omni.cpp`(+11 行)— 生成/清理修正
1. `stream_prefill` index=0:把注释掉的 `llama_kv_cache_clear` 换成 `llama_memory_clear(llama_get_memory(...))` —— 清 LLM KV cache,避免跨请求残留旧 KV 导致生成死循环。
2. `stream_decode`:主循环条件加 `(il + total_tokens_generated) < max_tgt_len` —— 生成长度硬上限,压住模型"狂生成不停止"。

### 3.3 `tools/server/server-omni.cpp`(+6 行)— HTTP 服务修正
1. `omni_init` handler:设置 prompt 时把 `omni_*` **和** `audio_*` 两组字段都赋值(原来只设 omni_*,音频模式的翻译提示词会被忽略)。
2. SSE chunked provider 结束返回 `false`(原来 `return true` 导致 SSE 挂起/重复)。

> 备份文件:同目录 `*.bak*`(`ggml-cann.cpp.bak3` / `.bak_blocker1_reset` / `.bak_blocker1_sqr` / `.bak_blocker1fix`、`omni.cpp.bak_pre_blocker3fix`、`server-omni.cpp.bak` / `.bak2`)。**迁移建议:直接 `git diff` 导出成 patch,带到新机器重打,而不是手抄。**

---

## 4. 构建方法

```bash
cd /workspace/llama.cpp-omni
source /usr/local/Ascend/cann-9.1.0-beta.3/set_env.sh   # 或镜像自带等效环境

cmake -B build -DGGML_CANN=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

产物(在 `build/bin/`):
- `llama-omni-server`(HTTP/WS 服务端,1.1MB)
- `llama-omni-cli`(独立推理 CLI,79KB)
- `libggml-cann.so`(昇腾后端,CANN 构建必需)

构建目录当前 57MB。

---

## 5. 服务启动(实测)

启动脚本 `/workspace/llama.cpp-omni/start_server.sh`:

```bash
#!/bin/bash
cd /workspace/llama.cpp-omni
source /usr/local/Ascend/cann-9.1.0-beta.3/set_env.sh 2>/dev/null || true
export LD_LIBRARY_PATH="/workspace/llama.cpp-omni/build/bin:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common:${LD_LIBRARY_PATH}"
exec ./build/bin/llama-omni-server \
    -m /workspace/MiniCPM-o-4_5-gguf/MiniCPM-o-4_5-F16.gguf \
    --host 0.0.0.0 --port 28099 -n 256 -c 4096 -ngl 99
```

关键点:
- **权重是懒加载**:启动 ~1.2GB,真正加载在 `omni_init` 调用时(~19.7GB,约 4-7s)。
- `-n 256` 生成长度、`-c 4096` 上下文(模型训练上下文 40960,4096 够用)。
- 日志:`std::cout` 管道重定向会缓冲,**诊断要用 `stdbuf -oL -eL`**(见 §8)。
- 手动重启:先 `kill` 进程再起(服务内部没有守护)。

---

## 6. 模型权重(19.7GB 全量,目录 `/workspace/MiniCPM-o-4_5-gguf/`)

| 文件 | 大小 | 角色 |
|---|---|---|
| `MiniCPM-o-4_5-F16.gguf` | 16.4G | 主 LLM |
| `audio/MiniCPM-o-4_5-audio-F16.gguf` | 660M | APM 音频编码器(字幕/ASR 核心) |
| `vision/MiniCPM-o-4_5-vision-F16.gguf` | 1.1G | 视觉编码器 |
| `tts/MiniCPM-o-4_5-tts-F16.gguf` | 1.16G | TTS(语音合成,可选) |
| `tts/MiniCPM-o-4_5-projector-F16.gguf` | 15M | TTS projector |
| `token2wav-gguf/encoder.gguf` | 151M | 声码器 token→wav 编码器 |
| `token2wav-gguf/flow_matching.gguf` | 458M | 声码器 flow-matching 主模型 |
| `token2wav-gguf/flow_extra.gguf` | 13.7M | 声码器 |
| `token2wav-gguf/hifigan2.gguf` | 83M | 声码器 HiFiGAN 声码器 |
| `token2wav-gguf/prompt_cache.gguf` | 212M | 声码器 |

> 子模型路径由 `-m` 目录结构自动推导(见 `omni-cli.cpp` / `ws_handler.cpp` 的 `resolve_model_paths` 逻辑)。

---

## 7. HTTP API(已实测,`127.0.0.1:28099`)

- `GET /health` → `{"engine":"comni","status":"ok"}`
- `POST /v1/stream/omni_init`(body):
  - `media_type`(或 `msg_type`):**1=音频**,2=多模态
  - `use_tts`:false(避免声码器 crash)
  - `voice_clone_prompt` / `assistant_prompt`:提示词,见下
  - `output_dir`:服务端输出目录
- `POST /v1/stream/prefill`(body):
  - `audio_path_prefix`:**服务端文件系统上的完整 WAV 路径**(传给 `omni_audio_embed_make_with_filename`)
  - `cnt`:int,**0=系统提示词初始化,用户音频必须从 cnt≥1 开始**(否则第一个 chunk 被丢)
- `POST /v1/stream/decode` → SSE,`{"stream":true,"debug_dir":...}`,收到 `data: {"content","stop","is_listen","end_of_turn"}` 直到 `data: [DONE]`

**翻译提示词模板(media_type=1,必须以 `<|` 开头)**:
```
voice_clone_prompt = "<|im_start|>system\n{翻译指令}\n<|audio_start|>"
assistant_prompt   = "<|audio_end|>{任务}<|im_end|>\n<|im_start|>user\n"
```
示例见 `E:\ai-real-time-translation\probe_translate.py`。

---

## 8. 已知问题与诊断(迁移后可能遇到)

1. **NPU 健康告警**:`npu-smi info -t health -i 7 -c 0` 报 `80CB8001`,`AIV(向量单元)/RAS State/module error can not be fixed`。
   - ECC HBM 双 bit=0、PCIe err=0 —— **内存/总线正常**,属 AIV 单元级不可修复告警。
   - 影响:文本生成等算子仍能跑(报告实测 59 tok/s);AIV 相关算子(音频/部分量化)可能**挂起/空转**。
   - **迁移建议:新机器先确认 `npu-smi info` 无 Alarm 再部署。**

2. **HTTP 音频路径挂起(当前节点)**:`omni_init` 成功(3.9s)后,`prefill cnt=0` 在 `system prompt ref_audio` 之后 **100% CPU 空转**(不是阻塞),日志停在 `system prompt ref_audio:` 行。疑似 APM/CANN 异步忙等,与告警 80CB8001 相关联。
   - 独立 CLI(`llama-omni-cli --no-tts --test ...`)同样疑似卡 APM。
   - **诊断手段**:服务日志 `std::cout` 需 `stdbuf -oL -eL` 才能实时看到(否则只在退出时 flush)。

3. **声码器(TTS)在昇腾 crash**(历史,报告 Blocker 1):`ggml-cann.cpp:70 CANN error, rtMemcpyAsync ... context null pointer, device -1`。当前 `use_tts=false` 规避。

---

## 9. 客户机(本机 Windows)侧依赖

- `E:\ai-real-time-translation\probe_translate.py` —— CLI 翻译探针(调 REST,走隧道)。
- `E:\ai-dubber\scripts\remote.py` / `remote_config.json` —— paramiko 嵌套 SSH + SFTP(网关 direct-tcpip → 节点)。
- 本机已装:flask / requests / numpy / websocket / paramiko / python 3.12(conda 环境)。

---

## 10. 迁移到新机器 checklist

- [ ] 新节点 `npu-smi info` 无 Alarm(重点看 AIV/health)
- [ ] 装昇腾驱动 + CANN 9.1.0-beta.3(或镜像预装),`set_env.sh` 可 source
- [ ] `git clone` tc-mb/llama.cpp-omni @ 6e9ae1a(gh-proxy 或本地 bundle)
- [ ] 打上 §3 的三个本地 patch(建议 `git diff` 导出)
- [ ] `cmake -B build -DGGML_CANN=ON -DCMAKE_BUILD_TYPE=Release && cmake --build build -j`
- [ ] 放置权重到 `/workspace/MiniCPM-o-4_5-gguf/`(19.7GB,见 §6)
- [ ] `start_server.sh` 起服务,`curl /health` 通
- [ ] 探针验证:`python probe_translate.py`(音频→翻译文本链路)

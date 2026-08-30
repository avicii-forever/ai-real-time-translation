# -*- coding: utf-8 -*-
"""后端补丁:让 duplex 的 KV 滑窗可配,修长会话退化。

问题(2026-08-27 定位):
  hard-listen(listen_prob_scale<=0.05)把 <|listen|> 的 logit 打成 -inf,模型
  永远进不了 LISTEN 分支 -> `slide_last_was_listen` 恒为 false -> 滑窗判定里的
  `mid_speak` 恒为 true -> **常规滑窗被永久 defer**,只剩 n_ctx-512 的紧急
  force slide 兜底。且保留量写死 `max(n_ctx/4, 2048)`,滑完模型仍盯着自己最近
  ~114s 的输出,跑到 ~160s 必然退化(-c 16384 漂成英文转写;-c 3072 变编号列表幻觉)。

改动(三处,全部向后兼容,0/未配置 = 原行为):
  1. omni.h    新增 duplex_slide_keep_tokens / duplex_slide_trigger
  2. omni.cpp  trigger 与 target_keep_tokens 改为可配;hard-listen 下不再让
               mid_speak 挡住常规滑窗
  3. ws_handler.cpp  session.init 的 config 里解析这两个新参数

用法(在节点上):
  python3 duplex_slide_config.py            # 打补丁
  python3 duplex_slide_config.py --revert   # 还原
"""
import argparse
import pathlib
import shutil
import sys

ROOT = pathlib.Path("/workspace/llama.cpp-omni")
SUFFIX = ".bak.slide_config"
# 三个文件改完都会出现这个标识,用来判断是否已打过补丁
MARKER = "duplex_slide_keep_tokens"

OMNI_H = ROOT / "tools/omni/omni.h"
OMNI_CPP = ROOT / "tools/omni/omni.cpp"
WS_CPP = ROOT / "tools/server/ws_handler.cpp"

# ---------------- omni.h ----------------
H_ANCHOR = "    float listen_prob_scale = 1.0f;\n"
H_ADD = H_ANCHOR + """
    // 🔧 [长时流式翻译] duplex 滑窗可配参数(0 = 保持原有默认行为)
    // hard-listen 下模型永不进 LISTEN 分支,slide_last_was_listen 恒 false,
    // 常规滑窗会被永久 defer;保留量又写死 max(n_ctx/4,2048),模型始终盯着
    // 自己最近 ~114s 的输出 -> ~160s 必然退化。这两个参数用来把窗口压下去。
    int duplex_slide_keep_tokens = 0;   // >0: 滑窗后保留的 token 数
    int duplex_slide_trigger     = 0;   // >0: n_past 超过该值即触发滑窗
"""

# ---------------- omni.cpp ----------------
CPP_TRIGGER_OLD = \
    "        const int duplex_trigger = std::max(ctx_omni->n_keep, n_ctx - 2048);\n"
CPP_TRIGGER_NEW = """        // 🔧 可配触发点:默认 n_ctx-2048,配了 duplex_slide_trigger 就用它
        const int duplex_trigger = (ctx_omni->duplex_slide_trigger > 0)
            ? std::max(ctx_omni->n_keep + 1, ctx_omni->duplex_slide_trigger)
            : std::max(ctx_omni->n_keep, n_ctx - 2048);
"""

CPP_MIDSPEAK_OLD = """        bool mid_speak     = !ctx_omni->slide_last_was_listen.load();
        bool force_slide   = (ctx_omni->n_past + chunk_size >= n_ctx - 512);

        if (!force_slide && (generating || tts_busy || mid_speak)) {
            return;
        }
"""
CPP_MIDSPEAK_NEW = """        // 🔧 hard-listen(listen_prob_scale<=0.05)下 <|listen|> 的 logit 被设 -inf,
        // 模型永远停不下来:slide_last_was_listen 恒 false **且** text_streaming 恒 true。
        // 于是这里的 defer 条件永远成立,常规滑窗被永久跳过,只剩 n_ctx-512 的
        // force slide 兜底 —— 那一刀又深又晚(实测一次砍掉 1681 token ≈ 93s 上下文),
        // 正是长会话退化(漂成英文转写 / 编号列表幻觉)的根因。
        // 因此 hard-listen 下只保留 tts_busy 这一个保护(避免切断正在合成的配音),
        // 让滑窗按 trigger 频繁而**浅**地进行。
        // ⚠️ 实测结论(2026-08-27):绕过 generating 去频繁滑窗会**摧毁模型** ——
        // hard-listen 下 rounds 恒为 0,滑窗只能走"盲切尾部+位置整体左移"分支,
        // 在生成中途每 9s 切一次 -> KV 位置错乱 -> 输出退化成 "ABAABAAB" 乱码。
        // 所以这个 bypass 默认**关闭**,只有显式配了 duplex_slide_trigger 才启用,
        // 保证不配任何参数时行为与原版逐位一致。留着是为了后续实验。
        const bool hard_listen = (ctx_omni->listen_prob_scale <= 0.05f)
                                 && (ctx_omni->duplex_slide_trigger > 0);
        bool mid_speak     = !hard_listen && !ctx_omni->slide_last_was_listen.load();
        bool force_slide   = (ctx_omni->n_past + chunk_size >= n_ctx - 512);

        const bool defer = hard_listen ? tts_busy
                                       : (generating || tts_busy || mid_speak);
        if (!force_slide && defer) {
            return;
        }
"""

CPP_KEEP_OLD = \
    "            const int target_keep_tokens = std::max(n_ctx / 4, 2048);\n"
CPP_KEEP_NEW = """            // 🔧 可配保留量:默认 max(n_ctx/4,2048),配了就用配置值
            const int target_keep_tokens = (ctx_omni->duplex_slide_keep_tokens > 0)
                ? ctx_omni->duplex_slide_keep_tokens
                : std::max(n_ctx / 4, 2048);
"""
CPP_KEEP_COUNT = 2      # rounds<2 分支 + rounds>=2 分支各一处

# ---------------- ws_handler.cpp ----------------
WS_ANCHOR = """    if (init.config.contains("max_new_speak_tokens_per_chunk") && init.config.at("max_new_speak_tokens_per_chunk").is_number_integer()) {
        octx->max_new_speak_tokens_per_chunk = init.config.at("max_new_speak_tokens_per_chunk").get<int>();
    }
"""
WS_ADD = WS_ANCHOR + """    // 🔧 [长时流式翻译] duplex 滑窗可配参数
    if (init.config.contains("duplex_slide_keep_tokens") && init.config.at("duplex_slide_keep_tokens").is_number_integer()) {
        octx->duplex_slide_keep_tokens = init.config.at("duplex_slide_keep_tokens").get<int>();
    }
    if (init.config.contains("duplex_slide_trigger") && init.config.at("duplex_slide_trigger").is_number_integer()) {
        octx->duplex_slide_trigger = init.config.at("duplex_slide_trigger").get<int>();
    }
"""

EDITS = [
    (OMNI_H,   [(H_ANCHOR, H_ADD, 1)]),
    (OMNI_CPP, [(CPP_TRIGGER_OLD, CPP_TRIGGER_NEW, 1),
                (CPP_MIDSPEAK_OLD, CPP_MIDSPEAK_NEW, 1),
                (CPP_KEEP_OLD, CPP_KEEP_NEW, CPP_KEEP_COUNT)]),
    (WS_CPP,   [(WS_ANCHOR, WS_ADD, 1)]),
]


def backup(p):
    b = p.with_suffix(p.suffix + SUFFIX)
    if not b.exists():
        shutil.copy2(p, b)
        print(f"  备份 {b.name}")
    return b


def revert():
    for p, _ in EDITS:
        b = p.with_suffix(p.suffix + SUFFIX)
        if b.exists():
            shutil.copy2(b, p)
            print(f"还原 {p.name}")
        else:
            print(f"⚠️  没有备份,跳过 {p.name}")


def apply():
    # 先全部校验,任何一处对不上就整体不动 —— 避免改一半
    plans = []
    for p, edits in EDITS:
        s = p.read_text(encoding="utf-8")
        # 用补丁独有的标识判断是否已打过(不能拿 new 的开头判,那往往就是锚点本身)
        if MARKER in s:
            print(f"⚠️  {p.name} 已包含 {MARKER},似乎打过补丁了,先 --revert")
            return 1
        for old, new, count in edits:
            got = s.count(old)
            if got != count:
                print(f"❌ {p.name}: 期望 {count} 处匹配,实际 {got} 处\n"
                      f"   模式: {old.strip()[:80]}")
                return 1
        plans.append((p, edits, s))

    for p, edits, s in plans:
        backup(p)
        for old, new, count in edits:
            s = s.replace(old, new, count)
        p.write_text(s, encoding="utf-8")
        print(f"✅ 已改 {p.name}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    if a.revert:
        revert()
        sys.exit(0)
    sys.exit(apply())

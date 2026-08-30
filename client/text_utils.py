# -*- coding: utf-8 -*-
"""译文文本清洗 —— duplex 输出的通用修正。

duplex 后端把空格转义成下划线("I_have_a"),直接显示会是 "I_have_a";
还原成空格后,中文目标语言会变成 "今 天 我 们",所以还要去掉 CJK 之间的空格。
两条管线(pipeline_ws / pipeline_media)共用这里的实现。
"""
import re

# CJK 标点 + 汉字 + 全角符号(,。!?等)
_CJK = r"　-〿一-鿿＀-￯"
# 模型输出的中文里常混半角标点,一并当作"贴着中文写"的标点处理
_PUNCT = r",.!?;:、，。！？；：…"

_CJK_SPACE = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")
_SPACE_BEFORE_PUNCT = re.compile(rf"\s+(?=[{_PUNCT}])")
_SPACE_AFTER_PUNCT = re.compile(rf"(?<=[{_PUNCT}])\s+(?=[{_CJK}])")


def fix_output(text):
    """下划线还原成空格 -> 压缩空白 -> 去掉中文之间/标点周围的空格。

    中文与拉丁(GPU / A100)之间保留一个空格,更易读。
    """
    if not text:
        return ""
    t = text.replace("_", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = _CJK_SPACE.sub("", t)
    t = _SPACE_BEFORE_PUNCT.sub("", t)
    t = _SPACE_AFTER_PUNCT.sub("", t)
    return t

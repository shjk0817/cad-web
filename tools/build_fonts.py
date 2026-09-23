# -*- coding: utf-8 -*-
"""生成前端 dxf-viewer 使用的中文字体子集（静态 TTF）。

源字体：C:\\Windows\\Fonts\\NotoSansSC-VF.ttf（SIL OFL-1.1，可随项目分发）。
可变字体先实例化为 Regular(wght=400)，再按 GB2312 全部汉字 + ASCII +
工程常用符号（° ± ∅ 希腊字母等）子集化，输出到
frontend/public/fonts/noto-sc-regular.ttf，供浏览器缓存复用。
"""
from __future__ import annotations

import os

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

SRC_FONT = r"C:\Windows\Fonts\NotoSansSC-VF.ttf"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "frontend", "public", "fonts"))
OUT_FONT = os.path.join(OUT_DIR, "noto-sc-regular.ttf")


def build_charset() -> str:
    """返回需要保留的全部字符。"""
    chars: set = set()

    # ASCII 可见字符
    chars.update(chr(c) for c in range(0x20, 0x7F))
    # Latin-1：不间断空格、° ± ² ³ × ÷
    for c in (0x00A0, 0x00B0, 0x00B1, 0x00B2, 0x00B3, 0x00D7, 0x00F7):
        chars.add(chr(c))
    # 希腊字母（工程标注常用）
    chars.update(chr(c) for c in range(0x0391, 0x03A2))
    chars.update(chr(c) for c in range(0x03A3, 0x03A9))
    chars.update(chr(c) for c in range(0x03B1, 0x03C9))
    # 通用标点与排版符号
    chars.update(chr(c) for c in range(0x2010, 0x2028))
    chars.add(chr(0x2030))  # ‰
    chars.add(chr(0x2032))  # ′
    chars.add(chr(0x2033))  # ″
    chars.add(chr(0x2103))  # ℃
    chars.add(chr(0x2109))  # ℉
    # 数学运算符（含 U+2205 ∅，对应 CAD %%c）
    chars.update(chr(c) for c in range(0x2200, 0x22FF))
    # CJK 标点
    chars.update(chr(c) for c in range(0x3000, 0x303F))
    # 全角 ASCII
    chars.update(chr(c) for c in range(0xFF01, 0xFF5F))
    # GB2312 一、二级汉字（6763 字）
    for b1 in range(0xB0, 0xF8):
        for b2 in range(0xA1, 0xFF):
            try:
                chars.add(bytes([b1, b2]).decode("gb2312"))
            except UnicodeDecodeError:
                pass

    return "".join(sorted(chars))


def main() -> None:
    if not os.path.isfile(SRC_FONT):
        raise SystemExit("未找到源字体：%s" % SRC_FONT)

    font = TTFont(SRC_FONT)
    # 可变字体实例化为 Regular
    instancer.instantiateVariableFont(font, {"wght": 400}, inplace=True)

    subsetter = subset.Subsetter()
    subsetter.populate(text=build_charset())
    subsetter.subset(font)

    os.makedirs(OUT_DIR, exist_ok=True)
    font.save(OUT_FONT)
    print("已生成：%s (%.1f KB)" % (OUT_FONT, os.path.getsize(OUT_FONT) / 1024))


if __name__ == "__main__":
    main()

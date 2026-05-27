#!/usr/bin/env python3
"""测试翻译+点击查词功能 - 用已有简报HTML"""
import sys
sys.path.insert(0, '.')
from daily_briefing import ensure_bilingual_and_dict, get_config
import os

# 设置环境变量
os.environ['DEEPSEEK_API_KEY'] = 'sk-c6949a1a0b864d2fa6f4b4bc94ee1fa9'
os.environ['TAVILY_API_KEY'] = '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'

config = get_config()

# 读取今天已有的简报
input_path = r'E:\workbuddy\每日世界期刊\每日简报-2026-05-27.html'
output_path = r'E:\workbuddy\每日世界期刊\每日简报-2026-05-27-v6.html'

with open(input_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

print(f"原始HTML大小: {len(html_content)} 字符")
print("开始运行 ensure_bilingual_and_dict ...")

html_result = ensure_bilingual_and_dict(html_content, config)

print(f"处理后HTML大小: {len(html_result)} 字符")

with open(output_path, 'w', encoding='utf-8') as f:
    f.write(html_result)

print(f"已保存到: {output_path}")

#!/usr/bin/env python3
"""
每日全球重要动态简报 - GitHub Actions 版
==========================================
改造自 cloud/daily_briefing.py，使用环境变量代替 config.json，
适合在 GitHub Actions / 任意 CI 环境中运行。

环境变量（在 GitHub Secrets 中配置）:
  DEEPSEEK_API_KEY    DeepSeek API 密钥 (sk-xxx)
  TAVILY_API_KEY      Tavily Search API 密钥 (tvly-xxx)
  EMAIL_SENDER        发件邮箱 (xxx@qq.com)
  EMAIL_PASSWORD      QQ 邮箱 SMTP 授权码
  EMAIL_RECEIVER      收件邮箱

本地测试:
  export DEEPSEEK_API_KEY=sk-xxx
  export TAVILY_API_KEY=tvly-xxx
  export EMAIL_SENDER=xxx@qq.com
  export EMAIL_PASSWORD=xxx
  export EMAIL_RECEIVER=xxx@qq.com
  python daily_briefing.py
  python daily_briefing.py --no-email
  python daily_briefing.py --date 2026-05-27
"""

import os
import sys
import argparse
import datetime
import smtplib
import logging
import json
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from pathlib import Path

import requests
from openai import OpenAI

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("daily-briefing")


# ============================================================
# 配置（全部从环境变量读取）
# ============================================================

def get_config() -> dict:
    """从环境变量读取配置，缺失时报错退出"""
    errors = []

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()
    email_sender = os.environ.get("EMAIL_SENDER", "").strip()
    email_password = os.environ.get("EMAIL_PASSWORD", "").strip()
    email_receiver = os.environ.get("EMAIL_RECEIVER", email_sender).strip()

    if not deepseek_key:
        errors.append("DEEPSEEK_API_KEY 未设置")
    if not tavily_key:
        errors.append("TAVILY_API_KEY 未设置")

    if errors:
        log.error("缺少必要的环境变量:")
        for e in errors:
            log.error(f"  - {e}")
        log.error("")
        log.error("本地测试时请先 export 这些变量。")
        log.error("GitHub Actions 中请在 Settings → Secrets → Actions 中添加。")
        sys.exit(1)

    return {
        "deepseek": {
            "api_key": deepseek_key,
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        },
        "search": {
            "tavily_api_key": tavily_key,
        },
        "email": {
            "enabled": bool(email_sender and email_password),
            "smtp_host": "smtp.qq.com",
            "smtp_port": 587,
            "sender": email_sender,
            "password": email_password,
            "receiver": email_receiver or email_sender,
        },
        "output": {
            "dir": os.environ.get("OUTPUT_DIR", "output"),
            "keep_days": int(os.environ.get("KEEP_DAYS", "30")),
        },
    }


# ============================================================
# 网页搜索 (Tavily)
# ============================================================

def search_tavily(query: str, api_key: str, max_results: int = 5) -> list[dict]:
    """使用 Tavily Search API 搜索"""
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in results
        ]
    except Exception as e:
        log.warning(f"Tavily 搜索失败 [{query[:40]}]: {e}")
        return []


# ============================================================
# 搜索关键词矩阵（双语）
# ============================================================

def get_search_queries() -> list[tuple[str, str, str]]:
    """返回 (板块名, 语言, 关键词) 列表"""
    today = datetime.date.today()
    month_cn = today.strftime("%Y年%m月")

    return [
        # 生命科学
        ("生命科学", "en", "gene therapy clinical trial breakthrough 2026"),
        ("生命科学", "en", "Nature medicine NEJM latest breakthrough May 2026"),
        ("生命科学", "zh", "基因治疗 临床突破 2026"),
        ("生命科学", "zh", "新药获批 中国 中科院 最新发现"),

        # 经济金融
        ("经济金融", "en", "S&P 500 Fed interest rate IMF global economy 2026"),
        ("经济金融", "en", "stock market AI investment merger acquisition 2026"),
        ("经济金融", "zh", "央行 货币政策 A股 经济数据 2026"),
        ("经济金融", "zh", "人民币汇率 进出口 中国经济 最新"),

        # AI
        ("人工智能", "en", "OpenAI Google AI model LLM breakthrough 2026"),
        ("人工智能", "en", "DeepSeek Anthropic Claude AI latest news 2026"),
        ("人工智能", "zh", "大模型 人工智能 突破 中国 2026"),
        ("人工智能", "zh", "DeepSeek 月之暗面 智谱 通义千问 最新"),

        # 硬科技
        ("硬科技", "en", "semiconductor chip TSMC Samsung Intel 2nm latest 2026"),
        ("硬科技", "en", "quantum computing breakthrough latest 2026"),
        ("硬科技", "zh", "芯片 半导体 华为 中芯国际 突破 2026"),
        ("硬科技", "zh", "量子计算 光刻 先进制程 中国"),

        # 材料科学
        ("材料科学", "en", "Nature Materials battery perovskite solar cell 2026"),
        ("材料科学", "en", "new material discovery breakthrough energy 2026"),
        ("材料科学", "zh", "材料科学 钙钛矿 固态电池 突破 2026"),
        ("材料科学", "zh", "新材料 中科院 清华 能源材料"),

        # 中国政策
        ("中国政策", "en", "China policy regulation technology economy 2026"),
        ("中国政策", "zh", "国务院 发改委 工信部 政策 新规 2026"),
        ("中国政策", "zh", "数据局 数字经济 算力 监管"),

        # 地缘政治
        ("地缘政治", "en", "geopolitics US China trade war sanctions 2026"),
        ("地缘政治", "en", "semiconductor export control supply chain 2026"),
        ("地缘政治", "zh", "中美关系 出口管制 半导体 2026"),
        ("地缘政治", "zh", "一带一路 供应链 地缘 最新"),

        # 中国科技专项补充扫描
        ("补充扫描", "zh", f"华为 发布 最新 {month_cn}"),
        ("补充扫描", "zh", f"比亚迪 宁德时代 中芯国际 突破 {month_cn}"),
        ("补充扫描", "zh", f"中科院 清华 浙大 研究 突破 2026"),
        ("补充扫描", "zh", f"中国科学家 首次 发现 发明 2026"),
    ]


# ============================================================
# 搜索阶段
# ============================================================

def collect_news(config: dict) -> str:
    """执行所有搜索，返回汇总的新闻文本"""
    api_key = config["search"]["tavily_api_key"]
    queries = get_search_queries()
    all_results = {}
    seen_urls = set()

    log.info(f"开始搜索: {len(queries)} 组关键词...")

    for i, (section, lang, query) in enumerate(queries):
        log.info(f"  [{i+1}/{len(queries)}] [{section}/{lang}] {query[:55]}...")
        results = search_tavily(query, api_key, max_results=5)

        for r in results:
            url = r["url"]
            if url not in seen_urls:
                seen_urls.add(url)
                all_results[url] = {
                    "section": section,
                    "title": r["title"],
                    "url": url,
                    "content": r["content"][:300],
                }

    log.info(f"搜索完成: 去重后共 {len(all_results)} 条结果")

    news_text = ""
    for i, (url, item) in enumerate(all_results.items()):
        news_text += f"\n--- 新闻 #{i+1} [{item['section']}] ---\n"
        news_text += f"标题: {item['title']}\n"
        news_text += f"链接: {item['url']}\n"
        news_text += f"摘要: {item['content']}\n"

    return news_text


# ============================================================
# 生成阶段
# ============================================================

BRIEFING_SYSTEM_PROMPT = """你是一位专业的全球动态分析师。你需要基于提供的新闻搜索结果，生成一份精炼的每日全球重要动态简报 HTML。

## 核心原则
中国是当今全球最重要的科技与商业中心之一。每个板块都必须覆盖中国动态，不能把"中国内容"局限于"政策监管"一个板块。

## 收集要求
从搜索结果中筛选最重要的 18-22 条动态，分布在以下 7 个板块:
1. 🧬 生命科学/医学 (2-3条)
2. 💰 经济/金融 (2-3条)
3. 🤖 人工智能 (2-3条)
4. 💻 科技/硬科技 (3-4条)
5. 🔬 材料科学 (2-3条)
6. 🇨🇳 中国政策与监管 (2-3条)
7. 🌍 地缘政治与国际关系 (2-3条)

## 质量要求
- 优先选择顶级期刊/权威来源 (Nature/Science/Cell/NEJM/新华社/人民日报等)
- 标记风险: 🔴重大风险 / 🟡值得观察
- 星级: ⭐⭐⭐⭐⭐(顶刊首发) / ⭐⭐⭐⭐(权威机构) / ⭐⭐⭐(一般可靠)
- 每个板块后加 50-100 字趋势关联简评

## 🌐 双语 + 英语学习要求（每条新闻必须包含）
每条新闻**必须**包含英语学习元素，帮助读者在阅读新闻的同时积累专业英语词汇：

1. **英文标题**（class="en-title"）：将中文新闻标题翻译为专业英文表述，用词准确、地道
2. **English Summary**（class="en-summary"）：2-3 句精炼英文摘要，概括核心内容，使用专业但不过于晦涩的英语
3. **Key Vocabulary**（class="vocab-card"）：提取 3-5 个核心术语，中英对照 + 简短释义

### 示例：
```html
<div class="bilingual">
  <p class="en-title">CRISPR Gene Editing Breakthrough: Guide Molecule Switches from RNA to DNA</p>
  <p class="en-summary">University of Florida researchers achieved a breakthrough in CRISPR gene editing by replacing the traditional RNA guide with a more stable DNA guide, significantly improving editing precision and reducing off-target effects. The DNA-guided system demonstrated 100% accuracy in viral detection tests.</p>
  <div class="vocab-card">
    <div class="vocab-title">📖 Key Vocabulary</div>
    <div class="vocab-list">
      <span class="vocab-item"><b>gene editing</b> 基因编辑</span>
      <span class="vocab-item"><b>guide RNA</b> 向导RNA</span>
      <span class="vocab-item"><b>off-target effect</b> 脱靶效应</span>
      <span class="vocab-item"><b>viral detection</b> 病毒检测</span>
    </div>
  </div>
</div>
```

**每条新闻都必须包含这个双语模块！这是简报的英语学习功能。** 确保英文表述专业、地道，词汇选取有学习价值。

## 🔴 深度解读要求（每条新闻必须包含）
每条新闻**必须**包含一个 `<details class="deepdive">` 折叠区域，内含三层深度解读：

1. **📌 是什么**（class="dl dl-what"）：用通俗语言解释这条新闻的核心内容。用比喻帮助理解，指出突破点在哪。
2. **📈 前景**（class="dl dl-prospect"）：分析短期(1-3年)和中长期(3-10年)的应用前景，列举2-3个具体场景，评估商业化/落地时间线。
3. **🔮 畅想**（class="dl dl-vision"）：大胆设想如果这项技术/政策/趋势完全实现，世界会变成什么样？从社会、经济、伦理等角度展开。

### 示例：
```html
<details class="deepdive">
<summary>📖 深度解读</summary>
<div class="deepdive-content">
  <p class="dl dl-what"><span class="dl-label">📌 是什么：</span>CRISPR基因编辑技术一直依赖RNA作为向导，但RNA稳定性差、易降解。佛罗里达大学团队突破性地将向导分子从RNA换成DNA——更稳定、更便宜、更精准。这就像把"纸质地图"换成了"石板地图"。</p>
  <p class="dl dl-prospect"><span class="dl-label">📈 前景：</span>① 体外诊断——病毒检测准确率已达100%；② 基因治疗——更低脱靶率意味着更安全的体内编辑；③ 农业育种——精准编辑作物基因。制备成本降低60%，可快速下沉到中低收入国家。3-5年内可能进入临床试验。</p>
  <p class="dl dl-vision"><span class="dl-label">🔮 畅想：</span>如果DNA-CRISPR普及，基因编辑可能从"高端科研专用"变成"社区医院也能做"的常规操作。当脱靶率降到接近零，人类可能首次真正拥有"编写生命代码"的能力——不是粗糙地剪切粘贴，而是像程序员修改代码一样精确地编辑基因组。</p>
</div>
</details>
```

**每条新闻都必须有这个 deepdive 结构，不要省略！这是简报最有价值的部分。**

## 每条新闻的完整结构
每条新闻的 HTML 结构应为：
1. `.item-header` — 中文标题 + 星级 + 风险标签
2. `.item-summary` — 中文摘要
3. `.bilingual` — 英文标题 + 英文摘要 + 词汇卡（英语学习模块）
4. `.deepdive` — 📌是什么 + 📈前景 + 🔮畅想（深度解读模块）
5. `.item-source` — 来源链接

## 输出格式
直接输出完整 HTML，不要任何解释性文字，必须以 <!DOCTYPE html> 开头。

## 🔴 CSS 样式规范（必须使用以下样式，不要自己写 CSS！）
你必须在 `<style>` 中使用以下 CSS，可以微调颜色但**不要改变布局结构和移动端适配规则**：

```css
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Microsoft YaHei", "PingFang SC", sans-serif; max-width: 940px; margin: 0 auto; padding: 16px; background: #f5f7fa; }
header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); color: #fff; padding: 20px 22px; border-radius: 12px; margin-bottom: 20px; }
header h1 { font-size: 20px; margin-bottom: 4px; }
header p { font-size: 12px; opacity: 0.75; }
h2 { font-size: 16px; color: #1a1a2e; margin: 22px 0 12px 0; padding-bottom: 6px; border-bottom: 2px solid #e8ecf0; display: flex; align-items: center; gap: 8px; }
.item { background: #fff; border-left: 3px solid #4a90d9; border-radius: 0 8px 8px 0; padding: 12px 14px; margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.item-header { margin-bottom: 8px; }
.item-title { font-size: 15px; font-weight: 600; color: #222; line-height: 1.5; margin-bottom: 6px; word-break: break-word; }
.item-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.star-rating { font-size: 12px; white-space: nowrap; color: #f5a623; background: #fffbf0; padding: 2px 8px; border-radius: 10px; border: 1px solid #f5d88a; }
.risk-red { background: #ffebee; color: #c62828; font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 700; }
.risk-yellow { background: #fff8e1; color: #e65100; font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 600; }
.item-summary { font-size: 14px; color: #555; line-height: 1.7; margin-bottom: 8px; }
.item-source { font-size: 12px; color: #999; margin-top: 8px; }
.item-source a { color: #4a90d9; text-decoration: none; }
.bilingual { margin: 8px 0; padding: 10px 12px; background: linear-gradient(135deg, #e3f2fd, #e8eaf6); border-radius: 8px; border: 1px solid #bbdefb; }
.en-title { font-size: 13.5px; color: #1565c0; font-weight: 600; margin-bottom: 5px; line-height: 1.5; }
.en-summary { font-size: 13px; color: #37474f; line-height: 1.7; margin-bottom: 8px; }
.vocab-card { background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 8px 12px; }
.vocab-title { font-size: 12px; color: #6a1b9a; font-weight: 700; margin-bottom: 4px; }
.vocab-list { display: flex; flex-wrap: wrap; gap: 4px 10px; }
.vocab-item { font-size: 12px; color: #424242; line-height: 1.8; }
.vocab-item b { color: #1a237e; background: #e8eaf6; padding: 1px 5px; border-radius: 3px; font-size: 11px; }
.deepdive { margin-top: 8px; }
.deepdive summary { cursor: pointer; font-size: 13px; font-weight: 600; color: #1b5e20; padding: 6px 10px; background: linear-gradient(135deg, #e8f5e9, #f1f8e9); border-radius: 6px; border: 1px solid #c8e6c9; user-select: none; }
.deepdive-content { background: #f9fbf9; border: 1px solid #e0e8e0; border-radius: 0 0 6px 6px; padding: 10px 12px; margin-top: 2px; font-size: 13px; line-height: 1.75; color: #444; }
.deepdive-content .dl { margin: 5px 0; }
.deepdive-content .dl-label { font-weight: 700; }
.dl-what { color: #1565c0; }
.dl-prospect { color: #e65100; }
.dl-vision { color: #6a1b9a; }
.trend-comment { background: linear-gradient(135deg, #f3e5f5, #e8eaf6); border-left: 3px solid #9575cd; border-radius: 0 6px 6px 0; padding: 10px 14px; margin: 10px 0 18px 0; font-size: 13px; color: #4a148c; font-style: italic; line-height: 1.7; }
footer { text-align: center; color: #aaa; font-size: 11px; margin-top: 30px; padding: 12px; }
@media (max-width: 600px) {
  body { padding: 8px; }
  header { padding: 16px; border-radius: 10px; }
  header h1 { font-size: 17px; }
  header p { font-size: 11px; }
  h2 { font-size: 15px; margin: 18px 0 10px 0; }
  .item { padding: 10px 12px; margin-bottom: 10px; border-radius: 0 6px 6px 0; }
  .item-title { font-size: 14px; line-height: 1.6; }
  .item-summary { font-size: 13px; }
  .bilingual { padding: 8px 10px; }
  .en-title { font-size: 13px; }
  .en-summary { font-size: 12px; }
  .vocab-list { gap: 3px 8px; }
  .vocab-item { font-size: 11px; }
  .vocab-item b { font-size: 10.5px; }
  .deepdive summary { font-size: 12px; padding: 5px 8px; }
  .deepdive-content { font-size: 12px; padding: 8px 10px; }
  .trend-comment { font-size: 12px; padding: 8px 10px; }
}
```
"""

BRIEFING_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>每日全球重要动态简报 - {DATE}</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, "Microsoft YaHei", "PingFang SC", sans-serif; max-width: 940px; margin: 0 auto; padding: 16px; background: #f5f7fa; }
    header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); color: #fff; padding: 20px 22px; border-radius: 12px; margin-bottom: 20px; }
    header h1 { font-size: 20px; margin-bottom: 4px; }
    header p { font-size: 12px; opacity: 0.75; }
    h2 { font-size: 16px; color: #1a1a2e; margin: 22px 0 12px 0; padding-bottom: 6px; border-bottom: 2px solid #e8ecf0; display: flex; align-items: center; gap: 8px; }
    .item { background: #fff; border-left: 3px solid #4a90d9; border-radius: 0 8px 8px 0; padding: 12px 14px; margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
    .item-header { margin-bottom: 8px; }
    .item-title { font-size: 15px; font-weight: 600; color: #222; line-height: 1.5; margin-bottom: 6px; word-break: break-word; }
    .item-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .star-rating { font-size: 12px; white-space: nowrap; color: #f5a623; background: #fffbf0; padding: 2px 8px; border-radius: 10px; border: 1px solid #f5d88a; }
    .risk-red { background: #ffebee; color: #c62828; font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 700; }
    .risk-yellow { background: #fff8e1; color: #e65100; font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 600; }
    .item-summary { font-size: 14px; color: #555; line-height: 1.7; margin-bottom: 8px; }
    .item-source { font-size: 12px; color: #999; margin-top: 8px; }
    .item-source a { color: #4a90d9; text-decoration: none; }
    .bilingual { margin: 8px 0; padding: 10px 12px; background: linear-gradient(135deg, #e3f2fd, #e8eaf6); border-radius: 8px; border: 1px solid #bbdefb; }
    .en-title { font-size: 13.5px; color: #1565c0; font-weight: 600; margin-bottom: 5px; line-height: 1.5; }
    .en-summary { font-size: 13px; color: #37474f; line-height: 1.7; margin-bottom: 8px; }
    .vocab-card { background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 8px 12px; }
    .vocab-title { font-size: 12px; color: #6a1b9a; font-weight: 700; margin-bottom: 4px; }
    .vocab-list { display: flex; flex-wrap: wrap; gap: 4px 10px; }
    .vocab-item { font-size: 12px; color: #424242; line-height: 1.8; }
    .vocab-item b { color: #1a237e; background: #e8eaf6; padding: 1px 5px; border-radius: 3px; font-size: 11px; }
    .deepdive { margin-top: 8px; }
    .deepdive summary { cursor: pointer; font-size: 13px; font-weight: 600; color: #1b5e20; padding: 6px 10px; background: linear-gradient(135deg, #e8f5e9, #f1f8e9); border-radius: 6px; border: 1px solid #c8e6c9; user-select: none; }
    .deepdive-content { background: #f9fbf9; border: 1px solid #e0e8e0; border-radius: 0 0 6px 6px; padding: 10px 12px; margin-top: 2px; font-size: 13px; line-height: 1.75; color: #444; }
    .deepdive-content .dl { margin: 5px 0; }
    .deepdive-content .dl-label { font-weight: 700; }
    .dl-what { color: #1565c0; }
    .dl-prospect { color: #e65100; }
    .dl-vision { color: #6a1b9a; }
    .trend-comment { background: linear-gradient(135deg, #f3e5f5, #e8eaf6); border-left: 3px solid #9575cd; border-radius: 0 6px 6px 0; padding: 10px 14px; margin: 10px 0 18px 0; font-size: 13px; color: #4a148c; font-style: italic; line-height: 1.7; }
    footer { text-align: center; color: #aaa; font-size: 11px; margin-top: 30px; padding: 12px; }
    @media (max-width: 600px) {
      body { padding: 8px; }
      header { padding: 16px; border-radius: 10px; }
      header h1 { font-size: 17px; }
      header p { font-size: 11px; }
      h2 { font-size: 15px; margin: 18px 0 10px 0; }
      .item { padding: 10px 12px; margin-bottom: 10px; border-radius: 0 6px 6px 0; }
      .item-title { font-size: 14px; line-height: 1.6; }
      .item-summary { font-size: 13px; }
      .bilingual { padding: 8px 10px; }
      .en-title { font-size: 13px; }
      .en-summary { font-size: 12px; }
      .vocab-list { gap: 3px 8px; }
      .vocab-item { font-size: 11px; }
      .vocab-item b { font-size: 10.5px; }
      .deepdive summary { font-size: 12px; padding: 5px 8px; }
      .deepdive-content { font-size: 12px; padding: 8px 10px; }
      .trend-comment { font-size: 12px; padding: 8px 10px; }
    }
  </style>
</head>
<body>
<header>
  <h1>每日全球重要动态简报</h1>
  <p>生成时间: {DATE} 08:00 CST | 数据来源: Tavily Search + DeepSeek AI 综合整理 | 💡 点击任意英文单词 → 中文释义</p>
</header>
"""


# ============================================================
# DeepSeek 翻译后处理 + 点击查词
# ============================================================

TRANSLATE_SYSTEM_PROMPT = """You are a professional bilingual translator specializing in science, technology, and finance news.

For each news item, output a JSON object with:
- "en_title": Professional English translation of the Chinese title (news headline style, concise and impactful)
- "en_summary": 2-3 sentence English summary capturing the key points (professional but accessible)
- "vocab": Array of 3-5 key technical terms, each with "en" (English term) and "zh" (Chinese translation)
- "dict": Object mapping lowercase English words/phrases from en_title and en_summary to their Chinese translations. Only include words that a Chinese reader learning English might not know. Skip basic words like "the", "is", "a", "in", "of", "and", "to", "for", "with", "on", "at", "by", "from", "as", "an", "be", "are", "was", "were", "has", "have", "had", "this", "that", "it", "not", "but", "or", "its", "can", "will", "may", "also", "into", "over", "than", "such", "through", "about", "between", "after", "before", "under", "during", "among", "both", "each", "other", "most", "more", "some", "any", "all", "no", "only", "own", "same", "so", "if", "we", "they", "he", "she", "what", "which", "who", "when", "where", "how", "up", "out", "just", "very", "even", "still", "already", "new", "first", "last", "long", "great", "little", "old", "big", "high", "small", "large", "next", "early", "young", "important", "few", "public", "bad", "same", "able".

Output ONLY a valid JSON array (one object per item). No markdown fences, no explanation.
Example: [{"en_title":"CRISPR Breakthrough","en_summary":"Researchers achieved...","vocab":[{"en":"gene editing","zh":"基因编辑"}],"dict":{"crispr":"规律间隔成簇短回文重复序列","breakthrough":"突破","gene editing":"基因编辑","off-target":"脱靶的"}}]"""


CLICK_WORD_CSS = """
/* 点击查词样式 */
.click-word { border-bottom: 1px dashed #7986cb; cursor: pointer; transition: background 0.2s; }
.click-word:hover { background: #e8eaf6; }
.click-tooltip { position: fixed; z-index: 9999; background: linear-gradient(135deg, #263238, #37474f); color: #fff; padding: 8px 14px; border-radius: 8px; font-size: 13px; line-height: 1.6; max-width: 280px; box-shadow: 0 4px 16px rgba(0,0,0,0.25); pointer-events: none; opacity: 0; transition: opacity 0.2s; }
.click-tooltip.show { opacity: 1; }
.click-tooltip .tw { font-weight: 700; color: #82b1ff; font-size: 14px; margin-bottom: 2px; }
.click-tooltip .tm { color: #b0bec5; font-size: 12px; }
@media (max-width: 600px) {
  .click-word { border-bottom-width: 0.5px; }
  .click-tooltip { font-size: 12px; max-width: 220px; padding: 6px 10px; }
  .click-tooltip .tw { font-size: 13px; }
}
"""

CLICK_WORD_JS = """
<script>
(function(){
  var D=__WORD_DICT__;
  if(!D||!Object.keys(D).length) return;
  /* 只扫描 .en-title 和 .en-summary 内的文本节点 */
  var targets=document.querySelectorAll('.en-title,.en-summary');
  targets.forEach(function(el){
    wrapWords(el,D);
  });
  function wrapWords(el,D){
    var walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT,null,false);
    var nodes=[];
    while(walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function(textNode){
      var text=textNode.textContent;
      /* 构建替换：匹配词典中的词（优先匹配长短语） */
      var keys=Object.keys(D).sort(function(a,b){return b.length-a.length;});
      var regex=new RegExp('\\\\b('+keys.map(function(k){return k.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&');}).join('|')+')\\\\b','gi');
      var hasMatch=regex.test(text);
      if(!hasMatch) return;
      regex.lastIndex=0;
      var frag=document.createDocumentFragment();
      var lastIdx=0;
      var m;
      while((m=regex.exec(text))!==null){
        if(m.index>lastIdx) frag.appendChild(document.createTextNode(text.slice(lastIdx,m.index)));
        var span=document.createElement('span');
        span.className='click-word';
        span.textContent=m[0];
        span.setAttribute('data-cn',D[m[1].toLowerCase()]||'');
        frag.appendChild(span);
        lastIdx=regex.lastIndex;
      }
      if(lastIdx<text.length) frag.appendChild(document.createTextNode(text.slice(lastIdx)));
      textNode.parentNode.replaceChild(frag,textNode);
    });
  }
  /* tooltip 逻辑 */
  var tip=document.createElement('div');
  tip.className='click-tooltip';
  document.body.appendChild(tip);
  var hideTimer;
  document.addEventListener('click',function(e){
    var w=e.target.closest('.click-word');
    if(w){
      e.preventDefault();
      e.stopPropagation();
      clearTimeout(hideTimer);
      var cn=w.getAttribute('data-cn');
      var en=w.textContent;
      tip.innerHTML='<div class="tw">'+en+'</div><div class="tm">'+cn+'</div>';
      var r=w.getBoundingClientRect();
      var left=r.left+r.width/2;
      var top=r.bottom+6;
      if(left+150>window.innerWidth) left=window.innerWidth-150;
      if(left<10) left=10;
      if(top+60>window.innerHeight) top=r.top-60;
      tip.style.left=left+'px';
      tip.style.top=top+'px';
      tip.classList.add('show');
      hideTimer=setTimeout(function(){tip.classList.remove('show');},3000);
    } else {
      tip.classList.remove('show');
    }
  },true);
})();
</script>
"""


def _extract_items_needing_translation(html: str) -> list[dict]:
    """从 HTML 中提取缺少 .bilingual 部分的新闻条目的中文标题和摘要。"""
    results = []
    item_starts = [m.start() for m in re.finditer(r'<div[^>]*class="item"[^>]*>', html)]

    for idx, start in enumerate(item_starts):
        end = item_starts[idx + 1] if idx + 1 < len(item_starts) else len(html)
        chunk = html[start:end]

        if 'class="bilingual"' in chunk:
            continue

        title_m = re.search(r'class="item-title"[^>]*>(.*?)</(?:p|div|span|h[1-6])', chunk, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() if title_m else ""

        summary_m = re.search(r'class="item-summary"[^>]*>(.*?)</(?:p|div)', chunk, re.DOTALL)
        summary = re.sub(r'<[^>]+>', '', summary_m.group(1)).strip() if summary_m else ""

        if title:
            results.append({"title": title, "summary": summary, "html_start": start, "html_end": end})

    return results


def _build_bilingual_html(translation: dict) -> str:
    """将一条翻译结果构建为 bilingual HTML 片段。"""
    vocab_lines = []
    for v in translation.get("vocab", []):
        vocab_lines.append(f'<span class="vocab-item"><b>{v["en"]}</b> {v["zh"]}</span>')
    vocab_html = "\n      ".join(vocab_lines) if vocab_lines else ""

    return (
        '\n<div class="bilingual">\n'
        f'  <p class="en-title">{translation.get("en_title", "")}</p>\n'
        f'  <p class="en-summary">{translation.get("en_summary", "")}</p>\n'
        '  <div class="vocab-card">\n'
        '    <div class="vocab-title">📖 Key Vocabulary</div>\n'
        '    <div class="vocab-list">\n'
        f'      {vocab_html}\n'
        '    </div>\n'
        '  </div>\n'
        '</div>'
    )


def ensure_bilingual_and_dict(html_content: str, config: dict) -> str:
    """后处理：确保每条新闻都有双语内容，并收集词典用于点击查词。
    1. 扫描缺少 .bilingual 的条目 → DeepSeek 翻译补全
    2. 从所有 .bilingual 区域提取英文文本 → DeepSeek 生成词典
    3. 注入点击查词 CSS + JS + 词典 JSON"""
    ds_config = config["deepseek"]
    client = OpenAI(api_key=ds_config["api_key"], base_url=ds_config["base_url"])

    # --- 步骤1: 补全缺失的双语内容 ---
    items = _extract_items_needing_translation(html_content)

    if items:
        log.info(f"📋 发现 {len(items)} 条新闻缺少双语内容，调用 DeepSeek 翻译...")
        items_text = ""
        for i, item in enumerate(items):
            items_text += f"\n#{i+1}\n标题: {item['title']}\n摘要: {item['summary']}\n"

        try:
            response = client.chat.completions.create(
                model=ds_config["model"],
                messages=[
                    {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Translate these {len(items)} news items to English:\n{items_text}"},
                ],
                temperature=0.1,
                max_tokens=4000,
            )
            result_text = response.choices[0].message.content.strip()
            if result_text.startswith("```"):
                lines = result_text.split("\n")
                result_text = "\n".join(lines[1:])
                if result_text.rstrip().endswith("```"):
                    result_text = result_text.rstrip()[:-3].strip()
            translations = json.loads(result_text)
            if not isinstance(translations, list):
                translations = [translations]
            log.info(f"✅ DeepSeek 翻译完成: {len(translations)} 条")

            # 从后向前注入
            for i in range(min(len(items), len(translations)) - 1, -1, -1):
                item = items[i]
                trans = translations[i]
                bilingual_html = _build_bilingual_html(trans)
                chunk = html_content[item["html_start"]:item["html_end"]]
                summary_end = re.search(
                    r'(class="item-summary"[^>]*>.*?</(?:p|div)>)',
                    chunk, re.DOTALL
                )
                if summary_end:
                    insert_pos = item["html_start"] + summary_end.end()
                    html_content = html_content[:insert_pos] + bilingual_html + html_content[insert_pos:]
                else:
                    header_end = re.search(
                        r'(class="item-header"[^>]*>.*?</div>)',
                        chunk, re.DOTALL
                    )
                    if header_end:
                        insert_pos = item["html_start"] + header_end.end()
                        html_content = html_content[:insert_pos] + bilingual_html + html_content[insert_pos:]
            log.info("✅ 双语内容注入完成")
        except Exception as e:
            log.warning(f"⚠️ 翻译后处理失败: {e}")

    # --- 步骤2: 从所有 .bilingual 区域提取英文文本，生成词典 ---
    log.info("📖 生成点击查词词典...")
    bilingual_blocks = re.findall(
        r'class="bilingual"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        html_content, re.DOTALL
    )
    # 也尝试简单匹配
    if not bilingual_blocks:
        bilingual_blocks = re.findall(
            r'class="bilingual"[^>]*>(.*?)(?=<div class="bilingual"|</div>\s*</div>\s*</div>)',
            html_content, re.DOTALL
        )

    # 提取 en-title 和 en-summary 的纯文本
    all_en_text = ""
    for block in bilingual_blocks:
        en_titles = re.findall(r'class="en-title"[^>]*>(.*?)</p>', block, re.DOTALL)
        en_summaries = re.findall(r'class="en-summary"[^>]*>(.*?)</p>', block, re.DOTALL)
        for t in en_titles:
            all_en_text += re.sub(r'<[^>]+>', '', t).strip() + " "
        for s in en_summaries:
            all_en_text += re.sub(r'<[^>]+>', '', s).strip() + " "

    # 同时从 vocab-card 提取已有词汇
    existing_vocab = {}
    vocab_items = re.findall(r'class="vocab-item"[^>]*><b>([^<]+)</b>\s*([^<]+)', html_content)
    for en_word, zh_meaning in vocab_items:
        existing_vocab[en_word.strip().lower()] = zh_meaning.strip()

    word_dict = dict(existing_vocab)

    # 如果有额外英文文本，调用 DeepSeek 补充词典
    if all_en_text.strip():
        # 提取英文单词（去重）
        en_words = set(re.findall(r'[a-zA-Z][a-zA-Z\-]{3,}', all_en_text))
        # 去掉已知的
        en_words = {w.lower() for w in en_words} - set(word_dict.keys())
        # 过滤常见虚词
        stop_words = {
            'that', 'this', 'with', 'from', 'have', 'been', 'were', 'will',
            'which', 'their', 'about', 'would', 'could', 'other', 'more',
            'than', 'also', 'into', 'over', 'such', 'through', 'after',
            'before', 'between', 'under', 'during', 'among', 'both',
            'each', 'most', 'some', 'very', 'even', 'still', 'already',
            'first', 'last', 'long', 'great', 'little', 'many', 'much',
            'only', 'just', 'like', 'well', 'back', 'then', 'there',
            'these', 'those', 'being', 'having', 'doing', 'using', 'based',
            'while', 'where', 'when', 'what', 'which', 'they', 'them',
            'against', 'within', 'without', 'should', 'might', 'must',
            'however', 'although', 'because', 'since', 'until', 'though',
        }
        en_words -= stop_words

        if en_words:
            words_list = sorted(en_words)[:80]  # 最多80个词
            dict_prompt = f"""For these English words/phrases from a Chinese-English bilingual news briefing, provide concise Chinese translations.
A Chinese reader is learning English from this briefing. Skip any word too basic for a high school student.

Words: {', '.join(words_list)}

Output ONLY a JSON object mapping each word to its Chinese translation. No markdown fences.
Example: {{"semiconductor":"半导体","breakthrough":"突破"}}"""

            try:
                response = client.chat.completions.create(
                    model=ds_config["model"],
                    messages=[
                        {"role": "system", "content": "You are a concise English-Chinese dictionary. Output only valid JSON."},
                        {"role": "user", "content": dict_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=2000,
                )
                dict_text = response.choices[0].message.content.strip()
                if dict_text.startswith("```"):
                    lines = dict_text.split("\n")
                    dict_text = "\n".join(lines[1:])
                    if dict_text.rstrip().endswith("```"):
                        dict_text = dict_text.rstrip()[:-3].strip()
                extra_dict = json.loads(dict_text)
                if isinstance(extra_dict, dict):
                    word_dict.update(extra_dict)
                    log.info(f"✅ DeepSeek 词典生成: {len(extra_dict)} 个新词")
            except Exception as e:
                log.warning(f"⚠️ 词典生成失败: {e}")

    log.info(f"📖 最终词典: {len(word_dict)} 个词条")

    # --- 步骤3: 注入点击查词功能 ---
    if word_dict:
        # 注入 CSS（在 </style> 前）
        if '</style>' in html_content:
            html_content = html_content.replace('</style>', CLICK_WORD_CSS + '\n</style>', 1)

        # 注入 JS + 词典（在 </body> 前）
        dict_json = json.dumps(word_dict, ensure_ascii=False)
        js_code = CLICK_WORD_JS.replace('__WORD_DICT__', dict_json)
        if '</body>' in html_content:
            html_content = html_content.replace('</body>', js_code + '\n</body>', 1)
        else:
            html_content += js_code

        log.info(f"✅ 点击查词功能注入完成 ({len(word_dict)} 词)")

    # 统计
    total_items = html_content.count('class="item"')
    total_bilingual = html_content.count('class="bilingual"')
    log.info(f"📊 双语覆盖率: {total_bilingual}/{total_items}")

    return html_content


# ============================================================
# 生成阶段
# ============================================================
    """调用 DeepSeek API 生成简报 HTML"""
    ds_config = config["deepseek"]
    client = OpenAI(api_key=ds_config["api_key"], base_url=ds_config["base_url"])

    user_message = (
        f"日期: {today_str}\n\n"
        f"以下是今天搜索到的新闻原始内容，请从中筛选最重要的动态，生成完整 HTML 简报:\n\n"
        f"{news_text}\n\n"
        f"请直接输出完整 HTML，以 <!DOCTYPE html> 开头，不要任何 markdown 代码块包裹。"
    )

    log.info(f"调用 DeepSeek API (model: {ds_config['model']})...")

    response = client.chat.completions.create(
        model=ds_config["model"],
        messages=[
            {"role": "system", "content": BRIEFING_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.7,
        max_tokens=16000,
    )

    html_content = response.choices[0].message.content.strip()

    # 去除可能的 markdown 包裹
    if html_content.startswith("```html"):
        html_content = html_content[7:]
        if html_content.endswith("```"):
            html_content = html_content[:-3]
        html_content = html_content.strip()
    elif html_content.startswith("```"):
        html_content = html_content[3:]
        if html_content.endswith("```"):
            html_content = html_content[:-3]
        html_content = html_content.strip()

    # 确保以 DOCTYPE 开头
    if "<!DOCTYPE" not in html_content[:100] and "<html" not in html_content[:100]:
        log.warning("AI 输出未包含完整 HTML 结构，尝试补充 header")
        header = BRIEFING_HTML_TEMPLATE.replace("{DATE}", today_str)
        html_content = header + html_content + "\n</body>\n</html>"

    # 修复 AI 输出截断问题：如果缺少 </body>，说明 HTML 被截断
    if "</body>" not in html_content:
        log.warning("AI 输出被截断（缺少 </body>），尝试修复...")
        import re as _re
        # 找到最后一个完整闭合标签，截断后面不完整的 HTML
        last_complete = 0
        for tag in ['</div>', '</p>', '</details>', '</section>', '</span>']:
            idx = html_content.rfind(tag)
            if idx + len(tag) > last_complete:
                last_complete = idx + len(tag)
        if last_complete > 0 and last_complete < len(html_content):
            trimmed = html_content[last_complete:].strip()
            if trimmed:
                log.info(f"  截去末尾 {len(html_content) - last_complete} 字符不完整 HTML")
                html_content = html_content[:last_complete]
        # 添加 footer 和闭合标签
        html_content += "\n<footer><p>每日全球重要动态简报 · 自动生成</p></footer>\n</body>\n</html>"
        log.info("  已补全 </body></html> 闭合标签")

    log.info(f"简报生成完成: {len(html_content):,} 字符")
    return html_content


# ============================================================
# 文件保存
# ============================================================

def save_briefing(html_content: str, config: dict, today_str: str) -> str:
    """保存 HTML 文件到 output/ 目录"""
    output_dir = Path(config["output"]["dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"每日简报-{today_str}.html"
    filepath = output_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    log.info(f"简报已保存: {filepath} ({filepath.stat().st_size // 1024} KB)")
    return str(filepath)


# ============================================================
# 邮件发送
# ============================================================

def send_email(html_content: str, filepath: str, config: dict, today_str: str):
    """通过 QQ 邮箱 SMTP 发送简报"""
    email_config = config.get("email", {})

    if not email_config.get("enabled", False):
        log.info("邮件未配置或已禁用，跳过发送")
        return

    sender = email_config["sender"]
    password = email_config["password"]
    receiver = email_config["receiver"]

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = Header(f"每日全球重要动态简报 - {today_str}", "utf-8")

    text_part = MIMEText(
        f"今日简报已生成。\n\n"
        f"日期: {today_str}\n"
        f"文件: {filepath}\n\n"
        f"本邮件包含 HTML 版本，请直接查看。",
        "plain",
        "utf-8",
    )
    html_part = MIMEText(html_content, "html", "utf-8")
    msg.attach(text_part)
    msg.attach(html_part)

    try:
        log.info(f"发送邮件 → {receiver}...")
        server = smtplib.SMTP("smtp.qq.com", 587, timeout=30)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [receiver], msg.as_string())
        server.quit()
        log.info("邮件发送成功!")
    except Exception as e:
        log.error(f"邮件发送失败: {e}")
        log.info(f"简报文件已保存: {filepath}")


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="每日全球重要动态简报生成器 (GitHub Actions 版)")
    parser.add_argument("--date", type=str, help="指定日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--no-email", action="store_true", help="不发送邮件")
    parser.add_argument("--search-only", action="store_true", help="仅搜索，不生成简报")
    args = parser.parse_args()

    today = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")

    log.info("===== 每日简报生成器 (GitHub Actions 版) =====")
    log.info(f"日期: {today_str}")

    config = get_config()

    # 阶段 1: 搜索
    log.info("【阶段1】搜索新闻...")
    news_text = collect_news(config)

    if args.search_only:
        print(news_text)
        return

    if not news_text.strip():
        log.error("未搜索到任何新闻，请检查 TAVILY_API_KEY 是否正确")
        sys.exit(1)

    # 阶段 2: 生成
    log.info("【阶段2】AI 生成简报...")
    html_content = generate_briefing(news_text, config, today_str)

    # 阶段 2.5: 翻译后处理 + 点击查词
    log.info("【阶段2.5】翻译保障 + 点击查词词典...")
    html_content = ensure_bilingual_and_dict(html_content, config)

    # 阶段 3: 保存
    log.info("【阶段3】保存文件...")
    filepath = save_briefing(html_content, config, today_str)

    # 阶段 4: 邮件
    if not args.no_email:
        log.info("【阶段4】发送邮件...")
        send_email(html_content, filepath, config, today_str)

    log.info("===== 完成! =====")
    log.info(f"文件: {filepath}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
每日全球重要动态简报 - GitHub Actions 版（升级版 v2）
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
  python daily_briefing.py --no-email
  python daily_briefing.py --date 2026-05-27 --no-email
"""

import os
import sys
import argparse
import datetime
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from pathlib import Path

import requests
from openai import OpenAI

# ============================================================
# 权威来源域名白名单
# ============================================================

AUTHORITATIVE_DOMAINS_EN = [
    # 通讯社 / 国际大报
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk",
    "bloomberg.com", "ft.com", "wsj.com", "economist.com",
    "nytimes.com", "washingtonpost.com", "theguardian.com",
    # 顶级学术期刊
    "nature.com", "science.org", "nejm.org", "thelancet.com",
    "cell.com", "sciencemag.org", "nih.gov", "pubmed.ncbi.nlm.nih.gov",
    "arxiv.org", "ieee.org", "sciencedirect.com",
    # 科技媒体
    "techcrunch.com", "wired.com", "arstechnica.com", "theverge.com",
    # 国际机构
    "who.int", "imf.org", "worldbank.org", "federalreserve.gov",
    "bis.org", "un.org",
]

AUTHORITATIVE_DOMAINS_ZH = [
    # 官方媒体
    "xinhuanet.com", "people.com.cn", "chinadaily.com.cn",
    "gov.cn", "ndrc.gov.cn", "mofcom.gov.cn", "pbc.gov.cn",
    "stats.gov.cn", "miit.gov.cn",
    # 学术机构
    "cas.cn", "sciencenet.cn", "most.gov.cn",
    # 财经媒体（专业，有编辑规范）
    "caixin.com", "21jingji.com", "yicai.com", "nbd.com.cn",
    "cbn.com.cn",
]

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

def search_tavily(
    query: str,
    api_key: str,
    max_results: int = 5,
    include_domains: list[str] | None = None,
) -> list[dict]:
    url = "https://api.tavily.com/search"
    payload: dict = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
    }
    if include_domains:
        payload["include_domains"] = include_domains
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

        # 补充扫描
        ("补充扫描", "zh", f"华为 发布 最新 {month_cn}"),
        ("补充扫描", "zh", f"比亚迪 宁德时代 中芯国际 突破 {month_cn}"),
        ("补充扫描", "zh", f"中科院 清华 浙大 研究 突破 2026"),
        ("补充扫描", "zh", f"中国科学家 首次 发现 发明 2026"),
    ]


# ============================================================
# 搜索阶段
# ============================================================

def collect_news(config: dict) -> str:
    api_key = config["search"]["tavily_api_key"]
    queries = get_search_queries()
    all_results = {}
    seen_urls = set()

    log.info(f"开始搜索: {len(queries)} 组关键词...")

    for i, (section, lang, query) in enumerate(queries):
        log.info(f"  [{i+1}/{len(queries)}] [{section}/{lang}] {query[:55]}...")
        domains = AUTHORITATIVE_DOMAINS_EN if lang == "en" else AUTHORITATIVE_DOMAINS_ZH
        results = search_tavily(query, api_key, max_results=5, include_domains=domains)

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
# System Prompt（升级版 v2）
# ============================================================

BRIEFING_SYSTEM_PROMPT = """你是一位顶级全球动态分析师，专注将原始新闻转化为高密度、高质量的每日简报 HTML。

## 【第一步：必须先生成今日综述模块】
在所有新闻板块之前，先输出 `<div class="overview">` 模块：
- 一段 80-120 字的宏观综述：概括今日简报核心趋势与信号，语言有判断力（不是罗列，是分析）
- 3-5 个"今日关键词"标签

示例：
```html
<div class="overview">
  <h3>🧠 今日 AI 综述</h3>
  <p>今日简报呈现三条主线：AI 算力军备竞赛进入新阶段，英伟达与 AMD 均发布旗舰新品；生命科学领域迎来 CRISPR 重大突破，临床转化时间线大幅前移；地缘方面，美对华半导体限制再度升级，供应链重组压力加剧。整体风向：科技端偏乐观，金融端保守观望，地缘风险持续积累。</p>
  <div class="overview-tags">
    <span class="overview-tag">🤖 AI军备竞赛</span>
    <span class="overview-tag">🧬 CRISPR临床化</span>
    <span class="overview-tag">🔴 芯片禁令升级</span>
    <span class="overview-tag">📉 全球增速下调</span>
  </div>
</div>
```

## 【第二步：输出 7 个板块新闻】
筛选最重要的 18-22 条动态，分布在 7 个板块：
1. 🧬 生命科学/医学 (2-3条)
2. 💰 经济/金融 (2-3条)
3. 🤖 人工智能 (2-3条)
4. 💻 科技/硬科技 (3-4条)
5. 🔬 材料科学 (2-3条)
6. 🇨🇳 中国政策与监管 (2-3条)
7. 🌍 地缘政治与国际关系 (2-3条)

### 每条新闻完整结构（5个模块，一个都不能省）：

**① item-header**：标题 + 星级 + 冲击指数 + 风险标签
- 星级：⭐⭐⭐⭐⭐(顶刊/头部机构) / ⭐⭐⭐⭐(权威来源) / ⭐⭐⭐(一般可靠)
- 冲击指数（每条必须有，不能省）：
  - `<span class="impact impact-high">冲击：High</span>`
  - `<span class="impact impact-med">冲击：Med</span>`
  - `<span class="impact impact-low">冲击：Low</span>`
- 风险标签（按需）：`<span class="risk-red">🔴 重大风险</span>` 或 `<span class="risk-yellow">🟡 值得观察</span>`

**② item-summary**：中文摘要 60-100 字，语言精炼有判断力。
关键数字必须用 span 标注（不能遗漏任何百分比、金额、关键量词）：
- 正面/增长数据 → `<span class="num-up">+3.2%</span>`
- 负面/下跌数据 → `<span class="num-down">-0.3%</span>`
- 中性关键参数 → `<span class="num-key">2nm</span>`

**③ bilingual（英语学习模块，每条必须有）**：
```html
<div class="bilingual">
  <p class="en-title">Professional English Title Here</p>
  <p class="en-summary">2-3 sentence professional English summary. Use domain-appropriate vocabulary.</p>
  <div class="vocab-card">
    <div class="vocab-title">📖 Key Vocabulary</div>
    <div class="vocab-list">
      <span class="vocab-item"><b>gene editing</b> 基因编辑</span>
      <span class="vocab-item"><b>off-target effect</b> 脱靶效应</span>
      <span class="vocab-item"><b>clinical trial</b> 临床试验</span>
    </div>
  </div>
</div>
```

**④ compare-box（有对立观点时必须生成）**：
当同一板块内出现两条明显对立观点时（一看涨一看跌、一支持一反对），在该板块趋势评论前插入：
```html
<div class="compare-box">
  <div class="compare-side compare-bull">
    <div class="compare-label">📈 乐观方</div>
    IMF 认为 AI 投资将拉动 2026 年全球增速反弹，科技行业领涨。
  </div>
  <div class="compare-side compare-bear">
    <div class="compare-label">📉 悲观方</div>
    高盛警告通胀黏性超预期，降息空间受限，衰退概率升至 35%。
  </div>
</div>
```

**⑤ deepdive（深度解读，每条必须有）**：
```html
<details class="deepdive">
<summary>📖 深度解读</summary>
<div class="deepdive-content">
  <p class="dl dl-what"><span class="dl-label">📌 是什么：</span>通俗解释，用比喻帮助理解，指出突破点。</p>
  <p class="dl dl-prospect"><span class="dl-label">📈 前景：</span>短期(1-3年)和中长期(3-10年)应用场景，2-3个具体例子，评估落地时间线。</p>
  <p class="dl dl-vision"><span class="dl-label">🔮 畅想：</span>如果完全实现，世界会变成什么样？社会/经济/伦理角度。</p>
</div>
</details>
```

**⑥ item-source**：`<p class="item-source">来源：<a href="..." target="_blank">...</a></p>`

### 每个板块末尾必须有趋势评论：
```html
<div class="trend-comment">📊 板块趋势：50-100字的趋势关联分析...</div>
```

## 【第三步：时间提醒模块（条件触发）】
在所有板块结束后（footer 之前），如果新闻中提到具体未来时间节点（"本周五"、"三个月后生效"、"Q3财报季"、"6月底截止"等），提取并生成：
```html
<div class="timeline">
  <h4>⏰ 近期关键时间节点</h4>
  <div class="timeline-item">
    <span class="timeline-date">2026-06-15</span>
    <span>美联储议息会议，市场预期维持利率不变</span>
  </div>
  <div class="timeline-item">
    <span class="timeline-date">2026-06-30</span>
    <span>美对华半导体出口新规正式生效</span>
  </div>
</div>
```
若无明确未来时间节点，省略此模块。

## 【质量硬性要求】
- 关键数字必须高亮，不能遗漏任何百分比/金额/数量
- 每条新闻的冲击指数 impact 必须有，不能省略
- 有对立观点就必须生成 compare-box
- 直接输出完整 HTML，以 <!DOCTYPE html> 开头，禁止任何 markdown 包裹
- 每个板块中国动态必须覆盖，不能把中国内容只局限于政策板块
- 优先选择顶级期刊/权威来源（Nature/Science/NEJM/新华社等）
"""

BRIEFING_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>每日全球重要动态简报 - {DATE}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, "Microsoft YaHei", "PingFang SC", sans-serif; max-width: 960px; margin: 0 auto; padding: 16px; background: #f0f2f5; }}

    /* ── 页头 ── */
    header {{ background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #1f2937 100%); color: #fff; padding: 22px 24px; border-radius: 14px; margin-bottom: 18px; box-shadow: 0 4px 20px rgba(0,0,0,0.18); }}
    header h1 {{ font-size: 21px; margin-bottom: 5px; letter-spacing: 0.5px; }}
    header p {{ font-size: 12px; opacity: 0.6; line-height: 1.6; }}

    /* ── 今日综述 ── */
    .overview {{ background: linear-gradient(135deg, #1a237e 0%, #283593 60%, #1565c0 100%); color: #fff; border-radius: 12px; padding: 18px 22px; margin-bottom: 20px; box-shadow: 0 3px 14px rgba(21,101,192,0.25); }}
    .overview h3 {{ font-size: 12px; opacity: 0.75; margin-bottom: 10px; letter-spacing: 1.5px; text-transform: uppercase; }}
    .overview p {{ font-size: 14.5px; line-height: 1.9; opacity: 0.95; }}
    .overview-tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 13px; }}
    .overview-tag {{ background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.22); font-size: 12px; padding: 4px 13px; border-radius: 20px; cursor: default; transition: background 0.2s; }}
    .overview-tag:hover {{ background: rgba(255,255,255,0.25); }}

    /* ── 板块标题 ── */
    h2 {{ font-size: 16px; color: #1a1a2e; margin: 26px 0 13px 0; padding-bottom: 7px; border-bottom: 2px solid #dde3ea; display: flex; align-items: center; gap: 8px; }}

    /* ── 新闻卡片 ── */
    .item {{ background: #fff; border-left: 3px solid #4a90d9; border-radius: 0 10px 10px 0; padding: 14px 16px; margin-bottom: 13px; box-shadow: 0 2px 8px rgba(0,0,0,0.07); transition: box-shadow 0.2s; }}
    .item:hover {{ box-shadow: 0 4px 16px rgba(0,0,0,0.11); }}
    .item-header {{ margin-bottom: 9px; }}
    .item-title {{ font-size: 15px; font-weight: 700; color: #1a1a2e; line-height: 1.55; margin-bottom: 7px; word-break: break-word; }}
    .item-meta {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
    .star-rating {{ font-size: 12px; white-space: nowrap; color: #f5a623; background: #fffbf0; padding: 2px 9px; border-radius: 10px; border: 1px solid #f5d88a; }}
    .risk-red {{ background: #ffebee; color: #c62828; font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 700; }}
    .risk-yellow {{ background: #fff8e1; color: #e65100; font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 600; }}

    /* ── 冲击指数 ── */
    .impact {{ font-size: 11px; padding: 2px 9px; border-radius: 10px; font-weight: 700; letter-spacing: 0.3px; }}
    .impact-high {{ background: #fce4ec; color: #880e4f; border: 1px solid #f8bbd0; }}
    .impact-med  {{ background: #fff3e0; color: #bf360c; border: 1px solid #ffe0b2; }}
    .impact-low  {{ background: #f3e5f5; color: #6a1b9a; border: 1px solid #e1bee7; }}

    .item-summary {{ font-size: 14px; color: #444; line-height: 1.78; margin-bottom: 9px; }}

    /* ── 关键数字高亮 ── */
    .num-up   {{ color: #1b5e20; font-weight: 700; background: #e8f5e9; padding: 1px 5px; border-radius: 4px; font-size: 0.94em; }}
    .num-down {{ color: #b71c1c; font-weight: 700; background: #ffebee; padding: 1px 5px; border-radius: 4px; font-size: 0.94em; }}
    .num-key  {{ color: #0d47a1; font-weight: 700; background: #e3f2fd; padding: 1px 5px; border-radius: 4px; font-size: 0.94em; }}

    .item-source {{ font-size: 12px; color: #bbb; margin-top: 9px; }}
    .item-source a {{ color: #4a90d9; text-decoration: none; }}
    .item-source a:hover {{ text-decoration: underline; }}

    /* ── 英语学习模块 ── */
    .bilingual {{ margin: 10px 0; padding: 12px 14px; background: linear-gradient(135deg, #e8f4fd, #ede7f6); border-radius: 9px; border: 1px solid #c5d8f0; }}
    .en-title {{ font-size: 13.5px; color: #1565c0; font-weight: 700; margin-bottom: 5px; line-height: 1.5; }}
    .en-summary {{ font-size: 13px; color: #37474f; line-height: 1.75; margin-bottom: 9px; }}
    .vocab-card {{ background: rgba(255,255,255,0.82); border: 1px solid #dde6f0; border-radius: 7px; padding: 8px 12px; }}
    .vocab-title {{ font-size: 12px; color: #6a1b9a; font-weight: 700; margin-bottom: 5px; }}
    .vocab-list {{ display: flex; flex-wrap: wrap; gap: 4px 12px; }}
    .vocab-item {{ font-size: 12px; color: #424242; line-height: 1.9; }}
    .vocab-item b {{ color: #1a237e; background: #e8eaf6; padding: 1px 5px; border-radius: 3px; font-size: 11px; }}

    /* ── 观点对比框 ── */
    .compare-box {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 12px 0; }}
    .compare-side {{ padding: 11px 13px; border-radius: 9px; font-size: 13px; line-height: 1.72; }}
    .compare-bull {{ background: #e8f5e9; border-left: 3px solid #2e7d32; }}
    .compare-bear {{ background: #ffebee; border-left: 3px solid #c62828; }}
    .compare-label {{ font-weight: 700; font-size: 11px; margin-bottom: 5px; opacity: 0.8; }}

    /* ── 深度解读 ── */
    .deepdive {{ margin-top: 9px; }}
    .deepdive summary {{ cursor: pointer; font-size: 13px; font-weight: 600; color: #1b5e20; padding: 6px 11px; background: linear-gradient(135deg, #e8f5e9, #f1f8e9); border-radius: 7px; border: 1px solid #c8e6c9; user-select: none; transition: background 0.15s; }}
    .deepdive summary:hover {{ background: linear-gradient(135deg, #dcedc8, #e8f5e9); }}
    .deepdive-content {{ background: #fafcfa; border: 1px solid #dde8dd; border-radius: 0 0 7px 7px; padding: 11px 14px; margin-top: 2px; font-size: 13px; line-height: 1.82; color: #444; }}
    .deepdive-content .dl {{ margin: 6px 0; }}
    .deepdive-content .dl-label {{ font-weight: 700; }}
    .dl-what {{ color: #1565c0; }}
    .dl-prospect {{ color: #e65100; }}
    .dl-vision {{ color: #6a1b9a; }}

    /* ── 板块趋势评论 ── */
    .trend-comment {{ background: linear-gradient(135deg, #f3e5f5, #ede7f6); border-left: 3px solid #9575cd; border-radius: 0 8px 8px 0; padding: 11px 15px; margin: 11px 0 20px 0; font-size: 13px; color: #4a148c; font-style: italic; line-height: 1.78; }}

    /* ── 时间提醒 ── */
    .timeline {{ background: #fff; border: 1px solid #ffd54f; border-radius: 10px; padding: 14px 18px; margin: 20px 0; box-shadow: 0 2px 10px rgba(255,193,7,0.13); }}
    .timeline h4 {{ font-size: 13px; color: #e65100; font-weight: 700; margin-bottom: 10px; }}
    .timeline-item {{ font-size: 13px; color: #4e342e; padding: 6px 0; display: flex; gap: 10px; align-items: flex-start; border-bottom: 1px solid #fff8e1; }}
    .timeline-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
    .timeline-date {{ background: #fff9c4; border: 1px solid #ffd54f; color: #5d4037; font-weight: 700; padding: 2px 9px; border-radius: 5px; white-space: nowrap; font-size: 11.5px; flex-shrink: 0; margin-top: 1px; }}

    /* ── 页脚 ── */
    footer {{ text-align: center; color: #bbb; font-size: 11.5px; margin-top: 32px; padding: 14px; line-height: 1.8; }}

    /* ── 移动端 ── */
    @media (max-width: 640px) {{
      body {{ padding: 8px; }}
      header {{ padding: 16px; border-radius: 11px; }}
      header h1 {{ font-size: 18px; }}
      h2 {{ font-size: 15px; margin: 20px 0 11px 0; }}
      .overview {{ padding: 14px 15px; border-radius: 10px; }}
      .overview p {{ font-size: 13.5px; }}
      .item {{ padding: 11px 12px; margin-bottom: 11px; border-radius: 0 8px 8px 0; }}
      .item-title {{ font-size: 14px; }}
      .item-summary {{ font-size: 13px; }}
      .compare-box {{ grid-template-columns: 1fr; gap: 8px; }}
      .bilingual {{ padding: 9px 11px; }}
      .en-title {{ font-size: 13px; }}
      .en-summary {{ font-size: 12px; }}
      .vocab-item {{ font-size: 11px; }}
      .vocab-item b {{ font-size: 10.5px; }}
      .deepdive summary {{ font-size: 12px; padding: 5px 9px; }}
      .deepdive-content {{ font-size: 12px; padding: 9px 11px; }}
      .trend-comment {{ font-size: 12px; padding: 9px 11px; }}
      .timeline {{ padding: 11px 12px; }}
      .timeline-item {{ font-size: 12px; flex-direction: column; gap: 4px; }}
    }}
  </style>
</head>
<body>
<header>
  <h1>📡 每日全球重要动态简报</h1>
  <p>生成时间: {DATE} 08:00 CST &nbsp;·&nbsp; 数据来源: Tavily Search + DeepSeek AI &nbsp;·&nbsp; v2 深度解读版</p>
</header>
"""


# ============================================================
# 生成阶段
# ============================================================

def generate_briefing(news_text: str, config: dict, today_str: str) -> str:
    ds_config = config["deepseek"]
    client = OpenAI(api_key=ds_config["api_key"], base_url=ds_config["base_url"])

    user_message = (
        f"日期: {today_str}\n\n"
        f"以下是今天搜索到的新闻原始内容，请从中筛选最重要的动态，生成完整 HTML 简报:\n\n"
        f"{news_text}\n\n"
        f"请直接输出完整 HTML，以 <!DOCTYPE html> 开头，不要任何 markdown 代码块包裹。\n"
        f"重要提醒：\n"
        f"① 必须先生成 overview 综述模块（含 overview-tags）\n"
        f"② 所有关键数字用 num-up / num-down / num-key 高亮，不能遗漏\n"
        f"③ 每条新闻必须有 impact 冲击指数标签\n"
        f"④ 同一板块有对立观点时必须生成 compare-box\n"
        f"⑤ 新闻中有具体未来时间节点时生成 timeline 模块\n"
        f"⑥ 每条新闻必须有 bilingual 英语学习模块和 deepdive 深度解读\n"
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

    # 修复 AI 输出截断问题
    if "</body>" not in html_content:
        log.warning("AI 输出被截断（缺少 </body>），尝试修复...")
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
        html_content += (
            "\n<footer><p>每日全球重要动态简报 · 由 DeepSeek AI 自动生成 · v2 深度解读版</p></footer>"
            "\n</body>\n</html>"
        )
        log.info("  已补全闭合标签")

    log.info(f"简报生成完成: {len(html_content):,} 字符")
    return html_content


# ============================================================
# 文件保存
# ============================================================

def save_briefing(html_content: str, config: dict, today_str: str) -> str:
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
    msg["Subject"] = Header(f"📡 每日全球重要动态简报 - {today_str}", "utf-8")

    text_part = MIMEText(
        f"今日简报已生成。\n\n日期: {today_str}\n文件: {filepath}\n\n本邮件包含 HTML 版本，请直接查看。",
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
    parser = argparse.ArgumentParser(description="每日全球重要动态简报生成器 v2")
    parser.add_argument("--date", type=str, help="指定日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--no-email", action="store_true", help="不发送邮件")
    parser.add_argument("--search-only", action="store_true", help="仅搜索，不生成简报")
    args = parser.parse_args()

    today = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")

    log.info("===== 每日简报生成器 v2 =====")
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

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
print("DEBUG: Script started")
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
# 网页搜索 (DuckDuckGo - 免费，无需 API Key)
from duckduckgo_search import DDGS

def search_tavily(query: str, api_key: str = "", max_results: int = 5) -> list[dict]:
    """使用 DuckDuckGo 搜索，失败时自动重试，都失败则用 Bing 备用"""
    import time
    results = []

    # 主搜索：DuckDuckGo（重试 2 次）
    for attempt in range(1, 3):
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "content": r.get("body", "")[:300],
                    })
            if results:
                return results
        except Exception as e:
            log.warning(f"DuckDuckGo 搜索尝试 {attempt}/2 失败: {e}")
            time.sleep(2)

    # 备用搜索：Bing API
    bing_key = os.environ.get("BING_API_KEY", "").strip()
    if bing_key:
        try:
            resp = requests.get(
                "https://api.bing.microsoft.com/v7.0/search",
                headers={"Ocp-Apim-Subscription-Key": bing_key},
                params={"q": query, "count": max_results, "mkt": "zh-CN"},
                timeout=15
            )
            data = resp.json()
            for r in data.get("webPages", {}).get("value", []):
                results.append({
                    "title": r.get("name", ""),
                    "url": r.get("url", ""),
                    "content": r.get("snippet", "")[:300],
                })
        except Exception as e:
            log.warning(f"Bing 备用搜索也失败: {e}")

    return results
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
# 天气模块（和风天气免费 API）
# ============================================================
def get_weather() -> str:
    """获取漳州今日天气，返回 HTML 片段，失败返回空字符串"""
    api_key = os.environ.get("WEATHER_API_KEY", "").strip()
    location_id = os.environ.get("WEATHER_LOCATION_ID", "101230501")  # 默认漳州
    if not api_key:
        log.warning("WEATHER_API_KEY 未设置，跳过天气")
        return ""

    try:
        # 获取实时天气
        now_resp = requests.get(
            "https://devapi.qweather.com/v7/weather/now",
            params={"location": location_id, "key": api_key},
            timeout=10
        )
        now_data = now_resp.json()
        if now_data.get("code") != "200":
            log.warning(f"天气API错误: {now_data.get('code')}")
            return ""

        now = now_data["now"]

        # 获取 3 天预报
        forecast_resp = requests.get(
            "https://devapi.qweather.com/v7/weather/3d",
            params={"location": location_id, "key": api_key},
            timeout=10
        )
        forecast_data = forecast_resp.json()
        today_forecast = None
        if forecast_data.get("code") == "200":
            today_forecast = forecast_data["daily"][0] if forecast_data.get("daily") else None

        # 生成 HTML
        weather_html = '<div class="weather-box" style="background:#e3f2fd;border:1px solid #90caf9;border-radius:10px;padding:14px 18px;margin-bottom:20px;">'
        weather_html += f'<p style="font-size:14px;margin:0 0 8px 0;">🌤 <b>今日天气 · 漳州</b></p>'
        weather_html += f'<p style="font-size:13px;margin:4px 0;">当前：{now.get("temp","?")}°C，{now.get("text","?")}，湿度{now.get("humidity","?")}%</p>'
        if today_forecast:
            weather_html += f'<p style="font-size:13px;margin:4px 0;">今日预报：{today_forecast.get("textDay","?")}，{today_forecast.get("tempMin","?")}~{today_forecast.get("tempMax","?")}°C</p>'
        weather_html += '</div>'
        return weather_html

    except Exception as e:
        log.warning(f"天气获取失败: {e}")
        return ""
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
- 禁止在 HTML 中使用 flex、gap、linear-gradient、transition 等 CSS3 属性，布局只用 inline-block，背景只用纯色，font-weight 只用 400/600/700
"""

BRIEFING_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>每日全球重要动态简报 - {DATE}</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, "Microsoft YaHei", "PingFang SC", sans-serif;
      max-width: 660px;
      margin: 0 auto;
      padding: 24px 20px;
      background: #f0f2f5;
      word-break: break-word;
      overflow-x: hidden;
    }
    header {
      background: #1a1a2e;
      color: #fff;
      padding: 26px 24px;
      border-radius: 14px;
      margin-bottom: 24px;
    }
    header h1 { font-size: 21px; margin-bottom: 6px; }
    header p { font-size: 12px; opacity: 0.65; line-height: 1.6; }

    .overview {
      background: #1a237e;
      color: #fff;
      border-radius: 12px;
      padding: 20px 24px;
      margin-bottom: 28px;
    }
    .overview h3 { font-size: 12px; opacity: 0.7; margin-bottom: 12px; }
    .overview p { font-size: 14px; line-height: 1.9; opacity: 0.95; }
    .overview-tags { margin-top: 14px; }
    .overview-tag {
      background: rgba(255,255,255,0.12);
      border: 1px solid rgba(255,255,255,0.18);
      font-size: 12px;
      padding: 4px 14px;
      border-radius: 16px;
      margin-right: 8px;
      margin-bottom: 8px;
      display: inline-block;
    }

    h2 {
      font-size: 17px;
      color: #1a1a2e;
      margin: 30px 0 14px 0;
      padding-bottom: 7px;
      border-bottom: 2px solid #dde3ea;
    }

    .item {
      background: #fff;
      border-left: 3px solid #4a90d9;
      border-radius: 0 10px 10px 0;
      padding: 16px 18px;
      margin-bottom: 18px;
    }
    .item-header { margin-bottom: 8px; }
    .item-title {
      font-size: 15px;
      font-weight: 700;
      color: #1a1a2e;
      line-height: 1.5;
      margin-bottom: 8px;
    }
    .item-meta { margin-bottom: 10px; }
    .star-rating, .risk-red, .risk-yellow, .impact {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 10px;
      margin-right: 6px;
      margin-bottom: 4px;
      display: inline-block;
      font-weight: 600;
    }
    .star-rating { color: #f5a623; background: #fffbf0; border: 1px solid #f5d88a; }
    .risk-red { background: #ffebee; color: #c62828; }
    .risk-yellow { background: #fff8e1; color: #e65100; }
    .impact { background: #f3e5f5; color: #6a1b9a; border: 1px solid #e1bee7; }
    .impact-high { background: #fce4ec; color: #880e4f; }
    .impact-med  { background: #fff3e0; color: #bf360c; }
    .impact-low  { background: #f3e5f5; color: #6a1b9a; }

    .item-summary {
      font-size: 14px;
      color: #444;
      line-height: 1.8;
      margin-bottom: 12px;
    }

    .num-up, .num-down, .num-key {
      font-weight: 700;
      padding: 1px 5px;
      border-radius: 4px;
    }
    .num-up   { color: #1b5e20; background: #e8f5e9; }
    .num-down { color: #b71c1c; background: #ffebee; }
    .num-key  { color: #0d47a1; background: #e3f2fd; }

    .item-source { font-size: 11px; color: #bbb; margin-top: 10px; }
    .item-source a { color: #4a90d9; text-decoration: none; }

    .bilingual {
      margin: 14px 0;
      padding: 12px 14px;
      background: #f0f7ff;
      border-radius: 9px;
      border: 1px solid #bbdefb;
    }
    .en-title { font-size: 13px; color: #1565c0; font-weight: 700; margin-bottom: 6px; }
    .en-summary { font-size: 13px; color: #37474f; line-height: 1.7; margin-bottom: 10px; }
    .vocab-card { background: #fff; border: 1px solid #dde6f0; border-radius: 7px; padding: 8px 12px; }
    .vocab-title { font-size: 12px; color: #6a1b9a; font-weight: 700; margin-bottom: 5px; }
    .vocab-item { font-size: 12px; color: #424242; line-height: 1.9; margin-right: 12px; display: inline-block; }
    .vocab-item b { color: #1a237e; background: #e8eaf6; padding: 1px 4px; border-radius: 3px; }

    .compare-box { margin: 16px 0; }
    .compare-side {
      padding: 12px 14px;
      border-radius: 9px;
      font-size: 13px;
      line-height: 1.7;
      margin-bottom: 10px;
    }
    .compare-bull { background: #e8f5e9; border-left: 3px solid #2e7d32; }
    .compare-bear { background: #ffebee; border-left: 3px solid #c62828; }
    .compare-label { font-weight: 700; font-size: 11px; margin-bottom: 5px; }

    .deepdive { margin-top: 10px; }
    .deepdive summary {
      font-size: 13px;
      font-weight: 600;
      color: #1b5e20;
      padding: 7px 10px;
      background: #e8f5e9;
      border-radius: 7px;
      border: 1px solid #c8e6c9;
      margin-bottom: 2px;
    }
    .deepdive-content {
      background: #fafcfa;
      border: 1px solid #dde8dd;
      border-radius: 7px;
      padding: 12px 14px;
      margin-top: 2px;
      font-size: 13px;
      line-height: 1.8;
      color: #444;
    }
    .deepdive-content .dl { margin: 6px 0; }
    .deepdive-content .dl-label { font-weight: 700; }
    .dl-what { color: #1565c0; }
    .dl-prospect { color: #e65100; }
    .dl-vision { color: #6a1b9a; }

    .trend-comment {
      background: #f3e5f5;
      border-left: 3px solid #9575cd;
      border-radius: 0 8px 8px 0;
      padding: 12px 16px;
      margin: 14px 0 24px 0;
      font-size: 13px;
      color: #4a148c;
      font-style: italic;
      line-height: 1.8;
    }

    .timeline {
      background: #fff;
      border: 1px solid #ffd54f;
      border-radius: 10px;
      padding: 16px 20px;
      margin: 22px 0;
    }
    .timeline h4 { font-size: 13px; color: #e65100; font-weight: 700; margin-bottom: 12px; }
    .timeline-item { font-size: 13px; color: #4e342e; padding: 7px 0; border-bottom: 1px solid #fff8e1; }
    .timeline-item:last-child { border-bottom: none; }
    .timeline-date {
      background: #fff9c4;
      border: 1px solid #ffd54f;
      color: #5d4037;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 5px;
      font-size: 11px;
      margin-right: 10px;
      display: inline-block;
    }

    footer {
      text-align: center;
      color: #aaa;
      font-size: 11px;
      margin-top: 36px;
      padding: 14px;
      line-height: 1.8;
    }

    @media (max-width: 600px) {
      body { padding: 16px 12px; }
      header { padding: 20px 16px; }
      .item { padding: 14px 14px; }
      .overview { padding: 16px 16px; }
    }
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
# 企业微信机器人推送
# ============================================================
def send_wechat_bot(today_str: str, page_url: str):
    """通过企业微信机器人 Webhook 推送简报链接"""
    webhook_url = os.environ.get("WECOM_BOT_WEBHOOK", "").strip()
    if not webhook_url:
        log.info("WECOM_BOT_WEBHOOK 未设置，跳过微信推送")
        return

    try:
        import json as json_lib
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"""📡 **每日全球重要动态简报已生成**
> 日期：{today_str}
> 状态：已生成并保存

[在浏览器中查看完整简报]({page_url})

<font color=\"#999\">由 DeepSeek AI 自动生成 · 每日早 8 点</font>"""
            }
        }
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info("微信推送成功!")
        else:
            log.warning(f"微信推送失败: {resp.status_code} {resp.text}")
    except Exception as e:
        log.warning(f"微信推送异常: {e}")

# ============================================================
# 邮件发送
# ============================================================
# ============================================================
# 邮件专用简化 HTML（兼容邮件客户端）
# ============================================================

def send_email(html_content: str, filepath: str, config: dict, today_str: str):
    page_url = f"https://QQQ347.github.io/daily-briefing/每日简报-{today_str}.html"
    email_config = config.get("email", {})

    if not email_config.get("enabled", False):
        log.info("邮件未配置或已禁用，跳过发送")
        return

    receiver = email_config["receiver"]
    resend_key = os.environ.get("RESEND_API_KEY", "").strip()

    # ── 方式1: Resend HTTPS API（推荐）──
    if resend_key:
        try:
            log.info(f"发送邮件 (Resend API) → {receiver}...")

            resp = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": "每日简报 <onboarding@resend.dev>",
                    "to": [receiver],
                    "subject": f"📡 每日全球重要动态简报 - {today_str}",
                    "html": f"""
                    <div style="max-width:560px;margin:0 auto;font-family:'Microsoft YaHei',sans-serif;padding:24px;background:#f5f5f5;border-radius:10px;">
                      <h2 style="color:#1a1a2e;margin:0 0 12px 0;">📡 每日全球重要动态简报</h2>
                      <p style="color:#666;font-size:14px;">{today_str} 的简报已生成。</p>
                      <p style="margin:28px 0;">
                        <a href="{page_url}" style="background:#1a237e;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-size:16px;display:inline-block;">🔗 在浏览器中查看完整简报</a>
                      </p>
                      <p style="color:#999;font-size:12px;margin-top:20px;">或复制链接：<br><a href="{page_url}" style="color:#4a90d9;">{page_url}</a></p>
                      <p style="color:#bbb;font-size:11px;margin-top:24px;">由 DeepSeek AI 自动生成 · 每日早 8 点</p>
                    </div>
                    """,
                },
                timeout=30,
            )
            if resp.status_code in (200, 201):
                log.info(f"邮件发送成功 (Resend)! ID: {resp.json().get('id', '?')}")
                return
            else:
                log.error(f"Resend 发送失败: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            log.error(f"Resend 请求异常: {e}")

    # ── 方式2: SMTP 备用（GitHub Actions 上端口被封，仅本地可用）──
    sender = email_config.get("sender", "")
    password = email_config.get("password", "")
    if not sender or not password:
        log.error("SMTP 未配置 sender/password，跳过备用发送")
        return

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = Header(f"📡 每日全球重要动态简报 - {today_str}", "utf-8")
    msg.attach(MIMEText(
        f"今日简报已生成。\n\n日期: {today_str}\n文件: {filepath}\n\n本邮件包含 HTML 版本，请直接查看。",
        "plain", "utf-8",
    ))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    for host, port, use_ssl in [("smtp.qq.com", 465, True), ("smtp.qq.com", 587, False)]:
        try:
            log.info(f"SMTP 尝试 {host}:{port} ({'SSL' if use_ssl else 'STARTTLS'})...")
            if use_ssl:
                server = smtplib.SMTP_SSL(host, port, timeout=30)
            else:
                server = smtplib.SMTP(host, port, timeout=30)
                server.ehlo()
                server.starttls()
            server.login(sender, password)
            server.sendmail(sender, [receiver], msg.as_string())
            server.quit()
            log.info(f"邮件发送成功 (SMTP {port})!")
            return
        except Exception as e:
            log.error(f"SMTP {port} 失败: {e}")

    log.error("所有邮件发送方式均失败，简报已保存至本地文件")
    log.info(f"简报文件: {filepath}")


# ============================================================
# 主流程
# ============================================================

def main():
    print("Entered main()", flush=True)
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

    # 天气
    weather_html = get_weather()

    # 阶段 1: 搜索
    log.info("【阶段1】搜索新闻...")
    news_text = collect_news(config)

    if args.search_only:
        print(news_text)
        return

    # 如果搜索失败，发兜底通知
    if not news_text.strip():
        log.error("未搜索到任何新闻")
        if not args.no_email:
            send_email("<p>今日简报生成失败：未搜索到新闻。请检查搜索服务。</p>",
                       "", config, today_str)
            send_wechat_bot(today_str, "https://QQQ347.github.io/daily-briefing/")
        sys.exit(1)

    # 把天气插入到 news_text 前面
    if weather_html:
        news_text = weather_html + "\n" + news_text

    # 阶段 2: 生成
    log.info("【阶段2】AI 生成简报...")
    html_content = generate_briefing(news_text, config, today_str)

    # 阶段 3: 保存
    log.info("【阶段3】保存文件...")
    filepath = save_briefing(html_content, config, today_str)

    # 阶段 4: 邮件 + 微信推送
    if not args.no_email:
        log.info("【阶段4】发送通知...")
        page_url = f"https://QQQ347.github.io/daily-briefing/每日简报-{today_str}.html"
        send_email(html_content, filepath, config, today_str)
        send_wechat_bot(today_str, page_url)
    else:
        log.info("跳过邮件和微信推送 (--no-email)")

    log.info("===== 完成! =====")
    log.info(f"文件: {filepath}")
if __name__ == "__main__":
    try:
        print("Calling main() now...", flush=True)
        main()
        print("main() finished successfully.", flush=True)
    except SystemExit as e:
        print(f"SystemExit with code: {e.code}", flush=True)
        sys.exit(e.code)
    except Exception as e:
        import traceback
        print("FATAL ERROR:", str(e), flush=True)
        traceback.print_exc()
        sys.exit(1)

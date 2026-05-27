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

## 输出格式
直接输出完整 HTML，不要任何解释性文字，必须以 <!DOCTYPE html> 开头。
"""

BRIEFING_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>每日全球重要动态简报 - {DATE}</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, "Microsoft YaHei", "PingFang SC", sans-serif; max-width: 940px; margin: 0 auto; padding: 20px; background: #f5f7fa; }
    header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); color: #fff; padding: 28px 32px; border-radius: 12px 12px 0 0; margin-bottom: 24px; }
    header h1 { margin: 0 0 6px 0; font-size: 22px; }
    header p { margin: 0; opacity: 0.75; font-size: 13px; }
    h2 { font-size: 17px; color: #1a1a2e; margin: 28px 0 14px 0; padding-bottom: 8px; border-bottom: 2px solid #e8ecf0; }
    .item { background: #fff; border-left: 3px solid #4a90d9; border-radius: 0 8px 8px 0; padding: 14px 18px; margin-bottom: 14px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
    .item-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; gap: 10px; flex-wrap: wrap; }
    .item-title-wrap { flex: 1; min-width: 0; }
    .item-title { font-size: 15px; font-weight: 600; color: #222; line-height: 1.5; }
    .item-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .star-rating { font-size: 13px; white-space: nowrap; color: #f5a623; background: #fffbf0; padding: 2px 8px; border-radius: 10px; border: 1px solid #f5d88a; }
    .risk-red { background: #ffebee; color: #c62828; font-size: 12px; padding: 2px 8px; border-radius: 10px; font-weight: 700; }
    .risk-yellow { background: #fff8e1; color: #e65100; font-size: 12px; padding: 2px 8px; border-radius: 10px; font-weight: 600; }
    .item-summary { font-size: 13.5px; color: #555; line-height: 1.7; margin-bottom: 8px; }
    .item-source { font-size: 12px; color: #888; }
    .item-source a { color: #4a90d9; text-decoration: none; }
    .deepdive { margin-top: 10px; }
    .deepdive summary { cursor: pointer; font-size: 12.5px; font-weight: 600; color: #1b5e20; padding: 6px 10px; background: linear-gradient(135deg, #e8f5e9, #f1f8e9); border-radius: 6px; border: 1px solid #c8e6c9; user-select: none; }
    .deepdive-content { background: #f9fbf9; border: 1px solid #e0e8e0; border-radius: 0 0 6px 6px; padding: 12px 14px; margin-top: 2px; font-size: 12.5px; line-height: 1.75; color: #444; }
    .deepdive-content .dl { margin: 6px 0; }
    .deepdive-content .dl-label { font-weight: 700; }
    .dl-what { color: #1565c0; }
    .dl-prospect { color: #e65100; }
    .dl-vision { color: #6a1b9a; }
    .trend-comment { background: linear-gradient(135deg, #f3e5f5, #e8eaf6); border-left: 3px solid #9575cd; border-radius: 0 6px 6px 0; padding: 10px 16px; margin: 10px 0 20px 0; font-size: 13px; color: #4a148c; font-style: italic; line-height: 1.7; }
    .summary-stats { background: #fff; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); display: flex; gap: 24px; flex-wrap: wrap; }
    .stat-item { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #555; }
    .stat-item .stat-num { font-size: 22px; font-weight: 700; color: #1a1a2e; }
    footer { text-align: center; color: #aaa; font-size: 12px; margin-top: 40px; padding: 16px; }
  </style>
</head>
<body>
<header>
  <h1>每日全球重要动态简报</h1>
  <p>生成时间: {DATE} 08:00 CST | 数据来源: Tavily Search + DeepSeek AI 综合整理</p>
</header>
"""


def generate_briefing(news_text: str, config: dict, today_str: str) -> str:
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

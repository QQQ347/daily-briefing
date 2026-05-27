#!/usr/bin/env python3
"""
每日全球重要动态简报 - 云服务器版
====================================
在云服务器上独立运行, 不依赖 WorkBuddy。
每天自动搜索全球重要动态, 生成 HTML 简报, 可选邮件推送。

使用方法:
  python daily_briefing.py              # 生成今天的简报
  python daily_briefing.py --date 2026-05-25  # 生成指定日期简报
  python daily_briefing.py --no-email   # 不发送邮件

依赖安装:
  pip install requests openai

配置:
  复制 config.example.json 为 config.json, 填入 API 密钥
"""

import os
import sys
import json
import argparse
import datetime
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from pathlib import Path
from typing import Optional

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
# 配置加载
# ============================================================

def load_config() -> dict:
    """加载 config.json"""
    script_dir = Path(__file__).parent
    config_path = script_dir / "config.json"
    
    if not config_path.exists():
        log.error(f"配置文件不存在: {config_path}")
        log.error("请复制 config.example.json 为 config.json 并填入 API 密钥")
        sys.exit(1)
    
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


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
        log.warning(f"Tavily 搜索失败 [{query}]: {e}")
        return []


# ============================================================
# 搜索关键词矩阵
# ============================================================

def get_search_queries() -> list[tuple[str, str, str]]:
    """返回 (板块名, 语言, 关键词) 列表"""
    today = datetime.date.today()
    month_cn = today.strftime("%Y年%m月")
    
    queries = [
        # 生命科学
        ("生命科学", "en", "gene therapy clinical trial breakthrough 2026"),
        ("生命科学", "en", "Nature medicine NEJM latest breakthrough May 2026"),
        ("生命科学", "zh", "基因治疗 临床突破 2026"),
        ("生命科学", "zh", "新药获批 中国 中科院 最新发现"),
        
        # 经济金融
        ("经济金融", "en", "S&P 500 Fed interest rate IMF global economy May 2026"),
        ("经济金融", "en", "stock market AI investment merger acquisition 2026"),
        ("经济金融", "zh", "央行 货币政策 A股 经济数据 2026"),
        ("经济金融", "zh", "人民币汇率 进出口 中国经济 最新"),
        
        # AI
        ("人工智能", "en", "OpenAI Google AI model LLM breakthrough May 2026"),
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
        ("地缘政治", "en", "Iran Strait of Hormuz semiconductor export control 2026"),
        ("地缘政治", "zh", "中美关系 出口管制 半导体 2026"),
        ("地缘政治", "zh", "一带一路 供应链 地缘 最新"),
        
        # === 中国科技补充扫描 ===
        ("补充扫描", "zh", f"华为 发布 最新 {month_cn}"),
        ("补充扫描", "zh", f"比亚迪 宁德时代 中芯国际 突破 {month_cn}"),
        ("补充扫描", "zh", f"中科院 清华 浙大 研究 突破 2026"),
        ("补充扫描", "zh", f"中国科学家 首次 发现 发明 2026"),
    ]
    return queries


# ============================================================
# 搜索阶段：收集所有新闻
# ============================================================

def collect_news(config: dict) -> str:
    """执行所有搜索, 返回汇总的新闻文本"""
    search_config = config["search"]
    api_key = search_config.get("tavily_api_key", "")
    
    if not api_key:
        log.error("未配置搜索 API 密钥")
        sys.exit(1)
    
    queries = get_search_queries()
    all_results = {}  # url -> result, 自动去重
    seen_urls = set()
    
    log.info(f"开始搜索: {len(queries)} 组关键词...")
    
    for i, (section, lang, query) in enumerate(queries):
        log.info(f"  [{i+1}/{len(queries)}] [{section}] {query[:60]}...")
        results = search_tavily(query, api_key, max_results=5)
        
        for r in results:
            url = r["url"]
            if url not in seen_urls:
                seen_urls.add(url)
                all_results[url] = {
                    "section": section,
                    "title": r["title"],
                    "url": url,
                    "content": r["content"][:300],  # 截取前300字
                }
    
    log.info(f"搜索完成: 去重后共 {len(all_results)} 条结果")
    
    # 拼接成文本
    news_text = ""
    for i, (url, item) in enumerate(all_results.items()):
        news_text += f"\n--- 新闻 #{i+1} [{item['section']}] ---\n"
        news_text += f"标题: {item['title']}\n"
        news_text += f"链接: {item['url']}\n"
        news_text += f"摘要: {item['content']}\n"
    
    return news_text


# ============================================================
# 生成阶段：AI 写简报
# ============================================================

def get_briefing_prompt(today_str: str) -> str:
    """返回简报生成 prompt"""
    return f"""请基于下方提供的今日新闻搜索结果, 生成一份完整的每日全球重要动态简报 HTML。

日期: {today_str}

## 核心原则
中国是当今全球最重要的科技与商业中心之一。每个板块都必须覆盖中国动态, 不能把"中国内容"局限于"政策监管"一个板块。

## 收集要求
从下方搜索结果中筛选最重要的 18-22 条动态, 分布在以下 7 个板块:
1. 🧬 生命科学/医学 (2-3条)
2. 💰 经济/金融 (2-3条)
3. 🤖 人工智能 (2-3条)
4. 💻 科技/硬科技 (3-4条)
5. 🔬 材料科学 (2-3条)
6. 🇨🇳 中国政策与监管 (2-3条)
7. 🌍 地缘政治与国际关系 (2-3条)

## 质量要求
- 优先选择顶级期刊/权威来源 (Nature/Science/Cell/NEJM/新华社/人民日报等)
- 每条包含: 信息标题、2-3句摘要、来源链接
- 标记风险: 🔴重大风险 / 🟡值得观察
- 星级: ⭐⭐⭐⭐⭐(顶刊) / ⭐⭐⭐⭐(权威) / ⭐⭐⭐(一般)
- 每个板块后加50-100字趋势关联简评

## 信息来源标注
每条必须标注原始链接和来源机构名称。

## 输出格式
必须输出完整 HTML 文件, 使用以下精确的 CSS 样式和结构:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>每日全球重要动态简报 - {today_str}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, "Microsoft YaHei", "PingFang SC", sans-serif; max-width: 940px; margin: 0 auto; padding: 20px; background: #f5f7fa; }}
    header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); color: #fff; padding: 28px 32px; border-radius: 12px 12px 0 0; margin-bottom: 24px; }}
    header h1 {{ margin: 0 0 6px 0; font-size: 22px; }}
    header p {{ margin: 0; opacity: 0.75; font-size: 13px; }}
    h2 {{ font-size: 17px; color: #1a1a2e; margin: 28px 0 14px 0; padding-bottom: 8px; border-bottom: 2px solid #e8ecf0; }}
    .item {{ background: #fff; border-left: 3px solid #4a90d9; border-radius: 0 8px 8px 0; padding: 14px 18px; margin-bottom: 14px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); transition: box-shadow 0.2s; }}
    .item:hover {{ box-shadow: 0 2px 12px rgba(74,144,217,0.18); }}
    .item-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; gap: 10px; flex-wrap: wrap; }}
    .item-title-wrap {{ flex: 1; min-width: 0; }}
    .item-title {{ font-size: 15px; font-weight: 600; color: #222; line-height: 1.5; }}
    .item-meta {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .star-rating {{ font-size: 13px; white-space: nowrap; color: #f5a623; background: #fffbf0; padding: 2px 8px; border-radius: 10px; border: 1px solid #f5d88a; }}
    .risk-red {{ background: #ffebee; color: #c62828; font-size: 12px; padding: 2px 8px; border-radius: 10px; font-weight: 700; }}
    .risk-yellow {{ background: #fff8e1; color: #e65100; font-size: 12px; padding: 2px 8px; border-radius: 10px; font-weight: 600; }}
    .item-summary {{ font-size: 13.5px; color: #555; line-height: 1.7; margin-bottom: 8px; }}
    .item-source {{ font-size: 12px; color: #888; }}
    .item-source a {{ color: #4a90d9; text-decoration: none; }}
    .item-source a:hover {{ text-decoration: underline; }}
    .deepdive {{ margin-top: 10px; }}
    .deepdive summary {{ cursor: pointer; font-size: 12.5px; font-weight: 600; color: #1b5e20; padding: 6px 10px; background: linear-gradient(135deg, #e8f5e9, #f1f8e9); border-radius: 6px; border: 1px solid #c8e6c9; user-select: none; transition: background 0.2s; }}
    .deepdive summary:hover {{ background: linear-gradient(135deg, #c8e6c9, #dcedc8); }}
    .deepdive-content {{ background: #f9fbf9; border: 1px solid #e0e8e0; border-radius: 0 0 6px 6px; padding: 12px 14px; margin-top: 2px; font-size: 12.5px; line-height: 1.75; color: #444; }}
    .deepdive-content .dl {{ margin: 6px 0; padding-left: 2px; }}
    .deepdive-content .dl-label {{ font-weight: 700; display: inline; }}
    .dl-what {{ color: #1565c0; }}
    .dl-prospect {{ color: #e65100; }}
    .dl-vision {{ color: #6a1b9a; }}
    .trend-comment {{ background: linear-gradient(135deg, #f3e5f5, #e8eaf6); border-left: 3px solid #9575cd; border-radius: 0 6px 6px 0; padding: 10px 16px; margin: 10px 0 20px 0; font-size: 13px; color: #4a148c; font-style: italic; line-height: 1.7; }}
    .trend-comment strong {{ font-style: normal; color: #311b92; }}
    .summary-stats {{ background: #fff; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); display: flex; gap: 24px; flex-wrap: wrap; }}
    .stat-item {{ display: flex; align-items: center; gap: 8px; font-size: 13px; color: #555; }}
    .stat-item .stat-num {{ font-size: 22px; font-weight: 700; color: #1a1a2e; }}
    footer {{ text-align: center; color: #aaa; font-size: 12px; margin-top: 40px; padding: 16px; }}
    @media (max-width: 640px) {{ body {{ padding: 12px; }} header {{ padding: 20px 18px; }} header h1 {{ font-size: 18px; }} .item {{ padding: 12px 14px; }} .summary-stats {{ gap: 16px; }} }}
  </style>
</head>
<body>
<header>
  <h1>每日全球重要动态简报</h1>
  <p>生成时间: {today_str} 08:00 CST | 数据来源: AI 综合整理自公开信息</p>
</header>

<div class="summary-stats">
  <div class="stat-item"><span class="stat-num">N</span><span>条动态</span></div>
  <div class="stat-item"><span class="stat-num" style="color:#c62828">X</span><span>重大风险</span></div>
  <div class="stat-item"><span class="stat-num" style="color:#e65100">Y</span><span>值得观察</span></div>
</div>

<!-- 各板块内容 -->

<footer>本简报信息来源均标注原始链接, 仅供参考, 内容判断由 AI 自动完成, 不代表任何机构立场。</footer>
</body>
</html>
```

## 重要：深入解读
对于每条重要新闻, 在来源链接下方添加可折叠的深入解读模块:
```html
<details class="deepdive">
  <summary>🧠 深入解读: 这条新闻到底意味着什么？</summary>
  <div class="deepdive-content">
    <div class="dl"><span class="dl-label dl-what">📌 是什么: </span>[用通俗语言解释该技术/事件]</div>
    <div class="dl"><span class="dl-label dl-prospect">📈 前景: </span>[产业影响和应用前景]</div>
    <div class="dl"><span class="dl-label dl-vision">🔮 畅想: </span>[未来5-10年的想象]</div>
  </div>
</details>
```

## 最终要求
- 直接输出完整 HTML, 不要任何解释性文字
- 确保所有 HTML 标签正确闭合
- 统计栏中的 N/X/Y 替换为实际数字
- 回复必须以 <!DOCTYPE html> 开头
"""


def generate_briefing(news_text: str, config: dict, today_str: str) -> str:
    """调用 DeepSeek API 生成简报 HTML"""
    ds_config = config["deepseek"]
    
    client = OpenAI(
        api_key=ds_config["api_key"],
        base_url=ds_config.get("base_url", "https://api.deepseek.com"),
    )
    
    prompt = get_briefing_prompt(today_str)
    
    log.info("调用 DeepSeek API 生成简报...")
    
    response = client.chat.completions.create(
        model=ds_config.get("model", "deepseek-v4-flash"),
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"以下是今天搜索到的新闻:\n\n{news_text}\n\n请生成完整 HTML 简报。"},
        ],
        temperature=0.7,
        max_tokens=16000,
    )
    
    html_content = response.choices[0].message.content
    
    # 提取 HTML (去除可能的 markdown 包裹)
    if "```html" in html_content:
        html_content = html_content.split("```html")[1].split("```")[0].strip()
    elif "```" in html_content:
        html_content = html_content.split("```")[1].split("```")[0].strip()
    
    # 确保以 <!DOCTYPE html> 开头
    if not html_content.strip().startswith("<!DOCTYPE"):
        # 尝试找到 doctype
        for line in html_content.split("\n"):
            if "<!DOCTYPE" in line or "<html" in line:
                idx = html_content.index(line)
                html_content = html_content[idx:]
                break
    
    log.info(f"简报生成完成: {len(html_content)} 字符")
    return html_content


# ============================================================
# 文件保存
# ============================================================

def save_briefing(html_content: str, config: dict, today_str: str) -> str:
    """保存 HTML 文件"""
    output_dir = Path(config["output"]["dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"每日简报-{today_str}.html"
    filepath = output_dir / filename
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    log.info(f"简报已保存: {filepath}")
    
    # 清理旧文件
    keep_days = config["output"].get("keep_days", 30)
    cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
    for f in output_dir.glob("每日简报-*.html"):
        if f.stat().st_mtime < cutoff.timestamp():
            f.unlink()
            log.info(f"清理旧文件: {f.name}")
    
    return str(filepath)


# ============================================================
# 邮件发送
# ============================================================

def send_email(html_content: str, filepath: str, config: dict, today_str: str):
    """通过 QQ 邮箱 SMTP 发送简报"""
    email_config = config.get("email", {})
    if not email_config.get("enabled", True):
        log.info("邮件推送已禁用")
        return
    
    sender = email_config["sender"]
    password = email_config["password"]
    receiver = email_config["receiver"]
    smtp_host = email_config.get("smtp_host", "smtp.qq.com")
    smtp_port = email_config.get("smtp_port", 587)
    
    # 构建邮件
    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = Header(f"每日全球重要动态简报 - {today_str}", "utf-8")
    
    # 纯文本版
    text_part = MIMEText(f"今日简报已生成, 请查看附件或在浏览器中打开 HTML 文件。", "plain", "utf-8")
    msg.attach(text_part)
    
    # HTML 版
    html_part = MIMEText(html_content, "html", "utf-8")
    msg.attach(html_part)
    
    try:
        log.info(f"发送邮件到 {receiver}...")
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [receiver], msg.as_string())
        server.quit()
        log.info("邮件发送成功!")
    except Exception as e:
        log.error(f"邮件发送失败: {e}")
        log.info(f"简报文件已保存在: {filepath}")


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="每日全球重要动态简报生成器")
    parser.add_argument("--date", type=str, help="指定日期 (YYYY-MM-DD), 默认今天")
    parser.add_argument("--no-email", action="store_true", help="不发送邮件")
    parser.add_argument("--search-only", action="store_true", help="仅搜索, 不生成简报")
    args = parser.parse_args()
    
    # 日期
    if args.date:
        today = datetime.date.fromisoformat(args.date)
    else:
        today = datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")
    
    log.info(f"===== 每日简报生成器 =====")
    log.info(f"日期: {today_str}")
    
    # 加载配置
    config = load_config()
    
    # 搜索阶段
    log.info("【阶段1】搜索新闻...")
    news_text = collect_news(config)
    
    if args.search_only:
        log.info("仅搜索模式, 结果已输出")
        print(news_text)
        return
    
    if not news_text.strip():
        log.error("未搜索到任何新闻, 请检查搜索 API 配置")
        sys.exit(1)
    
    # 生成阶段
    log.info("【阶段2】AI 生成简报...")
    html_content = generate_briefing(news_text, config, today_str)
    
    # 保存
    log.info("【阶段3】保存文件...")
    filepath = save_briefing(html_content, config, today_str)
    
    # 邮件
    if not args.no_email:
        log.info("【阶段4】发送邮件...")
        send_email(html_content, filepath, config, today_str)
    
    log.info(f"===== 完成! =====")
    log.info(f"文件: {filepath}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
每日全球重要动态简报 - 腾讯云 SCF 版
======================================
在腾讯云函数（SCF）上运行，每天定时触发。
不依赖本机开机，不依赖 GitHub，校网也能用。

环境变量（在 SCF 控制台配置）:
  DEEPSEEK_API_KEY    DeepSeek API 密钥
  TAVILY_API_KEY      Tavily Search API 密钥
  EMAIL_SENDER        发件邮箱 (xxx@qq.com)
  EMAIL_PASSWORD      QQ邮箱SMTP授权码
  EMAIL_RECEIVER      收件邮箱

部署方式:
  1. 在腾讯云 SCF 控制台创建函数，选择 Python 3.9 运行时
  2. 上传本文件（或在线编辑粘贴代码）
  3. 配置环境变量
  4. 添加定时触发器（每天 08:00）
"""

import os
import sys
import subprocess
import json
import datetime
import smtplib
import logging
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

# ============================================================
# 自动安装依赖（SCF 环境可能没有 requests/openai）
# ============================================================
def _ensure_deps():
    """首次运行时自动安装缺失的依赖到 /tmp 层"""
    try:
        import requests  # noqa
        from openai import OpenAI  # noqa
        return
    except ImportError:
        pass
    # 安装到 /tmp/deps（SCF 可写目录）
    deps_dir = "/tmp/deps"
    os.makedirs(deps_dir, exist_ok=True)
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "requests>=2.31.0", "openai>=1.0.0",
        "--target", deps_dir, "-q",
    ])
    sys.path.insert(0, deps_dir)

_ensure_deps()

import requests
from openai import OpenAI

# ============================================================
# 日志
# ============================================================
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ============================================================
# 配置
# ============================================================

def get_config() -> dict:
    """从环境变量读取配置"""
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()
    email_sender = os.environ.get("EMAIL_SENDER", "").strip()
    email_password = os.environ.get("EMAIL_PASSWORD", "").strip()
    email_receiver = os.environ.get("EMAIL_RECEIVER", email_sender).strip()

    if not deepseek_key:
        raise ValueError("环境变量 DEEPSEEK_API_KEY 未设置")
    if not tavily_key:
        raise ValueError("环境变量 TAVILY_API_KEY 未设置")

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
    }


# ============================================================
# 搜索
# ============================================================

def search_tavily(query: str, api_key: str, max_results: int = 5) -> list:
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
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in data.get("results", [])
        ]
    except Exception as e:
        logger.warning(f"Tavily 搜索失败 [{query[:40]}]: {e}")
        return []


def get_search_queries() -> list:
    today = datetime.date.today()
    month_cn = today.strftime("%Y年%m月")
    return [
        ("生命科学", "en", "gene therapy clinical trial breakthrough 2026"),
        ("生命科学", "en", "Nature medicine NEJM latest breakthrough May 2026"),
        ("生命科学", "zh", "基因治疗 临床突破 2026"),
        ("生命科学", "zh", "新药获批 中国 中科院 最新发现"),
        ("经济金融", "en", "S&P 500 Fed interest rate IMF global economy 2026"),
        ("经济金融", "en", "stock market AI investment merger acquisition 2026"),
        ("经济金融", "zh", "央行 货币政策 A股 经济数据 2026"),
        ("经济金融", "zh", "人民币汇率 进出口 中国经济 最新"),
        ("人工智能", "en", "OpenAI Google AI model LLM breakthrough 2026"),
        ("人工智能", "en", "DeepSeek Anthropic Claude AI latest news 2026"),
        ("人工智能", "zh", "大模型 人工智能 突破 中国 2026"),
        ("人工智能", "zh", "DeepSeek 月之暗面 智谱 通义千问 最新"),
        ("硬科技", "en", "semiconductor chip TSMC Samsung Intel 2nm latest 2026"),
        ("硬科技", "en", "quantum computing breakthrough latest 2026"),
        ("硬科技", "zh", "芯片 半导体 华为 中芯国际 突破 2026"),
        ("硬科技", "zh", "量子计算 光刻 先进制程 中国"),
        ("材料科学", "en", "Nature Materials battery perovskite solar cell 2026"),
        ("材料科学", "en", "new material discovery breakthrough energy 2026"),
        ("材料科学", "zh", "材料科学 钙钛矿 固态电池 突破 2026"),
        ("材料科学", "zh", "新材料 中科院 清华 能源材料"),
        ("中国政策", "en", "China policy regulation technology economy 2026"),
        ("中国政策", "zh", "国务院 发改委 工信部 政策 新规 2026"),
        ("中国政策", "zh", "数据局 数字经济 算力 监管"),
        ("地缘政治", "en", "geopolitics US China trade war sanctions 2026"),
        ("地缘政治", "en", "semiconductor export control supply chain 2026"),
        ("地缘政治", "zh", "中美关系 出口管制 半导体 2026"),
        ("地缘政治", "zh", "一带一路 供应链 地缘 最新"),
        ("补充扫描", "zh", f"华为 发布 最新 {month_cn}"),
        ("补充扫描", "zh", f"比亚迪 宁德时代 中芯国际 突破 {month_cn}"),
        ("补充扫描", "zh", f"中科院 清华 浙大 研究 突破 2026"),
        ("补充扫描", "zh", f"中国科学家 首次 发现 发明 2026"),
    ]


def collect_news(config: dict) -> str:
    api_key = config["search"]["tavily_api_key"]
    queries = get_search_queries()
    all_results = {}
    seen_urls = set()

    logger.info(f"开始搜索: {len(queries)} 组关键词...")

    for i, (section, lang, query) in enumerate(queries):
        logger.info(f"  [{i+1}/{len(queries)}] [{section}/{lang}] {query[:55]}...")
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

    logger.info(f"搜索完成: 去重后共 {len(all_results)} 条结果")

    news_text = ""
    for i, (url, item) in enumerate(all_results.items()):
        news_text += f"\n--- 新闻 #{i+1} [{item['section']}] ---\n"
        news_text += f"标题: {item['title']}\n"
        news_text += f"链接: {item['url']}\n"
        news_text += f"摘要: {item['content']}\n"

    return news_text


# ============================================================
# 生成
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

## 输出格式
直接输出完整 HTML，不要任何解释性文字，必须以 <!DOCTYPE html> 开头。
"""


def generate_briefing(news_text: str, config: dict, today_str: str) -> str:
    ds_config = config["deepseek"]
    client = OpenAI(api_key=ds_config["api_key"], base_url=ds_config["base_url"])

    user_message = (
        f"日期: {today_str}\n\n"
        f"以下是今天搜索到的新闻原始内容，请从中筛选最重要的动态，生成完整 HTML 简报:\n\n"
        f"{news_text}\n\n"
        f"请直接输出完整 HTML，以 <!DOCTYPE html> 开头，不要任何 markdown 代码块包裹。"
    )

    logger.info(f"调用 DeepSeek API (model: {ds_config['model']})...")

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

    # 去除 markdown 包裹
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

    logger.info(f"简报生成完成: {len(html_content):,} 字符")
    return html_content


# ============================================================
# 邮件
# ============================================================

def send_email(html_content: str, config: dict, today_str: str):
    email_config = config.get("email", {})
    if not email_config.get("enabled", False):
        logger.info("邮件未配置或已禁用，跳过发送")
        return

    sender = email_config["sender"]
    password = email_config["password"]
    receiver = email_config["receiver"]

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = Header(f"每日全球重要动态简报 - {today_str}", "utf-8")

    text_part = MIMEText(f"今日简报已生成。日期: {today_str}", "plain", "utf-8")
    html_part = MIMEText(html_content, "html", "utf-8")
    msg.attach(text_part)
    msg.attach(html_part)

    try:
        logger.info(f"发送邮件 → {receiver}...")
        server = smtplib.SMTP("smtp.qq.com", 587, timeout=30)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [receiver], msg.as_string())
        server.quit()
        logger.info("邮件发送成功!")
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")


# ============================================================
# SCF 入口函数
# ============================================================

def main_handler(event: dict, context: dict) -> dict:
    """
    腾讯云 SCF 入口函数
    定时触发器会自动调用此函数
    """
    try:
        today = datetime.date.today()
        today_str = today.strftime("%Y-%m-%d")

        logger.info(f"===== 每日简报 SCF 版 =====")
        logger.info(f"日期: {today_str}")

        config = get_config()

        # 搜索
        logger.info("【阶段1】搜索新闻...")
        news_text = collect_news(config)

        if not news_text.strip():
            raise RuntimeError("未搜索到任何新闻")

        # 生成
        logger.info("【阶段2】AI 生成简报...")
        html_content = generate_briefing(news_text, config, today_str)

        # 邮件
        logger.info("【阶段3】发送邮件...")
        send_email(html_content, config, today_str)

        logger.info("===== 完成! =====")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "简报生成成功",
                "date": today_str,
                "html_size": len(html_content),
            }, ensure_ascii=False)
        }

    except Exception as e:
        logger.error(f"执行失败: {e}")
        logger.error(traceback.format_exc())
        return {
            "statusCode": 500,
            "body": json.dumps({
                "message": f"执行失败: {str(e)}",
            }, ensure_ascii=False)
        }


# ============================================================
# 本地测试入口
# ============================================================

if __name__ == "__main__":
    # 本地运行时不走 SCF 入口，直接执行
    result = main_handler({}, None)
    print(json.dumps(result, ensure_ascii=False, indent=2))

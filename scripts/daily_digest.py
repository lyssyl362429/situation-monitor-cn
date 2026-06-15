#!/usr/bin/env python3
"""
Daily news digest script for Situation Monitor CN.
Fetches Chinese and AI news, generates a Chinese-language summary,
and sends it via Gmail SMTP.

Environment variables (set in GitHub Secrets):
  SMTP_USERNAME    — Gmail address (e.g., yourname@gmail.com)
  SMTP_PASSWORD    — Gmail App Password (16-char without spaces)
  EMAIL_TO         — recipient address (defaults to SMTP_USERNAME)
"""

import os
import sys
import json
import html
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from smtplib import SMTP_SSL, SMTPAuthenticationError
from typing import Optional

import time

import feedparser
import requests

# ── Config ──────────────────────────────────────────────────────────────────

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_TO = os.environ.get("EMAIL_TO", SMTP_USERNAME)

BEIJING_TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(BEIJING_TZ).strftime("%Y年%m月%d日")

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# ── News Sources ────────────────────────────────────────────────────────────

CHINESE_RSS_FEEDS = [
    ("36氪", "https://36kr.com/feed"),
    ("IT之家", "https://www.ithome.com/rss/"),
    ("BBC中文网", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"),
]

AI_RSS_FEEDS = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
    ("OpenAI Blog", "https://openai.com/blog/rss.xml"),
    ("Google Blog", "https://blog.google/rss/"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
]

GDELT_QUERIES = {
    "chinese": {
        "query": "(中国 OR 科技 OR 经济 OR 人工智能 OR 政治 OR 市场)",
        "lang": "chinese",
        "label": "GDELT中国",
    },
    "ai": {
        "query": '("artificial intelligence" OR "machine learning" OR "large language model" OR "generative AI" OR "AI" OR "deep learning")',
        "lang": "english",
        "label": "GDELT_AI",
    },
    "tech": {
        "query": '(technology OR "silicon valley" OR startup OR software OR tech)',
        "lang": "english",
        "label": "GDELT科技",
    },
}

MAX_ARTICLES_PER_SOURCE = 8

# ── Fetching ────────────────────────────────────────────────────────────────


def fetch_rss(name: str, url: str) -> list[dict]:
    """Fetch and parse an RSS feed, return list of article dicts."""
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        articles = []
        for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
            articles.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "source": name,
                "date": entry.get("published", ""),
            })
        return articles
    except Exception as e:
        print(f"  [warn] RSS fetch failed for {name}: {e}", file=sys.stderr)
        return []


def fetch_gdelt(query: str, lang: str, label: str, retries: int = 3) -> list[dict]:
    """Fetch news from GDELT API with retry on rate limit."""
    params = {
        "query": f"{query} sourcelang:{lang}",
        "timespan": "1d",
        "mode": "artlist",
        "maxrecords": 15,
        "format": "json",
        "sort": "date",
    }
    for attempt in range(retries):
        try:
            resp = requests.get(GDELT_URL, params=params, timeout=30)
            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"  [warn] GDELT rate limited for {label}, waiting {wait}s (attempt {attempt + 1}/{retries})...", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            articles = []
            for article in data.get("articles", []):
                articles.append({
                    "title": article.get("title", ""),
                    "link": article.get("url", ""),
                    "source": article.get("domain", label),
                    "date": article.get("seendate", ""),
                })
            return articles
        except Exception as e:
            print(f"  [warn] GDELT fetch failed for {label}: {e}", file=sys.stderr)
            return []
    print(f"  [warn] GDELT fetch failed for {label}: exhausted retries (429)", file=sys.stderr)
    return []


def fetch_all_news() -> dict:
    """Fetch all news sources and return categorized results."""
    print("📡 Fetching Chinese RSS feeds...")
    chinese_rss = []
    for name, url in CHINESE_RSS_FEEDS:
        print(f"  -> {name}")
        chinese_rss.extend(fetch_rss(name, url))

    print("📡 Fetching AI RSS feeds...")
    ai_rss = []
    for name, url in AI_RSS_FEEDS:
        print(f"  -> {name}")
        ai_rss.extend(fetch_rss(name, url))

    print("📡 Fetching GDELT (Chinese)...")
    gdelt_chinese = fetch_gdelt(**GDELT_QUERIES["chinese"])
    time.sleep(5)

    print("📡 Fetching GDELT (AI)...")
    gdelt_ai = fetch_gdelt(**GDELT_QUERIES["ai"])
    time.sleep(5)

    print("📡 Fetching GDELT (Tech)...")
    gdelt_tech = fetch_gdelt(**GDELT_QUERIES["tech"])

    # Deduplicate by title (simple fuzzy)
    seen_titles: set[str] = set()

    def dedup(articles: list[dict]) -> list[dict]:
        result = []
        for a in articles:
            key = a["title"].strip().lower()[:60]
            if key and key not in seen_titles:
                seen_titles.add(key)
                result.append(a)
        return result

    return {
        "中文资讯 (RSS)": dedup(chinese_rss),
        "AI 资讯 (RSS)": dedup(ai_rss),
        "中文要闻 (GDELT)": dedup(gdelt_chinese),
        "AI 前沿 (GDELT)": dedup(gdelt_ai),
        "科技动态 (GDELT)": dedup(gdelt_tech),
    }


# ── Summary Generation ──────────────────────────────────────────────────────


def generate_html(categorized: dict) -> str:
    """Generate a beautifully formatted HTML email in Chinese."""
    all_articles = []
    for cat_articles in categorized.values():
        all_articles.extend(cat_articles)

    total = len(all_articles)

    sections_html = ""
    for category, articles in categorized.items():
        if not articles:
            continue

        items_html = ""
        for i, article in enumerate(articles[:12]):
            title = html.escape(article.get("title", "(无标题)")).replace("&amp;", "&")
            link = article.get("link", "")
            source = html.escape(article.get("source", ""))
            items_html += f"""
            <tr>
              <td style="padding:8px 12px; border-bottom:1px solid #2d2d2d; font-size:14px; line-height:1.5;">
                <a href="{link}" style="color:#58a6ff; text-decoration:none;">{title}</a>
                <span style="color:#8b949e; font-size:12px; margin-left:8px;">— {source}</span>
              </td>
            </tr>"""

        sections_html += f"""
        <div style="margin-bottom:28px;">
          <h2 style="color:#f0f6fc; font-size:16px; margin:0 0 12px 0; padding-bottom:6px;
                     border-bottom:2px solid #30363d; display:flex; align-items:center; gap:8px;">
            <span style="background:#238636; color:#fff; font-size:11px; border-radius:10px;
                         padding:1px 8px;">{len(articles)}</span>
            {category}
          </h2>
          <table style="width:100%; border-collapse:collapse;">
            {items_html}
          </table>
        </div>"""

    # Top picks: up to 5 most interesting headlines across all categories
    top_picks = []
    for cat_articles in categorized.values():
        for a in cat_articles[:3]:
            top_picks.append(a)
    top_picks = top_picks[:5]

    top_html = ""
    if top_picks:
        items = "".join(
            f'<li style="margin-bottom:6px;"><a href="{html.escape(a["link"])}" '
            f'style="color:#f0883e; text-decoration:none;">{html.escape(a["title"]).replace("&amp;","&")}</a>'
            f' <span style="color:#8b949e; font-size:12px;">— {html.escape(a["source"])}</span></li>'
            for a in top_picks
        )
        top_html = f"""
        <div style="margin-bottom:28px; background:#1c2128; border:1px solid #30363d;
                    border-radius:8px; padding:16px;">
          <h2 style="color:#f0883e; font-size:15px; margin:0 0 10px 0;">📌 今日重点关注</h2>
          <ol style="margin:0; padding-left:20px; color:#c9d1d9;">{items}</ol>
        </div>"""

    date_cn = datetime.now(BEIJING_TZ).strftime("%Y年%m月%d日 %A")
    weekday_map = {
        "Monday": "星期一", "Tuesday": "星期二", "Wednesday": "星期三",
        "Thursday": "星期四", "Friday": "星期五", "Saturday": "星期六", "Sunday": "星期日",
    }
    date_cn = f"{datetime.now(BEIJING_TZ).strftime('%Y年%m月%d日')} {weekday_map.get(datetime.now(BEIJING_TZ).strftime('%A'), '')}"

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0; padding:0; background:#0d1117; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
<table style="max-width:680px; margin:0 auto; padding:24px 16px;" cellpadding="0" cellspacing="0">
  <tr>
    <td>
      <!-- Header -->
      <div style="text-align:center; padding:24px 0 20px; border-bottom:1px solid #21262d; margin-bottom:24px;">
        <h1 style="color:#f0f6fc; font-size:22px; margin:0 0 4px;">📰 每日情报摘要</h1>
        <p style="color:#8b949e; font-size:13px; margin:0;">{date_cn} · 共收录 {total} 条</p>
      </div>

      {top_html}

      {sections_html}

      <!-- Footer -->
      <div style="margin-top:32px; padding-top:16px; border-top:1px solid #21262d;
                  text-align:center; color:#8b949e; font-size:12px;">
        <p style="margin:0 0 4px;">由 <strong>Situation Monitor CN</strong> 自动生成</p>
        <p style="margin:0;">数据来源: GDELT Project · RSS Feeds</p>
        <p style="margin:6px 0 0; font-size:11px; color:#484f58;">
          若有问题，请回复此邮件或联系维护者
        </p>
      </div>
    </td>
  </tr>
</table>
</body>
</html>"""

    return html_body


def generate_text(categorized: dict) -> str:
    """Generate plain-text fallback."""
    lines = [
        "=" * 50,
        "每日情报摘要",
        f"{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')}",
        "=" * 50,
        "",
    ]
    for category, articles in categorized.items():
        if not articles:
            continue
        lines.append(f"【{category}】({len(articles)} 条)")
        lines.append("-" * 40)
        for a in articles[:10]:
            lines.append(f"  • {a['title']} — {a.get('source', '')}")
            if a.get("link"):
                lines.append(f"    {a['link']}")
        lines.append("")

    lines.append("=" * 50)
    lines.append("由 Situation Monitor CN 自动生成")
    return "\n".join(lines)


# ── Email ───────────────────────────────────────────────────────────────────


def send_email(html_content: str, text_content: str):
    """Send email via Gmail SMTP."""
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print("❌ SMTP credentials not configured. Set SMTP_USERNAME and SMTP_PASSWORD.", file=sys.stderr)
        sys.exit(1)

    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_USERNAME
    msg["To"] = EMAIL_TO
    weekday_map = {
        "Monday": "星期一", "Tuesday": "星期二", "Wednesday": "星期三",
        "Thursday": "星期四", "Friday": "星期五", "Saturday": "星期六", "Sunday": "星期日",
    }
    date_cn = f"{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')} {weekday_map.get(datetime.now(BEIJING_TZ).strftime('%A'), '')}"
    msg["Subject"] = f"📰 每日情报摘要 | {date_cn}"

    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        print(f"📧 Connecting to {SMTP_HOST}:{SMTP_PORT} ...")
        with SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"✅ Email sent to {EMAIL_TO}")
    except SMTPAuthenticationError:
        print(
            "❌ SMTP authentication failed. Make sure you're using a Gmail App Password:\n"
            "   1. Go to https://myaccount.google.com/apppasswords\n"
            "   2. Generate a 16-char app password (no spaces)\n"
            "   3. Set it as SMTP_PASSWORD in GitHub Secrets",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed to send email: {e}", file=sys.stderr)
        sys.exit(1)


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    print("=" * 50)
    print(f"📰 Situation Monitor CN — Daily Digest")
    print(f"   {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')} (CST)")
    print("=" * 50)

    news = fetch_all_news()

    # Print summary
    total = sum(len(v) for v in news.values())
    print(f"\n📊 Total articles collected: {total}")
    for cat, articles in news.items():
        print(f"   {cat}: {len(articles)}")

    print("\n📝 Generating digest...")
    html_body = generate_html(news)
    text_body = generate_text(news)

    # Save preview as artifact
    preview_path = os.path.join(os.path.dirname(__file__) or ".", "digest_preview.html")
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(html_body)
    print(f"   Preview saved to {preview_path}")

    print(f"\n📧 Sending email to {EMAIL_TO}...")
    send_email(html_body, text_body)

    print("\n✅ Done!")


if __name__ == "__main__":
    main()

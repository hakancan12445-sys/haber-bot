#!/usr/bin/env python3
"""Seçtiğin konulardaki son haberleri Telegram'a gönderir.

Hiçbir şey kaydetmez: her çalışmada sadece son WINDOW_HOURS saatte yayınlanmış
ve başlığında anahtar kelime geçen haberleri yollar.
"""
import html
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

TR = ZoneInfo("Europe/Istanbul")

# Bot 6 saatte bir çalışır. 7 saat, zamanlayıcı gecikse bile haber kaçmasın diye.
WINDOW_HOURS = 7

FEEDS = {
    "Anadolu Ajansı": "https://www.aa.com.tr/tr/rss/default?cat=guncel",
    "CNN Türk": "https://www.cnnturk.com/feed/rss/turkiye/news",
    "NTV": "https://www.ntv.com.tr/gundem.rss",
    "Habertürk": "https://www.haberturk.com/rss/kategori/gundem.xml",
    "Medyascope": "https://medyascope.tv/feed/",
}

# Sadece BAŞLIĞINDA bu ifadelerden biri geçen haberler gönderilir.
# Listeyi boş bırakırsan ([]) tüm haberler gelir.
KEYWORDS = [
    "yargı paketi",
    "infaz",
    "genel af",
    "af düzenlemesi",
    "denetimli serbestlik",
    "koşullu salıverilme",
]

MAX_PER_FEED = 8
CHUNK_LIMIT = 3800
UA = "Mozilla/5.0 (compatible; HaberBot/2.0)"


def norm(text):
    """Büyük/küçük harf ve Türkçe karakter farklarını yok sayar."""
    table = str.maketrans("İIıŞşĞğÜüÖöÇç", "iiissgguuoocc")
    return text.translate(table).lower()


def matches(title):
    if not KEYWORDS:
        return True
    t = norm(title)
    for kw in KEYWORDS:
        k = norm(kw)
        pos = t.find(k)
        while pos != -1:
            if pos == 0 or not t[pos - 1].isalnum():
                return True
            pos = t.find(k, pos + 1)
    return False


def parse_date(text):
    text = (text or "").strip()
    if not text:
        return None
    dt = None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        pass
    if dt is None:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TR)
    return dt


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read()


def parse_feed(xml_bytes):
    """RSS ve Atom için (başlık, bağlantı, tarih) listesi döndürür."""
    root = ET.fromstring(xml_bytes)
    items = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        title = link = ""
        dates = {}
        for ch in node:
            name = ch.tag.rsplit("}", 1)[-1]
            if name == "title":
                title = " ".join(html.unescape(ch.text or "").split())
            elif name == "link":
                link = (ch.text or "").strip() or ch.get("href", "").strip()
            elif name in ("pubDate", "published", "updated", "date"):
                dates[name] = ch.text
        raw = (
            dates.get("pubDate") or dates.get("published")
            or dates.get("date") or dates.get("updated")
        )
        if title and link:
            items.append((title, link, parse_date(raw)))
    return items


def collect():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    sections, used, failed = [], set(), 0
    for source, url in FEEDS.items():
        try:
            items = parse_feed(fetch(url))
        except Exception as exc:
            failed += 1
            print(f"[uyarı] {source} alınamadı: {exc}", file=sys.stderr)
            continue
        fresh = [
            (dt, t, l)
            for t, l, dt in items
            if dt is not None and dt >= cutoff and matches(t)
        ]
        fresh.sort(reverse=True)
        picked = []
        for dt, t, l in fresh:
            if l in used:
                continue
            used.add(l)
            picked.append((t, l))
            if len(picked) >= MAX_PER_FEED:
                break
        print(f"{source}: {len(items)} haber tarandı, {len(picked)} uygun.")
        if picked:
            sections.append((source, picked))
    return sections, failed


def build_messages(sections):
    now = datetime.now(TR).strftime("%d.%m.%Y %H:%M")
    blocks = []
    for source, items in sections:
        lines = [f"<b>{html.escape(source, quote=False)}</b>"]
        for title, link in items:
            lines.append(
                f'• <a href="{html.escape(link, quote=True)}">{html.escape(title, quote=False)}</a>'
            )
        blocks.append("\n".join(lines))

    header = f"📰 <b>Yargı paketi / infaz haberleri</b> — {now}"
    messages, current = [], header
    for block in blocks:
        if len(current) + len(block) + 2 > CHUNK_LIMIT:
            messages.append(current)
            current = block
        else:
            current += "\n\n" + block
    messages.append(current)
    return messages


def send_telegram(token, chat_id, text):
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        urllib.request.urlopen(url, data=data, timeout=25).read()
    except urllib.error.HTTPError as exc:
        print("Telegram hatası:", exc.read().decode("utf-8", "replace"), file=sys.stderr)
        raise


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    can_send = bool(token and chat_id)

    if os.environ.get("TEST") == "true":
        if not can_send:
            sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanımlı değil.")
        send_telegram(token, chat_id, "✅ Haber botu çalışıyor, Telegram bağlantısı tamam.")
        print("Test mesajı gönderildi.")
        return

    sections, failed = collect()
    if failed == len(FEEDS):
        sys.exit("Hiçbir haber kaynağına ulaşılamadı.")
    if not sections:
        print("Konuya uyan yeni haber yok, mesaj gönderilmedi.")
        return

    messages = build_messages(sections)
    if not can_send:
        print("[deneme modu]\n" + "\n\n---\n\n".join(messages))
        return
    for msg in messages:
        send_telegram(token, chat_id, msg)
    print("Gönderildi.")


if __name__ == "__main__":
    main()

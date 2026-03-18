"""News agent — RSS feed parsing, LLM filtering & article generation.

Drip-feed: generates up to _MAX_PER_RUN articles per hourly invocation,
but sends them to Telegram spread out over the hour with random intervals.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime, timezone
from typing import Any

import feedparser
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.data.providers.groq import generate_text

from ..config import NEWS_FILTER_PROMPT, NEWS_WRITER_PROMPT
from ..queries import (
    fetch_unprocessed_sources,
    mark_sources_processed,
    publish_news_article,
    save_news_article,
    save_news_source,
    save_report,
    fetch_recent_news,
)
from .monitor import send_to_owner

log = get_logger("ai_office.news")

# Category emoji mapping for Telegram
_CAT_EMOJI = {
    "injury": "🔴",
    "transfer": "🔄",
    "preview": "📋",
    "review": "📝",
    "standings": "📊",
}

# Max items per feed to avoid overwhelming the LLM
_MAX_PER_FEED = 20
# Max articles to publish per single run (drip-feed throughout the day)
_MAX_PER_RUN = 3

# Map LLM league_tag → API Football league_id
_LEAGUE_TAG_TO_ID: dict[str, int | None] = {
    "epl": 39,
    "laliga": 140,
    "seriea": 135,
    "bundesliga": 78,
    "ligue1": 61,
    "primeiraliga": 94,
    "ucl": None,       # European cups have no single league_id
    "uel": None,
    "uecl": None,
    "international": None,
    "none": None,
}


# ---------------------------------------------------------------------------
# RSS fetching
# ---------------------------------------------------------------------------

def _parse_feed_name(url: str) -> str:
    """Extract a human-readable name from the feed URL."""
    if "bbc" in url:
        return "BBC Sport"
    if "guardian" in url:
        return "The Guardian"
    if "espn" in url:
        return "ESPN FC"
    if "skysports" in url:
        return "Sky Sports"
    if "marca" in url:
        return "Marca"
    if "goal.com" in url:
        return "GOAL"
    if "kicker.de" in url:
        return "Kicker"
    if "lequipe.fr" in url:
        return "L'Équipe"
    if "record.pt" in url:
        return "Record"
    if "maisfutebol" in url:
        return "Mais Futebol"
    if "football-espana" in url:
        return "Football España"
    if "football-italia" in url:
        return "Football Italia"
    if "transfermarkt" in url:
        return "Transfermarkt"
    if "mundodeportivo" in url:
        return "Mundo Deportivo"
    if "gazzetta.it" in url:
        return "Gazzetta dello Sport"
    if "corrieredellosport" in url:
        return "Corriere dello Sport"
    if "rmcsport" in url:
        return "RMC Sport"
    if "90min" in url:
        return "90min"
    if "fourfourtwo" in url:
        return "FourFourTwo"
    # Fallback: domain name
    match = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1) if match else url[:50]


async def _fetch_all_feeds(feed_urls: list[str]) -> list[dict[str, Any]]:
    """Fetch and parse all configured RSS feeds.

    Returns a flat list of feed items with normalised fields.
    feedparser is sync-only but very fast, so we just run it directly.
    """
    items: list[dict[str, Any]] = []

    for url in feed_urls:
        try:
            feed = feedparser.parse(url)
            source_name = _parse_feed_name(url)
            count = 0
            for entry in feed.entries:
                if count >= _MAX_PER_FEED:
                    break
                link = entry.get("link", "").strip()
                title = entry.get("title", "").strip()
                if not link or not title:
                    continue
                description = entry.get("summary", "") or entry.get("description", "")
                # Clean HTML tags from description
                description = re.sub(r"<[^>]+>", "", description).strip()
                items.append({
                    "url": link,
                    "title": title,
                    "description": description[:500],
                    "source_name": source_name,
                })
                count += 1
            log.info("rss_parsed url=%s items=%d", url[:60], count)
        except Exception:
            log.exception("rss_parse_error url=%s", url[:80])

    return items


# ---------------------------------------------------------------------------
# LLM filtering & generation
# ---------------------------------------------------------------------------

async def _filter_articles(
    session: AsyncSession,
    sources: list[dict[str, Any]],
) -> list[int]:
    """Use Groq to filter relevant articles from batch.

    Also performs semantic deduplication: fetches recent article titles
    and asks the LLM to skip sources that cover already-published topics.
    Returns list of indices.
    """
    if not sources:
        return []

    # Fetch recent articles for dedup context (last 24h worth)
    recent = await fetch_recent_news(session, limit=20)
    recent_titles = [r.get("title", "") for r in recent if r.get("title")]

    # Build batch prompt with dedup context
    lines = ["Список новых RSS-новостей:\n"]
    for i, s in enumerate(sources):
        title = s.get("title", "")
        desc = s.get("description", "")[:200]
        lines.append(f"{i}. [{s.get('source_name', '?')}] {title}")
        if desc:
            lines.append(f"   {desc}")

    # Add dedup context
    if recent_titles:
        lines.append("\n\nУже опубликованные статьи (НЕ выбирай дубликаты):\n")
        for t in recent_titles:
            lines.append(f"  • {t}")

    prompt = "\n".join(lines)

    # Enhanced system prompt with dedup instructions
    dedup_system = (
        NEWS_FILTER_PROMPT.rstrip()
        + "\n\n"
        "ДЕДУПЛИКАЦИЯ: Если новость по смыслу дублирует уже опубликованную "
        "статью (та же травма, тот же трансфер, тот же матч, те же игроки) — "
        "НЕ включай её, даже если источник другой. "
        "Выбирай только УНИКАЛЬНЫЕ по смыслу новости."
    )

    try:
        raw = await generate_text(
            session,
            prompt,
            system_prompt=dedup_system,
            temperature=0.1,
            max_tokens=256,
            use_cache=False,
        )
        # Parse JSON array of indices
        cleaned = raw.strip()
        # Extract JSON array if wrapped in text
        arr_match = re.search(r"\[[\s\S]*?\]", cleaned)
        if arr_match:
            cleaned = arr_match.group(0)
        indices = json.loads(cleaned)
        if not isinstance(indices, list):
            log.warning("news_filter_not_list raw=%s", raw[:200])
            return []
        # Validate indices
        valid = [i for i in indices if isinstance(i, int) and 0 <= i < len(sources)]

        # --- Second pass: cross-source dedup within batch ---
        # If multiple sources from different outlets cover the same story,
        # keep only the first one encountered.
        if len(valid) > 1:
            valid = _dedup_within_batch(sources, valid)

        log.info(
            "news_filter_result total=%d relevant=%d recent_ctx=%d",
            len(sources), len(valid), len(recent_titles),
        )
        return valid
    except Exception:
        log.exception("news_filter_failed — passing all through")
        # On failure, pass all through (safer than dropping all)
        return list(range(len(sources)))


def _dedup_within_batch(
    sources: list[dict[str, Any]],
    indices: list[int],
) -> list[int]:
    """Remove intra-batch duplicates using simple keyword overlap.

    Two sources are considered duplicates if they share ≥3 significant
    words (length > 4) in their titles. Keeps the first occurrence.
    """
    kept: list[int] = []
    seen_keywords: list[set[str]] = []

    for idx in indices:
        title = (sources[idx].get("title", "") or "").lower()
        # Extract significant words (>4 chars, skip common English stop-words)
        words = {
            w for w in re.findall(r"[a-zA-Zà-ÿÀ-Ÿа-яА-ЯёЁäöüßÄÖÜñÑçÇãõÃÕ]+", title)
            if len(w) > 4 and w not in {
                # English
                "about", "after", "their", "there", "would", "could",
                "should", "which", "where", "these", "those", "being",
                "football", "league", "match", "premier", "season",
                "transfer", "breaking", "report", "update",
                # German
                "gegen", "spiel", "saison", "trainer", "spieltag",
                "verein", "mannschaft", "bundesliga",
                # Spanish
                "contra", "partido", "temporada", "equipo", "jugador",
                "primera", "división",
                # French
                "contre", "saison", "joueur", "ligue", "équipe",
                # Portuguese
                "contra", "futebol", "jogador", "época", "equipa",
            }
        }
        if not words:
            kept.append(idx)
            seen_keywords.append(words)
            continue

        # Check overlap with already-kept titles
        is_dup = False
        for prev_words in seen_keywords:
            overlap = words & prev_words
            if len(overlap) >= 3:
                src_name = sources[idx].get("source_name", "?")
                log.info(
                    "news_dedup_within_batch idx=%d src=%s overlap=%s",
                    idx, src_name, overlap,
                )
                is_dup = True
                break
        if not is_dup:
            kept.append(idx)
            seen_keywords.append(words)

    return kept


async def _generate_article(
    session: AsyncSession,
    source: dict[str, Any],
) -> dict[str, Any] | None:
    """Generate a news article from a single RSS source via Groq."""
    title = source.get("title", "")
    desc = source.get("raw_text", "") or source.get("description", "")
    prompt = (
        f"Источник: {source.get('source_name', 'Unknown')}\n"
        f"Заголовок: {title}\n"
        f"Описание: {desc[:500]}\n"
    )

    try:
        raw = await generate_text(
            session,
            prompt,
            system_prompt=NEWS_WRITER_PROMPT,
            temperature=0.3,
            max_tokens=512,
            use_cache=False,
        )
        cleaned = raw.strip()
        # Extract JSON object
        obj_match = re.search(r"\{[\s\S]*\}", cleaned)
        if obj_match:
            cleaned = obj_match.group(0)
        article = json.loads(cleaned)

        # Validate required fields
        if not isinstance(article, dict):
            log.warning("news_gen_not_dict raw=%s", raw[:200])
            return None

        required = {"title", "summary", "body", "category"}
        if not required.issubset(article.keys()):
            missing = required - set(article.keys())
            log.warning("news_gen_missing_fields fields=%s", missing)
            return None

        # Validate category
        valid_categories = {"preview", "review", "injury", "transfer", "standings"}
        if article["category"] not in valid_categories:
            article["category"] = "standings"  # safe default

        return article
    except Exception:
        log.exception("news_gen_failed title=%s", title[:60])
        return None


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def _format_single_article(article: dict[str, Any]) -> str:
    """Format a single article as an individual Telegram message."""
    emoji = _CAT_EMOJI.get(article.get("category", ""), "📌")
    cat_ru = {
        "injury": "Травма",
        "transfer": "Трансфер",
        "preview": "Превью",
        "review": "Обзор",
        "standings": "Таблица",
    }.get(article.get("category", ""), "Новость")

    lines = [
        f"{emoji} <b>[{cat_ru}]</b> {article['title']}",
        "",
    ]
    if article.get("body"):
        # Use body (full text), trim to reasonable Telegram length
        body = article["body"]
        if len(body) > 1500:
            body = body[:1500] + "..."
        lines.append(body)
    elif article.get("summary"):
        lines.append(article["summary"])

    return "\n".join(lines)


def _format_news_digest(articles: list[dict[str, Any]]) -> str:
    """Format multiple articles as a compact digest for save_report."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📰 <b>Новости</b> | {now_str}", ""]

    for a in articles[:10]:
        emoji = _CAT_EMOJI.get(a.get("category", ""), "📌")
        lines.append(f"{emoji} {a['title']}")
        if a.get("summary"):
            lines.append(f"  {a['summary']}")
        lines.append("")

    lines.append(f"Опубликовано: {len(articles)}")
    return "\n".join(lines)


def format_news_list(articles: list[dict[str, Any]]) -> str:
    """Format recent news articles for /news command response."""
    if not articles:
        return "📰 Нет свежих новостей"

    lines = ["📰 <b>Последние новости</b>", ""]
    for a in articles:
        emoji = _CAT_EMOJI.get(a.get("category", ""), "📌")
        cat_ru = {
            "injury": "Травма",
            "transfer": "Трансфер",
            "preview": "Превью",
            "review": "Обзор",
            "standings": "Таблица",
        }.get(a.get("category", ""), "Новость")

        pub = a.get("published_at")
        time_str = pub.strftime("%d.%m %H:%M") if pub else ""
        lines.append(f"{emoji} <b>[{cat_ru}]</b> {a['title']}")
        if a.get("summary"):
            lines.append(f"  {a['summary']}")
        if time_str:
            lines.append(f"  <i>{time_str}</i>")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main agent entry point
# ---------------------------------------------------------------------------

async def run(session: AsyncSession) -> dict[str, Any]:
    """Execute the news agent: RSS → filter → generate → save → Telegram.

    Returns summary dict with status and article counts.
    """
    log.info("news_run_start")

    # 1. Parse feed URLs from config
    feed_urls = [
        u.strip()
        for u in settings.ai_office_news_feeds.split(",")
        if u.strip()
    ]
    if not feed_urls:
        log.warning("news_no_feeds — AI_OFFICE_NEWS_FEEDS is empty")
        return {"status": "skipped", "reason": "no feed URLs configured"}

    # 2. Fetch RSS feeds
    raw_items = await _fetch_all_feeds(feed_urls)
    log.info("news_fetched total=%d from %d feeds", len(raw_items), len(feed_urls))

    if not raw_items:
        return {"status": "skipped", "reason": "no RSS items fetched"}

    # 3. Save to news_sources (dedup by URL)
    new_count = 0
    for item in raw_items:
        source_id = await save_news_source(
            session,
            url=item["url"],
            source_name=item["source_name"],
            title=item["title"],
            raw_text=item.get("description"),
        )
        if source_id:
            item["source_id"] = source_id
            new_count += 1

    log.info("news_new_sources count=%d", new_count)

    # 4. Fetch unprocessed sources (including leftovers from previous runs)
    unprocessed = await fetch_unprocessed_sources(session, limit=30)
    if not unprocessed:
        if new_count == 0:
            return {"status": "skipped", "reason": "no new items (all duplicates)"}
        return {"status": "skipped", "reason": "no unprocessed sources"}

    log.info("news_unprocessed count=%d", len(unprocessed))

    # 5. Filter via Groq LLM (batch — one call for up to 30 items)
    if settings.groq_enabled and settings.groq_api_key:
        relevant_indices = await _filter_articles(session, unprocessed)
    else:
        # No LLM — pass all through
        relevant_indices = list(range(len(unprocessed)))

    # Mark non-relevant as processed (no article)
    non_relevant_ids = [
        s["id"] for i, s in enumerate(unprocessed)
        if i not in set(relevant_indices)
    ]
    if non_relevant_ids:
        await mark_sources_processed(session, non_relevant_ids)
        log.info("news_marked_irrelevant count=%d", len(non_relevant_ids))

    if not relevant_indices:
        return {
            "status": "done",
            "fetched": new_count,
            "published": 0,
            "reason": "nothing relevant after filter",
        }

    # 6. Generate articles via Groq — limited to _MAX_PER_RUN per invocation
    #    Remaining articles stay unprocessed for the next hourly run (drip-feed)
    #    Phase A: generate & save all articles to DB (fast)
    #    Phase B: send to Telegram with random intervals spread over ~50 min
    generated: list[dict[str, Any]] = []
    for idx in relevant_indices:
        if len(generated) >= _MAX_PER_RUN:
            log.info("news_drip_limit reached=%d, rest deferred to next run", _MAX_PER_RUN)
            break
        if idx >= len(unprocessed):
            continue
        source = unprocessed[idx]
        try:
            article_data = await _generate_article(session, source)
            if article_data:
                # Resolve league_id from LLM league_tag
                league_tag = (article_data.get("league_tag") or "none").lower().strip()
                league_id = _LEAGUE_TAG_TO_ID.get(league_tag)
                article_id = await save_news_article(
                    session,
                    title=article_data["title"],
                    body=article_data["body"],
                    summary=article_data["summary"],
                    category=article_data["category"],
                    sources=[source["url"]],
                    league_id=league_id,
                    status="draft",  # published after Telegram send
                )
                await mark_sources_processed(session, [source["id"]], article_id)
                article_data["_article_id"] = article_id
                generated.append(article_data)
                log.info(
                    "news_article_saved id=%s cat=%s title=%s",
                    article_id, article_data["category"],
                    article_data["title"][:50],
                )
            else:
                await mark_sources_processed(session, [source["id"]])
        except Exception:
            log.exception("news_article_gen_failed source_id=%d", source["id"])
            try:
                await session.rollback()
            except Exception:
                pass
            try:
                await mark_sources_processed(session, [source["id"]])
            except Exception:
                log.exception("news_mark_processed_failed source_id=%d", source["id"])
                try:
                    await session.rollback()
                except Exception:
                    pass

    # Phase B: drip-feed to Telegram — spread articles over ~50 min
    published: list[dict[str, Any]] = []
    if generated:
        n = len(generated)
        if n == 1:
            delays = [0]
        else:
            # Spread over 50 min (3000s) with random jitter
            base_interval = 3000 / n
            delays = [0]
            for i in range(1, n):
                jitter = random.uniform(-base_interval * 0.3, base_interval * 0.3)
                delays.append(int(base_interval * i + jitter))
            delays.sort()

        for i, article_data in enumerate(generated):
            if i > 0 and delays[i] > delays[i - 1]:
                wait_sec = delays[i] - delays[i - 1]
                log.info(
                    "news_drip_wait %d/%d delay=%ds next_title=%s",
                    i + 1, n, wait_sec, article_data["title"][:40],
                )
                await asyncio.sleep(wait_sec)

            msg = _format_single_article(article_data)
            sent_ok = await send_to_owner(msg)

            # Mark as published on site simultaneously with Telegram
            aid = article_data.get("_article_id")
            if aid:
                await publish_news_article(session, aid)

            published.append(article_data)
            log.info(
                "news_article_sent id=%s cat=%s sent=%s title=%s",
                aid, article_data["category"],
                sent_ok, article_data["title"][:50],
            )

    log.info(
        "news_generation_done relevant=%d published=%d deferred=%d",
        len(relevant_indices), len(published),
        max(0, len(relevant_indices) - _MAX_PER_RUN - len([
            i for i, s in enumerate(unprocessed)
            if i not in set(relevant_indices)
        ])),
    )

    # 7. Save digest report to DB (for monitoring / agent freshness)
    if published:
        report = _format_news_digest(published)
        await save_report(
            session,
            agent="news",
            report_type="news_digest",
            report_text=report,
            metadata={"fetched": new_count, "published": len(published)},
            telegram_sent=True,
        )

    return {
        "status": "sent" if published else "done",
        "fetched": new_count,
        "published": len(published),
    }

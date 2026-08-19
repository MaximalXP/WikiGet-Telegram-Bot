import os
import logging
from logging.handlers import RotatingFileHandler
import asyncio
import hashlib
import html
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import aiohttp
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    InlineQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in .env file!")

WIKI_API_TIMEOUT = 5
WIKI_SEARCH_LIMIT = 10
WIKI_SUMMARY_SENTENCES = 3
WIKI_FULL_SENTENCES = 12
CACHE_SIZE = 1000


class ColoredFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


log_format = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
date_format = "%Y-%m-%d %H:%M:%S"

console_handler = logging.StreamHandler()
console_handler.setFormatter(ColoredFormatter(log_format, date_format))

file_handler = RotatingFileHandler(
    "wiki_bot.log",
    maxBytes=5 * 1024 * 1024,  # 5 MB per file
    backupCount=3,             # keep 3 old files
    encoding="utf-8",
)
file_handler.setFormatter(logging.Formatter(log_format, date_format))

logging.basicConfig(level=logging.INFO, handlers=[console_handler, file_handler])

logger = logging.getLogger("WikiBot")
api_logger = logging.getLogger("WikiBot.API")
search_logger = logging.getLogger("WikiBot.Search")
inline_logger = logging.getLogger("WikiBot.Inline")
cmd_logger = logging.getLogger("WikiBot.Commands")


@dataclass
class WikiArticle:
    title: str
    summary: str
    url: str
    language: str
    page_id: int
    thumbnail: Optional[str] = None
    full_extract: Optional[str] = None


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


DISAMBIGUATION_PATTERNS = [
    re.compile(r"\bmay refer to\b", re.IGNORECASE),
    re.compile(r"\bmay also refer to\b", re.IGNORECASE),
    re.compile(r"\bfor other uses,?\s*see\b", re.IGNORECASE),
]

NOTICE_PREFIXES = [
    "this article needs additional citations",
    "this article may require cleanup",
    "this article possibly contains original research",
    "this article needs to be",
    "the neutrality of this article is disputed",
    "this article might not be",
    "this article has been",
    "this article needs editing",
    "this article may be too long",
    "this article has multiple issues",
    "this article is incomplete",
    "this article may be unbalanced",
    "this article needs to be updated",
    "this article does not cite any",
    "this article relies excessively",
    "this article may not meet wikipedia",
    "this article should be split",
    "this article provides insufficient context",
    "this article needs references",
    "this article needs expert",
    "this article may contain improper",
    "this article may be confusing",
    "this article may have been",
    "this article is being considered",
    "this article needs to be wikified",
    "this article needs more sources",
    "this article may be biased",
    "this article needs to be rewritten",
    "this article possibly written",
    "this article is about the concept",
    "this article needs additional",
    "this article has some doubt",
    "this article is regarded as",
    "this article may be open to",
    "this article has issues",
    "this article may require additional references",
    "this article's tone or style",
    "this article's lead section",
    "the examples and perspective in this article",
    "this article cites",
    "this article is in list format",
    "this article possibly",
    "this article needs to be cleaned up",
]


def is_disambiguation(title: str, extract: str) -> bool:
    title_lower = title.lower()
    if "(disambiguation)" in title_lower:
        return True

    first_300 = (extract or "")[:300].lower()

    for pattern in DISAMBIGUATION_PATTERNS:
        match = pattern.search(first_300)
        if match and match.start() < 200:
            return True

    return False


def strip_notices(text: str) -> str:
    if not text:
        return text

    plain = strip_html(text).strip()
    lower = plain.lower()

    is_notice = any(lower.startswith(p) for p in NOTICE_PREFIXES)
    if not is_notice:
        return text

    sentences = re.split(r"(?<=[.!?])\s+", plain)
    cut_idx = 0
    for i, s in enumerate(sentences):
        s_lower = s.lower().strip()
        if any(s_lower.startswith(p) for p in NOTICE_PREFIXES):
            cut_idx = i + 1
        else:
            break

    if cut_idx == 0 or cut_idx >= len(sentences):
        return text

    remaining = " ".join(sentences[cut_idx:])
    search_text = remaining[:40]
    pos = text.find(search_text)
    if pos > 0:
        return text[pos:]

    p_breaks = list(re.finditer(r"</p>\s*<p", text, re.IGNORECASE))
    for m in p_breaks:
        before = strip_html(text[: m.start()]).strip().lower()
        if any(before.startswith(p) for p in NOTICE_PREFIXES):
            continue
        return text[m.start() :]

    return remaining


def clean_wiki_html(text: str, language: str = "en", max_length: int = 3500) -> str:
    if not text:
        return "No content available"

    text = strip_notices(text)

    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<ref[^/]*/>", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"<references[^>]*>.*?</references>", "", text, flags=re.DOTALL | re.IGNORECASE
    )

    text = re.sub(r"<sup[^>]*>.*?</sup>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<sup[^/]*/>", "", text, flags=re.IGNORECASE)

    text = re.sub(r"<sub[^>]*>.*?</sub>", "", text, flags=re.DOTALL | re.IGNORECASE)

    text = re.sub(r"</?(?:span|div)[^>]*>", "", text, flags=re.IGNORECASE)

    _remove_tags = [
        "table",
        "figure",
        "math",
        "gallery",
        "sidebar",
        "navbox",
        "templatestyles",
        "img",
        "hr",
        "dl",
        "dt",
        "dd",
        "center",
        "small",
        "big",
        "font",
        "tt",
        "del",
        "ins",
        "abbr",
        "cite",
        "dfn",
        "var",
        "kbd",
        "samp",
        "includeonly",
        "noinclude",
        "onlyinclude",
        "metadata",
        "mbox",
        "ambox",
        "reflist",
        "sref",
        "efn",
        "note",
        "fn",
        "hear",
        "lang",
        "source",
        "nowrap",
        "hlist",
        "plainlist",
        "vertical-navbox",
    ]
    for tag in _remove_tags:
        text = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE
        )
        text = re.sub(rf"<{tag}[^/]*/>", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"</?{tag}[^>]*>", "", text, flags=re.IGNORECASE)

    text = re.sub(
        r"<h[1-6][^>]*>(.*?)</h[1-6]>",
        r"\n\n<b>\1</b>\n",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    text = re.sub(r"<li[^>]*>\s*", "\n• ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:ul|ol)[^>]*>", "\n", text, flags=re.IGNORECASE)

    text = re.sub(r"<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    text = re.sub(r"<blockquote[^>]*>", "\n│ ", text, flags=re.IGNORECASE)
    text = re.sub(r"</blockquote>", "\n", text, flags=re.IGNORECASE)

    _skip_link_prefixes = (
        "Special:",
        "Help:",
        "Wikipedia:",
        "File:",
        "Category:",
        "Template:",
        "Talk:",
        "Portal:",
        "Draft:",
        "Module:",
        "MediaWiki:",
        "User:",
        "Wikipedia_talk:",
        "Category_talk:",
        "File_talk:",
        "Help_talk:",
        "Template_talk:",
        "Portal_talk:",
        "Draft_talk:",
    )

    def _fix_link(m):
        attrs = m.group(1)
        inner = m.group(2)
        href_m = re.search(r'href="([^"]*)"', attrs)
        if not href_m:
            return inner
        href = href_m.group(1)

        if href.startswith("/wiki/"):
            art = href[6:]
            if any(art.startswith(p) for p in _skip_link_prefixes):
                return inner  # text only, no link
            return f'<a href="https://{language}.wikipedia.org{href}">{inner}</a>'
        if href.startswith("//"):
            return f'<a href="https:{href}">{inner}</a>'
        if href.startswith(("http://", "https://")):
            return f'<a href="{href}">{inner}</a>'
        return inner  # unknown – text only

    text = re.sub(
        r"<a\s+([^>]*)>(.*?)</a>", _fix_link, text, flags=re.IGNORECASE | re.DOTALL
    )

    text = re.sub(
        r"<(?!/?(?:b|i|u|s|a|code|pre|blockquote)\b)[^>]*>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"</(?!b|i|u|s|a|code|pre|blockquote)\b>", "", text, flags=re.IGNORECASE
    )

    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    text = text.strip()

    if len(text) > max_length:
        trunc = text[:max_length]
        last_space = trunc.rfind(" ")
        if last_space > max_length * 0.8:
            trunc = trunc[:last_space]
        text = trunc + "..."

    return text if text else "No content available"


def rank_search_results(
    query: str, results: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if not results:
        return results

    query_lower = query.lower().strip()
    query_words = set(query_lower.split())

    def _score(r: Dict[str, Any]) -> int:
        title = r.get("title", "")
        title_lower = title.lower()
        title_base = re.sub(r"\s*\(.*\)\s*$", "", title_lower).strip()

        score = 0

        if title_lower == query_lower:
            score = 100
        elif title_lower.startswith(query_lower):
            score = 90
        elif title_base.startswith(query_lower):
            score = 80
        elif re.search(r"\b" + re.escape(query_lower) + r"\b", title_lower):
            score = 70
        elif query_lower in title_lower:
            score = 60
        elif query_words and all(w in title_lower for w in query_words):
            score = 50
        else:
            snippet_lower = strip_html(r.get("snippet", "")).lower()
            combined = title_lower + " " + snippet_lower
            if query_words and all(w in combined for w in query_words):
                score = 40

        if "(disambiguation)" in title_lower:
            score -= 5

        return score

    ranked = sorted(results, key=_score, reverse=True)

    search_logger.debug(
        f"Ranked {len(ranked)} results for '{query}': "
        f"top='{ranked[0].get('title', '')}' score={_score(ranked[0])}"
        if ranked
        else ""
    )
    return ranked


class WikipediaAPI:
    LANG_PATTERNS = {
        "ru": re.compile(r"[а-яА-ЯёЁ]"),
        "uk": re.compile(r"[іїєґІЇЄҐ]"),
        "ar": re.compile(r"[\u0600-\u06FF]"),
        "he": re.compile(r"[\u0590-\u05FF]"),
        "ja": re.compile(r"[\u3040-\u309F\u30A0-\u30FF]"),
        "zh": re.compile(r"[\u4E00-\u9FFF]"),
        "ko": re.compile(r"[\uAC00-\uD7AF]"),
        "th": re.compile(r"[\u0E00-\u0E7F]"),
        "el": re.compile(r"[\u0370-\u03FF]"),
        "hi": re.compile(r"[\u0900-\u097F]"),
        "bn": re.compile(r"[\u0980-\u09FF]"),
        "ta": re.compile(r"[\u0B80-\u0BFF]"),
        "te": re.compile(r"[\u0C00-\u0C7F]"),
        "fa": re.compile(r"[\u0600-\u06FF]"),
        "hy": re.compile(r"[\u0530-\u058F]"),
        "ka": re.compile(r"[\u10A0-\u10FF]"),
        "de": re.compile(r"[äöüßÄÖÜ]"),
        "fr": re.compile(r"[àâçéèêëîïôûùüÿœæ]"),
        "es": re.compile(r"[áéíóúüñ¿¡]"),
        "pt": re.compile(r"[àáâãçéêíóôõú]"),
    }

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, Any] = {}
        logger.info("WikipediaAPI initialized")

    async def ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=WIKI_API_TIMEOUT)
            connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={"User-Agent": "WikiTelegramBot/1.0 (https://t.me/yourbot)"},
            )
            api_logger.info("Created new aiohttp session")

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
            api_logger.info("Closed aiohttp session")

    def detect_language(self, text: str) -> str:
        for lang, pattern in self.LANG_PATTERNS.items():
            if pattern.search(text):
                api_logger.debug(f"Detected language: {lang} for text: {text[:30]}...")
                return lang
        return "en"

    def _get_cache_key(self, *args) -> str:
        return hashlib.md5(str(args).encode()).hexdigest()

    async def search(
        self,
        query: str,
        language: Optional[str] = None,
        limit: int = WIKI_SEARCH_LIMIT,
    ) -> List[Dict[str, Any]]:
        """Search Wikipedia articles (titles + text)."""
        start_time = datetime.now()

        if not language:
            language = self.detect_language(query)

        cache_key = self._get_cache_key("search", query, language, limit)
        if cache_key in self._cache:
            search_logger.debug(f"Cache hit for search: {query}")
            return self._cache[cache_key]

        await self.ensure_session()

        url = f"https://{language}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": str(limit),
            "format": "json",
            "utf8": "1",
        }

        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get("query", {}).get("search", [])

                    for r in results:
                        r["language"] = language

                    results = rank_search_results(query, results)

                    if len(self._cache) > CACHE_SIZE:
                        self._cache.clear()
                    self._cache[cache_key] = results

                    elapsed = (datetime.now() - start_time).total_seconds() * 1000
                    search_logger.info(
                        f"Search '{query}' ({language}): {len(results)} results in {elapsed:.1f}ms"
                    )
                    return results
                else:
                    api_logger.error(f"Search failed with status {response.status}")
                    return []
        except asyncio.TimeoutError:
            api_logger.error(f"Search timeout for: {query}")
            return []
        except Exception as e:
            api_logger.error(f"Search error: {e}")
            return []

    async def get_summary(
        self,
        title: str,
        language: str = "en",
        sentences: int = WIKI_SUMMARY_SENTENCES,
    ) -> Optional[WikiArticle]:
        """Get article summary (short version) – returns limited HTML."""
        start_time = datetime.now()

        cache_key = self._get_cache_key("summary", title, language, sentences)
        if cache_key in self._cache:
            api_logger.debug(f"Cache hit for summary: {title}")
            return self._cache[cache_key]

        await self.ensure_session()

        url = f"https://{language}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "titles": title,
            "prop": "extracts|info|pageimages",
            "exintro": "1",
            "exsentences": str(sentences),
            "inprop": "url",
            "pithumbsize": "300",
            "format": "json",
            "utf8": "1",
        }

        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    pages = data.get("query", {}).get("pages", {})

                    for page_id, page_data in pages.items():
                        if page_id == "-1":
                            api_logger.warning(f"Article not found: {title}")
                            return None

                        extract = page_data.get("extract", "")
                        article = WikiArticle(
                            title=page_data.get("title", title),
                            summary=extract or "No summary available",
                            url=page_data.get(
                                "fullurl",
                                f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                            ),
                            language=language,
                            page_id=int(page_id),
                            thumbnail=page_data.get("thumbnail", {}).get("source"),
                        )

                        if len(self._cache) > CACHE_SIZE:
                            self._cache.clear()
                        self._cache[cache_key] = article

                        elapsed = (datetime.now() - start_time).total_seconds() * 1000
                        api_logger.info(
                            f"Got summary for '{title}' ({language}) in {elapsed:.1f}ms"
                        )
                        return article
                else:
                    api_logger.error(
                        f"Summary request failed with status {response.status}"
                    )
        except asyncio.TimeoutError:
            api_logger.error(f"Summary timeout for: {title}")
        except Exception as e:
            api_logger.error(f"Summary error for '{title}': {type(e).__name__}: {e}")

        return None

    async def get_full_extract(
        self,
        title: str,
        language: str = "en",
        sentences: int = WIKI_FULL_SENTENCES,
    ) -> Optional[WikiArticle]:
        """Get full article extract – returns limited HTML."""
        start_time = datetime.now()

        cache_key = self._get_cache_key("full", title, language, sentences)
        if cache_key in self._cache:
            api_logger.debug(f"Cache hit for full extract: {title}")
            return self._cache[cache_key]

        await self.ensure_session()

        url = f"https://{language}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "titles": title,
            "prop": "extracts|info|pageimages",
            "exintro": "1",
            "exsentences": str(sentences),
            "inprop": "url",
            "pithumbsize": "500",
            "format": "json",
            "utf8": "1",
        }

        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    pages = data.get("query", {}).get("pages", {})

                    for page_id, page_data in pages.items():
                        if page_id == "-1":
                            api_logger.warning(f"Article not found: {title}")
                            return None

                        extract_text = page_data.get("extract", "")

                        if len(extract_text) > 500:
                            trunc = extract_text[:500]
                            last_open = trunc.rfind("<")
                            last_close = trunc.rfind(">")
                            if last_open > last_close:
                                trunc = trunc[:last_open]
                            summary_val = trunc + "..."
                        else:
                            summary_val = extract_text or "No content available"

                        article = WikiArticle(
                            title=page_data.get("title", title),
                            summary=summary_val,
                            url=page_data.get(
                                "fullurl",
                                f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                            ),
                            language=language,
                            page_id=int(page_id),
                            thumbnail=page_data.get("thumbnail", {}).get("source"),
                            full_extract=extract_text or "No content available",
                        )

                        if len(self._cache) > CACHE_SIZE:
                            self._cache.clear()
                        self._cache[cache_key] = article

                        elapsed = (datetime.now() - start_time).total_seconds() * 1000
                        api_logger.info(
                            f"Got full extract for '{title}' ({language}) in {elapsed:.1f}ms"
                        )
                        return article
                else:
                    api_logger.error(
                        f"Full extract request failed with status {response.status}"
                    )
        except asyncio.TimeoutError:
            api_logger.error(f"Full extract timeout for: {title}")
        except Exception as e:
            api_logger.error(
                f"Full extract error for '{title}': {type(e).__name__}: {e}"
            )

        return None

    async def get_random(
        self,
        language: str = "en",
    ) -> Optional[WikiArticle]:
        """Get a random Wikipedia article."""
        start_time = datetime.now()

        await self.ensure_session()

        url = f"https://{language}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "random",
            "rnnamespace": "0",
            "rnlimit": "1",
            "format": "json",
            "utf8": "1",
        }

        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    random_items = data.get("query", {}).get("random", [])
                    if not random_items:
                        return None

                    title = random_items[0]["title"]
                    article = await self.get_full_extract(title, language)

                    elapsed = (datetime.now() - start_time).total_seconds() * 1000
                    api_logger.info(
                        f"Got random article '{title}' ({language}) in {elapsed:.1f}ms"
                    )
                    return article
                else:
                    api_logger.error(
                        f"Random article request failed with status {response.status}"
                    )
        except asyncio.TimeoutError:
            api_logger.error("Random article timeout")
        except Exception as e:
            api_logger.error(f"Random article error: {type(e).__name__}: {e}")

        return None

    async def search_multilingual(
        self,
        query: str,
        languages: List[str] = None,
        limit_per_lang: int = 3,
    ) -> List[Dict[str, Any]]:
        if languages is None:
            detected = self.detect_language(query)
            languages = [detected]
            if detected != "en":
                languages.append("en")

        tasks = [self.search(query, lang, limit_per_lang) for lang in languages]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        combined = []
        for result in results:
            if isinstance(result, list):
                combined.extend(result)

        search_logger.info(
            f"Multilingual search '{query}': {len(combined)} total results"
        )
        return combined


wiki_api = WikipediaAPI()


class RateLimiter:
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        # user_id -> list of timestamps (seconds since epoch)
        self._hits: Dict[int, List[float]] = {}

    def is_allowed(self, user_id: int) -> bool:
        now = datetime.now().timestamp()
        cutoff = now - self.window

        timestamps = self._hits.get(user_id, [])
        timestamps = [t for t in timestamps if t > cutoff]

        if len(timestamps) >= self.max_requests:
            self._hits[user_id] = timestamps
            return False

        timestamps.append(now)
        self._hits[user_id] = timestamps
        return True

    def seconds_until_allowed(self, user_id: int) -> int:
        now = datetime.now().timestamp()
        cutoff = now - self.window
        timestamps = [t for t in self._hits.get(user_id, []) if t > cutoff]
        if not timestamps:
            return 0
        oldest = timestamps[0]
        wait = int(self.window - (now - oldest)) + 1
        return max(wait, 1)

    def cleanup(self, max_age: int = 300):
        cutoff = datetime.now().timestamp() - max_age
        stale = [uid for uid, ts in self._hits.items() if not ts or ts[-1] < cutoff]
        for uid in stale:
            del self._hits[uid]


rate_limiter = RateLimiter(max_requests=10, window_seconds=60)


def format_short_message(article: WikiArticle) -> str:
    title_escaped = html.escape(article.title)
    content = clean_wiki_html(article.summary, article.language, max_length=1000)

    message = f"""📚 <b>{title_escaped}</b>
🌐 Language: {article.language.upper()}

{content}"""

    return message


def format_full_message(article: WikiArticle) -> str:
    title_escaped = html.escape(article.title)
    raw = article.full_extract or article.summary or "No content available"
    content = clean_wiki_html(raw, article.language, max_length=3500)

    message = f"""📖 <b>{title_escaped}</b>
🌐 Language: {article.language.upper()}

{content}"""

    return message


def format_search_result(result: Dict[str, Any], is_short: bool = False) -> str:
    title = html.escape(result.get("title", "Unknown"))
    snippet = strip_html(result.get("snippet", "")).strip()
    snippet = html.escape(snippet[:150])
    lang = result.get("language", "en").upper()

    mode = "📋 Short" if is_short else "📖 Full"

    return f"""📚 <b>{title}</b>
🌐 {lang} | {mode}

{snippet}..."""


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    cmd_logger.info(f"Start command from user {user.id} ({user.username})")

    bot_username = context.bot.username

    welcome_message = f"""🌐 <b>Wikipedia Bot</b>

Welcome! I can help you search Wikipedia in any language.

<b>📝 How to use:</b>

<b>Inline Mode (any chat):</b>
• Type: <code>@{bot_username} your search</code> - Full article
• Type: <code>@{bot_username} /short your search</code> - Short summary

<b>Direct Commands:</b>
• Just type the article name - Get full article
• <code>/short article name</code> - Get short summary
• <code>/search query</code> - Search articles
• <code>/random</code> - Random interesting article

<b>🌍 Language Detection:</b>
The bot automatically detects the language from your query!

<b>Examples:</b>
• <code>@{bot_username} Python programming</code>
• <code>@{bot_username} /short Эйнштейн</code>
• <code>@{bot_username} 東京</code>
• Just send: <code>Machine learning</code>
• <code>/short Квантовая физика</code>

🚀 Fast & Multilingual!"""

    await update.message.reply_text(welcome_message, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd_logger.info(f"Help command from user {update.effective_user.id}")
    await start_command(update, context)


async def _find_good_article(
    search_results: List[Dict[str, Any]],
    full: bool = True,
) -> Optional[WikiArticle]:
    for result in search_results[:7]:
        if full:
            article = await wiki_api.get_full_extract(
                result["title"], result["language"]
            )
        else:
            article = await wiki_api.get_summary(result["title"], result["language"])

        if article is None:
            continue

        raw = article.full_extract or article.summary or ""
        if is_disambiguation(article.title, raw):
            cmd_logger.debug(f"Skipping disambiguation: {article.title}")
            continue

        cleaned = strip_notices(strip_html(raw))
        if len(cleaned.strip()) < 30:
            cmd_logger.debug(f"Skipping notice-only page: {article.title}")
            continue

        return article

    return None


async def wiki_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    query = " ".join(context.args) if context.args else ""

    cmd_logger.info(f"Wiki command from {user.id}: '{query}'")

    if not rate_limiter.is_allowed(user.id):
        wait = rate_limiter.seconds_until_allowed(user.id)
        await update.message.reply_text(
            f"⏳ Slow down! Please wait {wait}s before sending another request.",
        )
        return

    if not query:
        await update.message.reply_text(
            "❓ Please provide an article name.\n"
            "Example: <code>Python programming</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_chat_action("typing")

    results = await wiki_api.search(query, limit=10)

    if not results:
        results = await wiki_api.search(query, limit=WIKI_SEARCH_LIMIT)

    if not results:
        await update.message.reply_text(
            f"❌ No articles found for: <b>{html.escape(query)}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    article = await _find_good_article(results, full=True)

    if article:
        message = format_full_message(article)
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔗 Open in Wikipedia", url=article.url)]]
        )
        await update.message.reply_text(
            message,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=False,
        )
    else:
        await update.message.reply_text(
            f"❌ Could not retrieve article: <b>{html.escape(query)}</b>",
            parse_mode=ParseMode.HTML,
        )


async def short_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    query = " ".join(context.args) if context.args else ""

    cmd_logger.info(f"Short command from {user.id}: '{query}'")

    if not rate_limiter.is_allowed(user.id):
        wait = rate_limiter.seconds_until_allowed(user.id)
        await update.message.reply_text(
            f"⏳ Slow down! Please wait {wait}s before sending another request.",
        )
        return

    if not query:
        await update.message.reply_text(
            "❓ Please provide an article name.\n"
            "Example: <code>/short Albert Einstein</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_chat_action("typing")

    results = await wiki_api.search(query, limit=10)

    if not results:
        await update.message.reply_text(
            f"❌ No articles found for: <b>{html.escape(query)}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    article = await _find_good_article(results, full=False)

    if article:
        message = format_short_message(article)
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔗 Read Full Article", url=article.url)]]
        )
        await update.message.reply_text(
            message,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    else:
        await update.message.reply_text(
            f"❌ Could not retrieve article: <b>{html.escape(query)}</b>",
            parse_mode=ParseMode.HTML,
        )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    query = " ".join(context.args) if context.args else ""

    cmd_logger.info(f"Search command from {user.id}: '{query}'")

    if not rate_limiter.is_allowed(user.id):
        wait = rate_limiter.seconds_until_allowed(user.id)
        await update.message.reply_text(
            f"⏳ Slow down! Please wait {wait}s before sending another request.",
        )
        return

    if not query:
        await update.message.reply_text(
            "❓ Please provide a search query.\n"
            "Example: <code>/search quantum physics</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_chat_action("typing")

    results = await wiki_api.search(query, limit=5)

    if not results:
        await update.message.reply_text(
            f"❌ No results found for: <b>{html.escape(query)}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    message = f"🔍 <b>Search results for:</b> {html.escape(query)}\n\n"

    for i, result in enumerate(results, 1):
        title = html.escape(result["title"])
        lang = result.get("language", "en").upper()
        snippet = strip_html(result.get("snippet", "")).strip()
        snippet = html.escape(snippet[:100])

        disc_tag = (
            " 📑" if "(disambiguation)" in result.get("title", "").lower() else ""
        )

        message += f"{i}. 📚 <b>{title}</b>{disc_tag} [{lang}]\n"
        message += f"   {snippet}...\n\n"

    message += "\n💡 Just send the article name to get full article"

    await update.message.reply_text(message, parse_mode=ParseMode.HTML)


async def random_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    cmd_logger.info(f"Random command from {user.id}")

    if not rate_limiter.is_allowed(user.id):
        wait = rate_limiter.seconds_until_allowed(user.id)
        await update.message.reply_text(
            f"⏳ Slow down! Please wait {wait}s before sending another request.",
        )
        return

    await update.message.reply_chat_action("typing")

    language = "en"
    if context.args and len(context.args[0]) <= 5:
        lang_arg = context.args[0].lower()
        if lang_arg.isalpha() and len(lang_arg) <= 3:
            language = lang_arg

    article = await wiki_api.get_random(language)

    if article:
        message = format_full_message(article)
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔗 Open in Wikipedia", url=article.url)]]
        )
        await update.message.reply_text(
            message,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=False,
        )
    else:
        await update.message.reply_text(
            "❌ Could not fetch a random article. Try again!",
            parse_mode=ParseMode.HTML,
        )


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    user = update.inline_query.from_user

    inline_logger.info(f"Inline query from {user.id} ({user.username}): '{query}'")

    if not rate_limiter.is_allowed(user.id):
        await update.inline_query.answer([], cache_time=10)
        return

    if not query:
        results = [
            InlineQueryResultArticle(
                id="help",
                title="📖 How to use Wikipedia Bot",
                description="Type article name to search. Use /short for summaries.",
                input_message_content=InputTextMessageContent(
                    message_text="🌐 <b>Wikipedia Bot Help</b>\n\n"
                    "• Type article name for full version\n"
                    "• Type /short + name for summary\n"
                    "• Works in any language!",
                    parse_mode=ParseMode.HTML,
                ),
            )
        ]
        await update.inline_query.answer(results, cache_time=300)
        return

    is_short = False
    if query.lower().startswith("/short"):
        is_short = True
        query = re.sub(r"^/short\s*", "", query, flags=re.IGNORECASE).strip()

    if not query:
        await update.inline_query.answer([], cache_time=10)
        return

    search_results = await wiki_api.search(query, limit=WIKI_SEARCH_LIMIT)

    if not search_results:
        results = [
            InlineQueryResultArticle(
                id="not_found",
                title=f"❌ No results for: {query}",
                description="Try a different search term",
                input_message_content=InputTextMessageContent(
                    message_text=f"❌ No Wikipedia articles found for: <b>{html.escape(query)}</b>",
                    parse_mode=ParseMode.HTML,
                ),
            )
        ]
        await update.inline_query.answer(results, cache_time=60)
        return

    tasks = []
    for result in search_results:
        if is_short:
            tasks.append(wiki_api.get_summary(result["title"], result["language"]))
        else:
            tasks.append(wiki_api.get_full_extract(result["title"], result["language"]))

    articles = await asyncio.gather(*tasks, return_exceptions=True)

    inline_results = []

    for i, (result, article) in enumerate(zip(search_results, articles)):
        if isinstance(article, Exception):
            inline_logger.warning(
                f"Failed to fetch article: {result['title']}: {article}"
            )
            continue
        if article is None:
            continue

        raw = article.full_extract or article.summary or ""
        if is_disambiguation(article.title, raw):
            inline_logger.debug(f"Skipping disambiguation in inline: {article.title}")
            continue

        if is_short:
            message_text = format_short_message(article)
            desc_prefix = "📋 Short | "
        else:
            message_text = format_full_message(article)
            desc_prefix = "📖 Full | "

        summary_preview = strip_html(article.summary or "")[:100]
        description = f"{desc_prefix}{article.language.upper()} | {summary_preview}..."

        result_id = hashlib.md5(
            f"{article.title}_{article.language}_{is_short}_{i}".encode()
        ).hexdigest()

        inline_result = InlineQueryResultArticle(
            id=result_id,
            title=f"📚 {article.title}",
            description=description,
            input_message_content=InputTextMessageContent(
                message_text=message_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔗 Open on Wikipedia", url=article.url)]]
                ),
            ),
            thumbnail_url=article.thumbnail
            or "https://upload.wikimedia.org/wikipedia/en/thumb/8/80/Wikipedia-logo-v2.svg/103px-Wikipedia-logo-v2.svg.png",
            url=article.url,
        )

        inline_results.append(inline_result)

    inline_logger.info(
        f"Returning {len(inline_results)} results for '{query}' (short={is_short})"
    )

    await update.inline_query.answer(
        inline_results,
        cache_time=300,
        is_personal=False,
    )


async def direct_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    if chat.type != "private":
        return

    query = update.message.text.strip()
    user = update.effective_user

    cmd_logger.info(f"Direct message from {user.id}: '{query}'")

    context.args = query.split()
    await wiki_command(update, context)


async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    bot_username = context.bot.username
    text = update.message.text

    if f"@{bot_username}" not in text:
        return

    query = text.replace(f"@{bot_username}", "").strip()

    cmd_logger.info(f"Group mention from {update.effective_chat.id}: '{query}'")

    if not query:
        await update.message.reply_text(
            "💡 Type a Wikipedia article name after mentioning me!\n"
            f"Example: <code>@{bot_username} Python programming</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    context.args = query.split()
    await wiki_command(update, context)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=context.error)

    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again later."
            )
        except Exception:
            pass


async def post_init(application: Application):
    logger.info("Bot post-init: ensuring API session")
    await wiki_api.ensure_session()
    bot_info = await application.bot.get_me()
    logger.info(f"Bot started: @{bot_info.username} (ID: {bot_info.id})")


async def post_shutdown(application: Application):
    logger.info("Bot shutdown: closing API session")
    await wiki_api.close()


def main():
    logger.info("=" * 60)
    logger.info("Starting Wikipedia Telegram Bot")
    logger.info("=" * 60)

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("short", short_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("random", random_command))

    application.add_handler(InlineQueryHandler(inline_query_handler))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            direct_message_handler,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
            group_message_handler,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("Handlers registered successfully")
    logger.info("Bot is starting polling...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()

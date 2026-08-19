<div align="center">

# 🌐 WikiGet

**A fast, multilingual Wikipedia bot for Telegram**

[![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![telegram-bot](https://img.shields.io/badge/Telegram%20Bot%20API-21.3-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots/api)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[Features](#features) | [Demo](#demo) | [Setup](#setup) | [Commands](#commands) | [Configuration](#configuration) | [Deploy](#deploy)

</div>

---

## About

**WikiGet** is a Telegram bot that lets you search and read Wikipedia articles directly inside Telegram. It supports **every language** Wikipedia is available in, with automatic language detection from your query.

Whether you're in a private chat, a group, or using inline mode in any conversation — WikiGet brings Wikipedia to your fingertips, instantly.

## Try it now

No setup needed — just open the bot on Telegram: **[@WikiGetBot](https://t.me/wikigetbot)**

<div align="center">

[<img src="https://img.shields.io/badge/@WikiGetBot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="@WikiGetBot"/>](https://t.me/wikigetbot)

</div>

- Send any article name in a DM to get the full article
- Use it inline in any chat: `@wikigetbot <article>`
- Works in groups/channels/private chats — just @mention it

## Features

- **Smart Search** — Searches article titles, descriptions, and content simultaneously
- **Multilingual** — Auto-detects language from your query (Russian, Chinese, Arabic, Armenian, and 20+ more)
- **Formatting Preserved** — Bold, italic, links, and quotes from Wikipedia are kept intact
- **Random Article** — Discover something new with `/random`
- **Inline Mode** — Search Wikipedia from *any* chat without leaving the conversation
- **Disambiguation Filtering** — Automatically skips "may refer to" pages and shows the real article
- **Notice Removal** — Strips maintenance banners ("This article needs verification…") so you only see content
- **Smart Ranking** — Exact title matches always appear first in search results
- **Rate Limiting** — Prevents abuse with per-user request limits (10 requests/60s per user)
- **Caching** — Results are cached for fast repeated queries

## Demo

**Private Chat** — Just type an article name:

```
You:  Armenia
Bot:  Armenia
      Language: EN

      Armenia (hy: Hayastan), officially the Republic of Armenia,
      is a landlocked country in the Armenian Highlands of West Asia...

      [Open on Wikipedia]
```

**Inline Mode** — Search from any chat:

```
You:  @yourbot /short量子力学
Bot:  Short | EN | Quantum mechanics is a fundamental theory...
      [Open on Wikipedia]
```

## Setup

### Prerequisites

- **Python 3.8+**
- A **Telegram Bot Token** — get one from [@BotFather](https://t.me/BotFather)

### Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/MaximalXP/WikiGet-Telegram-Bot.git
   cd WikiGet-Telegram-Bot
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**

   Create a `.env` file in the project root:

   ```env
   BOT_TOKEN=your_telegram_bot_token_here
   ```

4. **Run the bot**

   ```bash
   python bot.py
   ```

The bot will start polling for updates immediately. You'll see colored logs in the console confirming it's running.

## Commands

| Command | Description | Example |
|---------|-------------|---------|
| *(just type)* | Get a full article by name | `Machine learning` |
| `/short <article>` | Get a short summary | `/short Albert Einstein` |
| `/search <query>` | Search Wikipedia articles | `/search quantum physics` |
| `/random` | Get a random interesting article | `/random` |
| `/random {lang_code}` | Random from Armenian Wikipedia | `/random hy` |
| `/random /short` | Random short summary | `/random /short` |
| `/random {lang_code} /short` | Random Armenian short summary | `/random hy /short` |
| `/start` | Show welcome message | `/start` |
| `/help` | Show help | `/help` |

### Inline Mode

Type `@yourbot` followed by a search query in **any** chat:

- `@yourbot Python` — Full article
- `@yourbot /short Einstein` — Short summary
- `@yourbot /random` — Random article
- `@yourbot /random hy` — Random from Armenian Wikipedia
- `@yourbot /random /short` — Random short summary
- `@yourbot 東京` — Works in any language

### Groups

WikiGet responds in groups **only when @mentioned**:

```
@yourbot Quantum entanglement
```

Commands like `/short` and `/search` also work in groups.

## Configuration

All settings are at the top of `bot.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `WIKI_API_TIMEOUT` | `5` | Timeout for Wikipedia API requests (seconds) |
| `WIKI_SEARCH_LIMIT` | `10` | Maximum search results per query |
| `WIKI_SUMMARY_SENTENCES` | `3` | Sentences in short summaries |
| `WIKI_FULL_SENTENCES` | `12` | Sentences in full articles |
| `CACHE_SIZE` | `1000` | Maximum cached entries |

### Rate Limiting

```python
rate_limiter = RateLimiter(max_requests=10, window_seconds=60)
```

Adjust the limits to fit your needs. Default: **10 requests per 60 seconds** per user.

## Deploy

### Files to upload to GitHub

| File | Purpose |
|------|---------|
| `bot.py` | Main bot code |
| `requirements.txt` | Python dependencies |
| `README.md` | Documentation |
| `LICENSE` | MIT license |
| `.gitignore` | Git ignore rules |

> **Do NOT commit `.env`, `wiki_bot.log`, or cache directories.** They are all excluded by `.gitignore`.

### Files to upload to hosting (e.g. Alwaysdata)

| File | Purpose |
|------|---------|
| `bot.py` | The bot itself |
| `requirements.txt` | Dependencies to install |
| `.env` | Your `BOT_TOKEN` |

Only 3 files needed — no README or LICENSE required on hosting.

## Architecture

```
wikiget/
├── bot.py              # Main bot file (handlers, API, formatting)
├── requirements.txt    # Python dependencies
├── .env                # Environment variables (not committed)
├── .gitignore          # Git ignore rules
├── LICENSE             # MIT license
└── README.md           # This file
```

### Key Components

- **`WikipediaAPI`** — Async HTTP client with caching, language detection, and search ranking
- **`RateLimiter`** — Sliding-window per-user rate limiting
- **`clean_wiki_html()`** — Converts Wikipedia's HTML to Telegram-compatible formatting
- **`rank_search_results()`** — Re-ranks results to prioritize exact title matches
- **`is_disambiguation()`** — Detects and filters disambiguation pages
- **`strip_notices()`** — Removes maintenance hatnotes from article text

## Contributing

Contributions are welcome! Feel free to:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Wikipedia API](https://www.mediawiki.org/wiki/API:Main_page) — The backbone of all article data
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) — Telegram Bot framework
- [Wikipedia](https://www.wikipedia.org/) — The free encyclopedia

---

<div align="center">

**Made with 💚 by Maxim Mkrtchyan (@MaximalXP)**

</div>

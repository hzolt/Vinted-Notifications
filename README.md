# Vinted-Notifications (September 2026 API fix + AI filter)

A real-time notification system for Vinted listings that works across all Vinted country
domains. Get instant alerts when items matching your search criteria are posted.

This fork keeps the bot working after **Vinted retired the legacy catalogue API in
September 2026**, and adds an **AI filter**: two AI models (one looks at the photos, one reads
the listing text) check each new item against a plain-language description of what you want,
before it is sent to Telegram or RSS.

The old `www.vinted.<tld>/api/v2/catalog/items` endpoint now returns `404` for every request;
the catalogue moved to a dedicated host, `api.vinted.<tld>/svc-catalogue/items`, behind a
bearer token that the `www` host still hands out on a plain request (so no account or API key
is needed for searching).

## Fork lineage & credits

- Original project: [Fuyucch1/Vinted-Notifications](https://github.com/Fuyucch1/Vinted-Notifications)
  (unmaintained since ~late 2025).
- September 2026 API fix: [3vilrabbit/Vinted-Notifications](https://github.com/3vilrabbit/Vinted-Notifications),
  which updated the catalogue endpoint, renamed/removed filters, item parsing, and the
  "new item" detection after Vinted dropped listing timestamps.
- Container image workflow: [TerrorSource/Vinted-Notifications](https://github.com/TerrorSource/Vinted-Notifications).
- This fork adds the AI filter, the seller's rating, the Dashboard and Items page additions,
  and versioned releases (see [What this fork adds](#what-this-fork-adds)).

## What this fork adds

- **AI filter** per query: a photos reviewer and a text reviewer check each new item against
  your description; only items that fit are sent. See [AI filter](#ai-filter) and
  [AI-FILTER.md](AI-FILTER.md) for the details.
- **AI Vision filter** per query: extra instructions for the photos reviewer only ("look for
  scratches", "stock photos or the seller's own?"). Without an AI filter text, nothing is
  filtered and every item is sent with the photos reviewer's remarks.
- **Seller's rating** on every message (`👤 Seller: rating 4.9/5 from 37 reviews`), read from
  the item page. `NOTIFY_SELLER_RATING=false` turns it off.
- **Web UI**
  - **Dashboard:** statistics (Total Items, Active Queries, Items per Day, Items per Last
    Hour, Last Found Item), whether the AI filter is on, and the AI decision for each recent
    item.
  - **Queries:** AI filter and AI Vision filter boxes, and a **Test** button that runs them
    against the query's last 3 items without saving anything.
  - **Items:** each checked item shows the AI outcome (fits / doesn't fit / unsure /
    maybe (reviewers disagree) / check failed, sent or skipped) with both reviewers' reasons.
  - **Logs:** the bot's log, filterable by level.
  - The header shows the running version and "Update available" when a newer release is
    published.
- **Telegram:** commands are accepted only from the configured chat; seller-written text is
  escaped so a title with `<` or `&` is no longer lost.
- **Robustness:** a busy database is waited for longer, an expired Vinted token is renewed,
  dropped connections are retried, and the item extractor and dispatcher keep running after
  an error; the cause is always logged.

## AI filter

Write what you want in plain language (Hungarian or English) in a query's **AI filter** box,
for example *"Original Sega Master System games, no Mega Drive cartridges, no bundles"*. For
each new item from that query the bot loads the full listing (description, all photos,
condition, seller rating) and asks two reviewers at the same time:

- **Photos** (`AI_FILTER_VISION_MODEL`, default `minimax/minimax-m3`): looks at up to 4 photos.
- **Text** (`AI_FILTER_TEXT_MODEL`, default `deepseek/deepseek-v4.1-flash`): reads the title,
  description, condition, size, price and the seller's rating.

Each reviewer answers `yes`, `no` or `maybe`, judging only what its own evidence can show, and
says `maybe` rather than `no` when it is not sure. The two answers become the final decision
according to `AI_FILTER_COMBINE`.

### Decision matrix

| Photos | Text | `maybe` (default) | `veto` | `strict` |
|---|---|---|---|---|
| no | no | no, skipped | no, skipped | no, skipped |
| yes | yes | yes, sent | yes, sent | yes, sent |
| maybe | maybe | maybe, sent ❔ | maybe, sent ❔ | maybe, sent ❔ |
| no | yes | maybe, sent ❔ | no, skipped | no, skipped |
| no | maybe | maybe, sent ❔ | no, skipped | no, skipped |
| yes | no | maybe, sent ❔ | no, skipped | no, skipped |
| yes | maybe | yes, sent | yes, sent | maybe, sent ❔ |
| maybe | no | maybe, sent ❔ | no, skipped | no, skipped |
| maybe | yes | yes, sent | yes, sent | maybe, sent ❔ |

- **`maybe`** (default): an item is skipped only when both reviewers say `no`. When one says
  `no` and the other `yes` or `maybe`, the reviewers disagree: the item is sent with
  `🤖❔ AI: maybe, reviewers disagree`.
- **`veto`**: any `no` skips the item.
- **`strict`**: any `no` skips the item, and `yes` needs a `yes` from both reviewers.

In every mode:

| Situation | Result |
|---|---|
| One reviewer fails (timeout, bad reply) or is set to `off` | The other one decides alone |
| Both reviewers fail | Sent with `⚠️ AI check skipped` (`AI_FILTER_FAIL_OPEN=false` skips it) |
| Final decision `maybe` | Sent with ❔ (`AI_FILTER_NOTIFY_MAYBE=false` skips it) |

A sent message ends with the outcome and both reviewers' reasons:

```
👤 Seller: rating 4.9/5 from 37 reviews
🤖 AI: fits
👁 yes: Genuine Bedale, zip intact
📝 yes: Size L, "barely worn", no defects
```

Queries without an AI filter text behave exactly as before. The AI check runs only on items
that would otherwise be sent, so tokens are not spent on items already seen, banned or outside
the country allowlist.

> **Changed default:** before the `maybe` mode existed, the default was `veto`. Set
> `AI_FILTER_COMBINE=veto` to keep skipping every item a reviewer rejects.

### Settings

The AI filter is configured through the container environment; the API key never lands in
the database or the web UI.

| Variable | Default | |
|---|---|---|
| `NANOGPT_API_KEY` | (none) | Required. Without it the AI filter is off and every query behaves as upstream. |
| `AI_FILTER_VISION_MODEL` | `minimax/minimax-m3` | Photos reviewer; `off` to disable. Must accept images. |
| `AI_FILTER_TEXT_MODEL` | `deepseek/deepseek-v4.1-flash` | Text reviewer; `off` to disable. |
| `AI_FILTER_COMBINE` | `maybe` | `maybe`, `veto` or `strict`, see the [decision matrix](#decision-matrix). |
| `AI_FILTER_NOTIFY_MAYBE` | `true` | Send items whose final decision is `maybe`. |
| `AI_FILTER_FAIL_OPEN` | `true` | Send items when neither reviewer could answer. |
| `NOTIFY_SELLER_RATING` | `true` | Seller's rating on every message, AI filter or not. |

All settings (timeouts, photo count and size, fetching the full listing, ...), what is stored
in the database, shell checks and tips for writing a good filter text are in
[AI-FILTER.md](AI-FILTER.md).

## Image

Images are built by GitHub Actions and published to GitHub Container Registry:

| Event | Image tags |
|---|---|
| Push to `main` | `ghcr.io/hzolt/vinted-notifications:main` (test build) |
| Release published | `:<tag>` (e.g. `:v1.01`) and `:latest` |
| Pre-release published | `:<tag>` only |

`:latest` only changes when a release is published, so a server running `:latest` gets
tested releases only. If you fork this repo, the workflow publishes under your own username
instead. Set the package to **public** (Packages → the package → Package settings → Change
visibility) so it can be pulled without registry credentials.

### Releasing

After each push to `main`, a draft release lists every commit since the last published one.
When you are ready, open the draft under **Releases**, choose a new tag (e.g. `v1.01`, target
`main`) and publish: the image is built as `:v1.01` and `:latest`. Details in
[AI-FILTER.md](AI-FILTER.md#releasing).

## Deploy (Docker Compose / Portainer)

```yaml
version: "3"

services:
  vinted-notifications:
    container_name: vinted-notifications
    image: ghcr.io/hzolt/vinted-notifications:latest
    pull_policy: always
    init: true
    restart: unless-stopped
    network_mode: bridge
    environment:
      - TZ=Europe/Amsterdam
      - PUID=1000
      - PGID=1000
      # AI filter: leave the key empty to run without it
      - NANOGPT_API_KEY=
      # Optional, shown with their defaults:
      # - AI_FILTER_VISION_MODEL=minimax/minimax-m3
      # - AI_FILTER_TEXT_MODEL=deepseek/deepseek-v4.1-flash
      # - AI_FILTER_COMBINE=maybe        # maybe | veto | strict
    ports:
      - 8200:8000        # Web UI (change the host port to taste)
      # - 8280:8080      # RSS feed (optional; uncomment if you use RSS)
    volumes:
      - /share/CACHEDEV1_DATA/Docker/vinted-notifications/data:/app/data
      - /share/CACHEDEV1_DATA/Docker/vinted-notifications/logs:/app/logs
```

[docker-compose.yml](docker-compose.yml) lists every optional setting with its default.
Adjust the volume host paths and ports to your setup. In Portainer, deploy via **Web editor**
(paste the compose) or **Repository**: either works, since the compose pulls a registry image
and doesn't build anything.

After starting, open the Web UI (e.g. `http://<host>:8200`) to configure Telegram, queries, and
other settings. Your database, queries and config live in the mounted `data` volume, so they
survive image updates. The AI filter's columns and table are added to an existing database on
first start; nothing else changes.

> **Upgrading from the old broken image?** Back up your `data` folder first: the fixed code
> runs a migration for the dropped-timestamp change. Then just swap the `image:` line and
> redeploy; your existing database and queries are preserved.

## Features

- **Web UI** to manage everything
- **Multi-country support** across all Vinted domains
- **Real-time notifications** for new listings
- **Multiple search queries** monitored at once
- **Country filtering** by seller origin
- **RSS feed** and **Telegram** integration
- **AI filter** per query (see above)

## Usage

### Adding a query

Add a query as a full Vinted catalog URL (filters included), via the Web UI or Telegram:

```
/add_query https://www.vinted.nl/catalog?search_text=nike%20shoes&price_to=50&currency=EUR&brand_ids[]=53
```

The AI filter and AI Vision filter texts are set on the Web UI's **Queries** page, in the add
form or a query's **Edit** dialog.

### Telegram commands

`/hello`, `/add_query <url>`, `/remove_query <n>`, `/remove_query all`, `/queries`,
`/allowlist`, `/add_country XX`, `/remove_country XX`, `/clear_allowlist`.

Commands are accepted only from the chat set in the Web UI's configuration.

### Custom notification format

```
MESSAGE = '''\
🆕 Title: {title}
💶 Price: {price}
🛍️ Brand: {brand}
<a href='{image}'>&#8205;</a>
'''
```

The seller's rating and the AI outcome are added below the message.

## Notes

- The `svc-catalogue` endpoint works on the anonymous token the `www` host hands out, so a
  logged-in token is not required for searching. (If you separately run a token refresher for
  logged-in features, it does no harm: it just isn't needed for catalogue search anymore.)
- Because upstream is unmaintained, a fork like this is responsible for its own updates. If
  Vinted changes the API again, watch 3vilrabbit (or the wider community) for the next fix and
  sync it into your fork ([how](AI-FILTER.md#keeping-up-with-fixes)).

## License

[GNU Affero General Public License](LICENSE), inherited from the upstream project.

## Acknowledgements

- [Fuyucch1](https://github.com/Fuyucch1) for the original project.
- [3vilrabbit](https://github.com/3vilrabbit) for the September 2026 API fix.
- [TerrorSource](https://github.com/TerrorSource) for the container image workflow.
- [@herissondev](https://github.com/herissondev) for pyVinted, a core dependency.

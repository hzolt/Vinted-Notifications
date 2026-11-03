# AI filter (fork addition)

Each query can have an **AI filter**: a plain-language description of what you
want. New items from that query are sent to a vision model (NanoGPT,
`minimax/minimax-m3` by default) with their main photo, title, brand, size,
condition and price. Only items the model accepts are sent to Telegram/RSS,
with a one-line reason under the message.

Queries without an AI filter text behave exactly as before.

## How an item flows

```
seen before? -> country allowlist -> banwords -> recorded as seen
   -> (first run of a query: stop here, nothing is announced)
   -> cap: at most 10 announcements per run, newest kept
   -> AI filter (only if the query has one) -> notify
```

The AI check runs last, so tokens are only spent on items that would otherwise
be announced. Because every item is recorded as seen before the check, a
rejected listing is never judged twice.

| Model answer | What happens |
|---|---|
| `yes` | notified, with `🤖 <reason>` |
| `maybe` (can't tell from the photo/fields) | notified with `🤖❔ <reason>` (set `AI_FILTER_NOTIFY_MAYBE=false` to skip these) |
| `no` | not notified; logged, and shown on the Items page as "AI: skipped" |
| model error / timeout / unreadable reply | notified with `⚠️ AI check skipped: <why>` (set `AI_FILTER_FAIL_OPEN=false` to skip instead) |

Every decision is stored with the item, so the **Items** page shows a badge
(sent / unsure, sent / skipped / failed, sent) with the reason under each checked
item. The **Logs** page has the same, one line per item:

```
AI filter [query 3] NO (4.2 s, 1180 in-tokens): Bőr dzseki | 25.0 EUR | faux leather visible at the seams | https://www.vinted.hu/items/...
```

## Configuration (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `NANOGPT_API_KEY` | (none) | Required. Without it the AI filter is off and every query behaves as upstream. |
| `AI_FILTER_ENABLED` | `true` | Kill switch that keeps the key in place. |
| `AI_FILTER_MODEL` | `minimax/minimax-m3` | Any vision model in the NanoGPT subscription. |
| `AI_FILTER_BASE_URL` | `https://nano-gpt.com/api/subscription/v1` | Subscription-only endpoint: never spends prepaid balance. |
| `AI_FILTER_NOTIFY_MAYBE` | `true` | Notify when the model can't tell. |
| `AI_FILTER_FAIL_OPEN` | `true` | Notify when the model call fails. |
| `AI_FILTER_TIMEOUT` | `90` | Seconds per model call. |
| `AI_FILTER_MAX_TOKENS` | `2000` | Output budget; reasoning models need room. Output tokens don't count against the weekly allowance. |
| `AI_FILTER_IMAGE_MAX_PX` | `768` | Photo is downscaled and sent as JPEG; smaller = fewer input tokens. |

The key lives only in the container environment, never in the SQLite database
or the web UI.

## Using it

* **Queries page:** an "AI filter" box under the add form, a column in the
  query list, and a box in each query's Edit dialog.
* **Test button** (Edit dialog): runs the text currently in the box against
  the last 5 items that query found and shows yes/no/maybe with reasons,
  without saving. Stored items only have title, price and photo, so live
  checks see slightly more (brand, size, condition).
* **Check from the shell** that the key and model work:

  ```sh
  docker exec -it <container> python ai_filter.py \
    --intent "Only genuine Barbour waxed jackets, size L, good condition" \
    --title "Barbour Bedale viaszos kabát" --price 45 --currency EUR \
    --photo "https://images1.vinted.net/....webp"
  ```

### Writing a good filter text

* Name the things a photo or the fields can show: material, model, size,
  visible wear, colour, "single item, not a bundle".
* Say what to do when it can't be seen ("if condition isn't visible, say
  maybe").
* Hungarian or English both work; the reason comes back in the language you
  wrote the filter in.
* The model does **not** see the listing description (that would cost one more
  Vinted request per item, and the bot already gets rate-limited on user
  lookups).

## Deploying

This fork carries `.github/workflows/build.yml`, which builds the image on
every push to `main` and publishes it as
`ghcr.io/<your-github-user>/vinted-notifications:latest`. Make the package
public once (GitHub → Packages → vinted-notifications → Package settings →
Change visibility) so the NAS can pull it without credentials.

```yaml
services:
  vinted-notifications:
    image: ghcr.io/<your-github-user>/vinted-notifications:latest
    pull_policy: always
    environment:
      NANOGPT_API_KEY: "..."
      # AI_FILTER_MODEL: minimax/minimax-m3
    # ports, volumes, restart: keep as they are
```

Keep the existing data volume: the new columns are added on first start and
nothing else in the database changes. Watchtower can keep updating the image
as usual.

To build locally instead: `docker build -t vinted-notifications:ai .` in this
folder, use `image: vinted-notifications:ai`, drop `pull_policy: always` and
label the service `com.centurylinklabs.watchtower.enable: "false"`.

### Keeping up with fixes

The scraping code comes from 3vilrabbit/Vinted-Notifications (the September
2026 Vinted API fix); TerrorSource only adds the image workflow. To pull in
future fixes:

```sh
git remote add rabbit https://github.com/3vilrabbit/Vinted-Notifications.git
git remote add terrorsource https://github.com/TerrorSource/Vinted-Notifications.git
git fetch --all
git merge rabbit/main        # or terrorsource/main
```

The AI filter touches `core.clear_item_queue` (the notify loop at its end),
`db.py` (new functions only), the web UI and two templates, so merges rarely
conflict.

## Database

`queries.ai_intent`, `items.ai_decision` and `items.ai_reason` (all `TEXT`)
are added at startup if missing (idempotent). The `version` parameter is
**not** changed, so the built-in update check keeps working.

## Cost

Roughly 1-2k input tokens per checked item (photo + text); the Test page shows
the real figure per item. Only queries with an AI filter text use tokens.

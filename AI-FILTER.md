# AI filter (fork addition)

Each query can have an **AI filter**: a plain-language description of what you
want. New items from that query are checked by two reviewers before anything is
sent to Telegram/RSS:

* **Photos** — a vision model (`minimax/minimax-m3` by default) looks at up to
  4 photos of the listing.
* **Text** — a text model (`deepseek/deepseek-v4.1-flash` by default) reads the
  title, description, condition, size, measurements, colour, price and the
  seller's rating.

Both decisions and the final outcome are stored, shown on the **Items** page
and logged. Queries without an AI filter text behave exactly as before.

## How an item flows

```
seen before? -> country allowlist -> banwords -> recorded as seen
   -> (first run of a query: stop here, nothing is announced)
   -> cap: at most 10 announcements per run, newest kept
   -> AI filter (only if the query has one):
        fetch full listing -> photos reviewer ┐ in parallel
                              text reviewer   ┘ -> final decision -> notify?
```

The AI check runs last, so tokens are only spent on items that would otherwise
be announced. Every item is recorded as seen before the check, so a rejected
listing is never judged twice.

### Full listing

Vinted's search results only carry the main photo, title, brand, size,
condition and price. For each checked item the bot loads the item page
(`www.vinted.<tld>/items/<id>`) through its own Vinted session and reads, in
order of preference:

| Details source | What the page gave |
|---|---|
| `page` | the page's embedded data: description, every photo, condition, size, brand and the other attributes (colour, material, author, ISBN, ...), seller rating and review count, reserved or not |
| `jsonld` | only the schema.org product block: description, main photo, price, new/used |
| `preview` | only the link-preview description (`og:description`), e.g. old or sold listings |
| `catalog` | nothing usable (blocked, error): the check runs on search-result data, and the Items page says "listing details were not available" |

That is one extra Vinted request per checked item (not per scraped item). The
seller's username is never sent to the model, only their rating.
`www.vinted.<tld>/api/v2/items/<id>` is not used: since the September 2026 API
change it answers 404 to anonymous clients.

### Final decision

Each reviewer answers `yes`, `no` or `maybe`, judging only what its evidence
can show (photos can't confirm a size; text can't show a stain).

| | Final |
|---|---|
| any reviewer says `no` | `no`, not sent |
| otherwise, at least one `yes` | `yes`, sent |
| otherwise (both `maybe`) | `maybe`, sent with ❔ (set `AI_FILTER_NOTIFY_MAYBE=false` to skip) |
| one reviewer fails (timeout, bad reply) | decided by the other one |
| both fail | sent with `⚠️ AI check skipped` (set `AI_FILTER_FAIL_OPEN=false` to skip) |

`AI_FILTER_COMBINE=strict` requires a `yes` from every reviewer that answered;
anything less becomes `maybe`.

A sent message ends with:

```
🤖 AI: fits
👁 yes: Eredeti Bedale, ép cipzár
📝 yes: L méret, „alig hordott”, nincs hiba
```

### What is stored

Table `ai_verdicts`, one row per checked item and query:

| Column | |
|---|---|
| `item_id`, `query_id`, `checked_at` | which item, which query, when (unix time) |
| `intent` | the AI filter text used for this check |
| `final_decision`, `final_reason`, `notified` | outcome and whether it was sent (1/0) |
| `vision_model`, `vision_decision`, `vision_reason`, `vision_tokens` | photos reviewer |
| `text_model`, `text_decision`, `text_reason`, `text_tokens` | text reviewer |
| `details_source` | `page`, `jsonld`, `preview` or `catalog` (see above) |
| `seconds` | total time for the check |

The database is `data/vinted_notifications.db` on the data volume; open it with
any SQLite tool, or from the container (the image has no `sqlite3` binary):

```sh
docker exec -u 10001:10001 vinted-notifications python -c "
import sqlite3
for r in sqlite3.connect('data/vinted_notifications.db').execute('''
    SELECT datetime(checked_at, 'unixepoch', 'localtime'), final_decision, notified,
           vision_decision, vision_reason, text_decision, text_reason
    FROM ai_verdicts ORDER BY checked_at DESC LIMIT 20'''):
    print(r)"
```

## Configuration (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `NANOGPT_API_KEY` | (none) | Required. Without it the AI filter is off and every query behaves as upstream. |
| `AI_FILTER_ENABLED` | `true` | Kill switch that keeps the key in place. |
| `AI_FILTER_VISION_MODEL` | `minimax/minimax-m3` | Photos reviewer; `off` to disable. (`AI_FILTER_MODEL` from the first version still works.) |
| `AI_FILTER_TEXT_MODEL` | `deepseek/deepseek-v4.1-flash` | Text reviewer; `off` to disable. |
| `AI_FILTER_MAX_PHOTOS` | `4` | Photos sent to the vision model. |
| `AI_FILTER_FETCH_DETAILS` | `true` | Fetch the full listing per checked item. |
| `AI_FILTER_COMBINE` | `veto` | `veto` or `strict`, see above. |
| `AI_FILTER_NOTIFY_MAYBE` | `true` | Send when the outcome is `maybe`. |
| `AI_FILTER_FAIL_OPEN` | `true` | Send when no reviewer could answer. |
| `AI_FILTER_BASE_URL` | `https://nano-gpt.com/api/subscription/v1` | Subscription-only endpoint: never spends prepaid balance. |
| `AI_FILTER_TIMEOUT` | `90` | Seconds per model call. |
| `AI_FILTER_MAX_TOKENS` | `2000` | Output budget per call; reasoning models need room. Output tokens don't count against the weekly allowance. |
| `AI_FILTER_IMAGE_MAX_PX` | `768` | Photos are downscaled and sent as JPEG; smaller = fewer input tokens. |

The key lives only in the container environment, never in the SQLite database
or the web UI.

## Using it

* **Queries page:** an "AI filter" box under the add form, a column in the
  query list, a box in each query's Edit dialog, and a status line naming both
  models.
* **Test button** (Edit dialog): runs the text currently in the box against
  the last 3 items that query found — full listings, both reviewers — and
  shows each reviewer's answer, without saving.
* **Items page:** each checked item shows the outcome (fits / doesn't fit /
  unsure / check failed, sent or skipped) with both reviewers' reasons.
* **Shell checks** from the NAS. Run them as the bot's user (uid 10001) so no
  root-owned files end up in the data folder, and quote the URL:

  ```sh
  # Does the full-listing fetch work? (no tokens used)
  docker exec -u 10001:10001 vinted-notifications python ai_filter.py \
    --item "https://www.vinted.hu/items/<id>" --details-only

  # Full check of one listing against a filter text
  docker exec -u 10001:10001 vinted-notifications python ai_filter.py \
    --item "https://www.vinted.hu/items/<id>" \
    --intent "Only genuine Barbour waxed jackets, size L, no damage"
  ```

  Replace `<id>` with a real listing's number (for example from **View on
  Vinted** on the Items page).

  `Details source: page` means the full listing reaches both models. `jsonld`
  or `preview` means only part of it did, and `catalog` plus an error note
  means the item page could not be loaded.

### Writing a good filter text

* Mix what photos show (model, colour, visible wear, single item vs bundle)
  with what text states (size, measurements, defects, "never worn"); each
  reviewer picks the parts it can judge.
* Say what you don't want ("no replicas", "no bundles", "nothing for parts").
* Hungarian or English both work; reasons come back in the language you wrote
  the filter in.

## Deploying

Images are built by `.github/workflows/build.yml` and published to
`ghcr.io/<your-github-user>/vinted-notifications`:

| Event | Image tags |
|---|---|
| push to `main` | `:main` (test build, version shown as `main-<commit>`) |
| release published | `:<tag>` (e.g. `:v1.01`) and `:latest` |
| pre-release published | `:<tag>` only |

The NAS runs `:latest`, so it only changes when you publish a release. Make
the package public once (GitHub → Packages → vinted-notifications → Package
settings → Change visibility) so the NAS can pull it without credentials.

### Releasing

1. Work on the code and push/merge to `main` as often as you like. After each
   push, `.github/workflows/release-draft.yml` updates a **draft release**
   listing every commit since the last published release.
2. When you are ready: GitHub → Releases → open the draft → Edit.
3. Choose a tag: type the new version (e.g. `v1.01`) and pick "Create new tag
   on publish" (target `main`). Change the title and add text if you like; only
   the part between the `auto` markers is rewritten by later pushes.
4. Publish. The tag is created on `main`, the image is built as `:v1.01` and
   `:latest`, and a fresh draft starts for the next release.

The web UI header shows the running version (the release tag baked into the
image) and "Update available" when a newer release is published in this
repository. `:main` test builds never report an update. Set `UPDATE_CHECK_REPO`
to follow a different repository.

```yaml
services:
  vinted-notifications:
    image: ghcr.io/<your-github-user>/vinted-notifications:latest
    pull_policy: always
    environment:
      NANOGPT_API_KEY: "..."
      # AI_FILTER_VISION_MODEL: minimax/minimax-m3
      # AI_FILTER_TEXT_MODEL: deepseek/deepseek-v4.1-flash
    # ports, volumes, restart: keep as they are
```

Keep the existing data volume: the `ai_verdicts` table and `queries.ai_intent`
column are created on first start; nothing else in the database changes.
Watchtower can keep updating the image as usual; it picks up each published
release.

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
`db.py` (new functions only), the web UI and three templates, so merges rarely
conflict.

## Database

`queries.ai_intent` (TEXT) and the `ai_verdicts` table are created at startup
if missing (idempotent). The `version` parameter is **not** changed, so the
built-in update check keeps working. The `items.ai_decision` / `items.ai_reason`
columns added by the first single-model version are no longer used and can stay.

## Cost

Roughly 3–5k input tokens per checked item for the photos (4 photos at 768 px)
plus 1–2k for the text; the Test page and the `*_tokens` columns show the real
figures. Only queries with an AI filter text use tokens, and only for items
that would otherwise be sent.

import db
import html
import os
import time
import requests
import ai_filter
from pyVintedVN import Vinted, requester
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from logger import get_logger

# Get logger for this module
logger = get_logger(__name__)

# Vinted stopped publishing listing times in September 2026, so "is this item new?"
# is answered purely by "have we recorded its id before?". That makes a wiped or
# freshly seeded database the only real flood risk, and this caps the blast radius.
MAX_NOTIFICATIONS_PER_RUN = 10


def notify_seller_rating():
    """NOTIFY_SELLER_RATING (default true): add the seller's rating to every
    message. It comes from the item page, so an item the AI check did not
    already read costs one extra Vinted request."""
    value = (os.environ.get("NOTIFY_SELLER_RATING") or "").strip().lower()
    return value not in ("0", "false", "no", "off")


def process_query(query, name=None, ai_intent=None, ai_vision_intent=None):
    """
    Process a Vinted query URL by:
    1. Checking if the URL is a brand URL and converting it to standard format if needed
    2. Parsing the URL and extracting query parameters
    3. Ensuring the order flag is set to "newest_first"
    4. Removing time and search_id parameters
    5. Rebuilding the query string and URL
    6. Checking if the query already exists in the database
    7. Adding the query to the database if it doesn't exist

    Args:
        query (str): The Vinted query URL
        name (str, optional): A name for the query. If provided, it will be used as the query name.
        ai_intent (str, optional): Plain-language AI filter text for this query.
        ai_vision_intent (str, optional): AI Vision filter instructions (vision model only).

    Returns:
        tuple: (message, is_new_query)
            - message (str): Status message
            - is_new_query (bool): True if query was added, False if it already existed
    """
    # Check if the URL is a brand URL (format: url/brand/id-name)
    parsed_url = urlparse(query)
    path_parts = parsed_url.path.strip("/").split("/")

    if len(path_parts) >= 2 and path_parts[0] == "brand":
        # Extract the brand ID from the format "id-name"
        brand_id_with_name = path_parts[1]
        brand_id = brand_id_with_name.split("-")[0]

        # Create a new URL with the standard format
        new_path = "/catalog"
        new_query_params = {"brand_ids[]": [brand_id]}
        new_query_string = urlencode(new_query_params, doseq=True)

        # Rebuild the URL
        query = urlunparse(
            (parsed_url.scheme, parsed_url.netloc, new_path, "", new_query_string, "")
        )
        logger.info(f"Converted brand URL to standard format: {query}")

        # Parse the URL and extract the query parameters
        parsed_url = urlparse(query)

    query_params = parse_qs(parsed_url.query)

    # Ensure the order flag is set to newest_first
    query_params["order"] = ["newest_first"]
    # Remove time and search_id if provided
    query_params.pop("time", None)
    query_params.pop("search_id", None)
    query_params.pop("disabled_personalization", None)
    query_params.pop("page", None)

    # Rebuild the query string and the entire URL
    new_query = urlencode(query_params, doseq=True)
    processed_query = urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            new_query,
            parsed_url.fragment,
        )
    )

    # Some queries are made with filters only, so we need to check if the search_text is present
    if db.is_query_in_db(processed_query) is True:
        return "Query already exists.", False
    else:
        # add the query to the db
        db.add_query_to_db(processed_query, name, ai_intent=ai_intent,
                           ai_vision_intent=ai_vision_intent)
        return "Query added.", True


def get_formatted_query_list():
    """
    Get a formatted list of all queries in the database.

    Returns:
        str: A formatted string with all queries, numbered
    """
    all_queries = db.get_queries()
    queries_keywords = []
    for query in all_queries:
        parsed_url = urlparse(query[1])
        query_params = parse_qs(parsed_url.query)

        # Get the name or Extract the value of 'search_text'
        query_name = (
            query[3]
            if query[3] is not None
            else query_params.get("search_text", [None])[0]
        )

        if query_name[0] is None:
            # Use query text instead of the whole query object
            queries_keywords.append([query[1]])
        else:
            queries_keywords.append(query_name)

    query_list = ("\n").join(
        [str(i + 1) + ". " + j for i, j in enumerate(queries_keywords)]
    )
    return query_list


def process_remove_query(number):
    """
    Process the removal of a query from the database.

    Args:
        number (str): The number of the query to remove or "all" to remove all queries

    Returns:
        tuple: (message, success)
            - message (str): Status message
            - success (bool): True if query was removed successfully
    """
    if number == "all":
        db.remove_all_queries_from_db()
        return "All queries removed.", True

    # Check if number is a valid digit
    if number.isdigit():
        # Remove the query from the database
        db.remove_query_from_db(number)
        return "Query removed.", True
    else:
        return "Invalid number.", False


def process_update_query(query_id, query, name):
    """
    Process the update of a query in the database.

    Args:
        query_id (int): The ID of the query to update
        query (str): The new Vinted query URL
        name (str, optional): A new name for the query. If provided, it will be used as the query name.

    Returns:
        tuple: (message, success)
            - message (str): Status message
            - success (bool): True if query was updated successfully
    """
    # Parse the URL and extract the query parameters
    parsed_url = urlparse(query)
    query_params = parse_qs(parsed_url.query)

    # Ensure the order flag is set to newest_first
    query_params["order"] = ["newest_first"]
    # Remove time and search_id if provided
    query_params.pop("time", None)
    query_params.pop("search_id", None)
    query_params.pop("disabled_personalization", None)
    query_params.pop("page", None)

    # Rebuild the query string and the entire URL
    new_query = urlencode(query_params, doseq=True)
    processed_query = urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            new_query,
            parsed_url.fragment,
        )
    )

    # Update the query in the database
    if db.update_query_in_db(query_id, processed_query, name):
        return "Query updated.", True
    else:
        return "Failed to update query.", False


def process_add_country(country):
    """
    Process the addition of a country to the allowlist.

    Args:
        country (str): The country code to add

    Returns:
        tuple: (message, country_list)
            - message (str): Status message
            - country_list (list): Current list of allowed countries
    """
    # Format the country code (remove spaces)
    country = country.replace(" ", "")
    country_list = db.get_allowlist()

    # Validate the country code (check if it's 2 characters long)
    if len(country) != 2:
        return "Invalid country code", country_list

    # Check if the country is already in the allowlist
    # If country_list is 0, it means the allowlist is empty
    if country_list != 0 and country.upper() in country_list:
        return f'Country "{country.upper()}" already in allowlist.', country_list

    # Add the country to the allowlist
    db.add_to_allowlist(country.upper())
    return "Country added.", db.get_allowlist()


def process_remove_country(country):
    """
    Process the removal of a country from the allowlist.

    Args:
        country (str): The country code to remove

    Returns:
        tuple: (message, country_list)
            - message (str): Status message
            - country_list (list): Current list of allowed countries
    """
    # Format the country code (remove spaces)
    country = country.replace(" ", "")

    # Validate the country code (check if it's 2 characters long)
    if len(country) != 2:
        return "Invalid country code", db.get_allowlist()

    # Remove the country from the allowlist
    db.remove_from_allowlist(country.upper())
    return "Country removed.", db.get_allowlist()


def get_user_country(profile_id):
    """
    Get the country code for a Vinted user.

    Makes an API request to retrieve the user's country code.
    Handles rate limiting by trying an alternative endpoint.

    Args:
        profile_id (str): The Vinted user's profile ID

    Returns:
        str: The user's country code (2-letter ISO code) or "XX" if it can't be determined
    """
    # Users are shared between all Vinted platforms, so we can use whatever locale we want
    url = f"https://www.vinted.fr/api/v2/users/{profile_id}?localize=false"
    response = requester.get(url)
    # That's a LOT of requests, so if we get a 429 we wait a bit before retrying once
    if response.status_code == 429:
        # In case of rate limit, we're switching the endpoint. This one is slower, but it doesn't RL as soon.
        # We're limiting the items per page to 1 to grab as little data as possible
        url = f"https://www.vinted.fr/api/v2/users/{profile_id}/items?page=1&per_page=1"
        response = requester.get(url)
        try:
            user_country = response.json()["items"][0]["user"]["country_iso_code"]
        except KeyError:
            logger.warning(
                "Couldn't get the country due to too many requests. Returning default value."
            )
            user_country = "XX"
    else:
        user_country = response.json()["user"]["country_iso_code"]
    return user_country


def process_items(queue):
    """
    Process all queries from the database, search for items, and put them in the queue.
    Uses the global items_queue by default, but can accept a custom queue for backward compatibility.

    Args:
        queue (Queue, optional): The queue to put the items in. Defaults to the global items_queue.

    Returns:
        None
    """

    all_queries = db.get_queries()

    # Initialize Vinted
    vinted = Vinted()

    # Get the number of items per query from the database
    items_per_query = int(db.get_parameter("items_per_query"))

    # for each keyword we parse data
    for query in all_queries:
        all_items = vinted.items.search(query[1], nbr_items=items_per_query)
        # Filter to only include new items. This should reduce the amount of db calls.
        data = [item for item in all_items if item.is_new_item()]
        queue.put((data, query[0]))
        logger.info(f"Scraped {len(data)} items for query: {query[1]}")


def clear_item_queue(items_queue, new_items_queue):
    """
    Process items from the items_queue.
    This function is scheduled to run frequently.
    """
    if not items_queue.empty():
        data, query_id = items_queue.get()
        banwords_str = db.get_parameter("banwords")

        # Read the watermark once, before the loop. It doubles as the "has this query
        # ever produced anything?" flag, and the updates made below would otherwise
        # cut a first-run priming pass short right after the first item.
        last_query_timestamp = db.get_last_timestamp(query_id)
        is_first_run = last_query_timestamp is None
        if is_first_run:
            logger.info(
                f"First run for query {query_id}: recording {len(data)} item(s) "
                f"without notifying, so the existing catalogue is not replayed."
            )

        to_notify = []
        for item in reversed(data):

            # The watermark is only meaningful when the API actually supplied a
            # listing time. Otherwise raw_timestamp is merely when we saw the item,
            # and comparing it against the watermark would discard every new item.
            if (
                item.has_real_timestamp
                and last_query_timestamp is not None
                and last_query_timestamp >= item.raw_timestamp
            ):
                continue
            # In case of multiple queries, we need to check if the item is already in the db
            if db.is_item_in_db_by_id(item.id) is True:
                # We update the timestamp
                db.update_last_timestamp(query_id, item.raw_timestamp)
                continue
            # If there's an allowlist and
            # If the user's country is not in the allowlist, we just update the timestamp
            if db.get_allowlist() != 0 and (
                get_user_country(item.raw_data["user"]["id"])
            ) not in (db.get_allowlist() + ["XX"]):
                db.update_last_timestamp(query_id, item.raw_timestamp)
                continue
            # Check if the item title contains any banwords
            if banwords_str and contains_banwords(item.title, banwords_str):
                # If it contains banwords, just update the timestamp and skip
                db.update_last_timestamp(query_id, item.raw_timestamp)
                continue

            # Being recorded is what stops an item coming back next run, so every
            # item that reaches this point is written to the db whether or not it
            # ends up being announced.
            to_notify.append(item)
            db.add_item_to_db(
                id=item.id,
                timestamp=item.raw_timestamp,
                price=item.price,
                title=item.title,
                photo_url=item.photo,
                query_id=query_id,
                currency=item.currency,
            )

        if is_first_run:
            return

        # data arrives newest first and is walked in reverse, so to_notify runs
        # oldest to newest. Trim from the front: a backlog is worth dropping, the
        # listings that just appeared are not.
        if len(to_notify) > MAX_NOTIFICATIONS_PER_RUN:
            logger.warning(
                f"{len(to_notify)} unseen items for query {query_id}; notifying the "
                f"{MAX_NOTIFICATIONS_PER_RUN} most recent and recording the rest silently."
            )
            to_notify = to_notify[-MAX_NOTIFICATIONS_PER_RUN:]

        # AI filter (fork addition). Runs after first-run priming and the cap, so
        # tokens are only spent on items that would otherwise be announced. Every
        # item here is already recorded in the db, so a rejected one is not judged
        # again on the next run. With only AI Vision filter instructions the
        # vision model adds picture remarks and nothing is filtered.
        ai_intent = ai_vision_intent = ai_mode = None
        if ai_filter.is_enabled():
            ai_intent = db.get_query_ai_intent(query_id)
            ai_vision_intent = db.get_query_ai_vision_intent(query_id)
            ai_mode = ai_filter.check_mode(ai_intent, ai_vision_intent)

        seller_rating = notify_seller_rating()

        for item in to_notify:
            ai_note = None
            listing = None
            if ai_mode:
                evaluation = ai_filter.evaluate_item(item, ai_intent, ai_vision_intent)
                listing = evaluation.listing
                db.save_ai_verdict(item.id, query_id, ai_intent, evaluation.to_row())
                logger.info(
                    f"AI filter [query {query_id}] {evaluation.summary()}"
                    f" | {item.title} | {item.price} {item.currency} | {item.url}"
                )
                if not evaluation.notify:
                    continue
                ai_note = ai_filter.format_note(evaluation)

            # Seller's rating: only on the item page, so read it unless the AI check already did
            seller_note = None
            if seller_rating:
                if listing is None or not listing.fetched:
                    listing = ai_filter.fetch_details(ai_filter.listing_from_item(item))
                seller_note = ai_filter.seller_line(listing)

            # We create the message
            message_template = db.get_parameter("message_template")
            # Telegram parses the message as HTML, so seller-written fields must be
            # escaped: a title with "<" or "&" would otherwise be rejected and lost.
            content = message_template.format(
                title=html.escape(str(item.title or "")),
                price=html.escape(f"{item.price} {item.currency or ''}".strip()),
                brand=html.escape(str(item.brand_title or "")),
                image=None if item.photo is None else html.escape(item.photo),
            )
            extra = [note for note in (seller_note, ai_note) if note]
            if extra:
                content = content.rstrip("\n") + "\n" + "\n".join(extra)
            # add the item to the queue
            new_items_queue.put((content, item.url, "Open Vinted", None, None))
            # new_items_queue.put((content, item.url, "Open Vinted", item.buy_url, "Open buy page"))


def contains_banwords(title, banwords_str):
    """
    Check if a title contains any banwords.

    Args:
        title (str): The title to check
        banwords_str (str): List of banwords separated by 3 pipe character
    Returns:
        bool: True if the title contains any banwords, False otherwise
    """

    # Split the banwords string into a list using pipe as delimiter
    banwords = [
        word.strip().lower() for word in banwords_str.split("|||") if word.strip()
    ]

    # If the list is empty, return False
    if not banwords:
        return False

    # Check if any banword is in the title (case-insensitive)
    title_lower = title.lower()
    for word in banwords:
        if word in title_lower:
            return True

    return False


# Repository whose releases the update check follows (this fork by default)
UPDATE_CHECK_REPO = (
    os.environ.get("UPDATE_CHECK_REPO") or "https://github.com/hzolt/Vinted-Notifications"
).rstrip("/")

# check_version() runs on every web UI page render, so its result is cached and
# the GitHub request has a short timeout: a slow GitHub must not stall the UI.
VERSION_CHECK_TTL = 6 * 60 * 60
VERSION_CHECK_TIMEOUT = 5
_version_cache = {"checked_at": 0.0, "key": None, "result": None}


def _strip_v(version):
    version = (version or "").strip()
    return version[1:] if version[:1] in ("v", "V") and version[1:2].isdigit() else version


def get_app_version():
    """Version of the running image: the release tag it was built from
    (APP_VERSION, set at build time), else the database schema version."""
    return _strip_v(os.environ.get("APP_VERSION") or db.get_parameter("version") or "dev")


def _is_release_version(version):
    return version[:1].isdigit()


def check_version():
    """
    Check if the application is up to date.

    Returns (is_up_to_date, current_version, latest_version, github_url).
    The running version comes from get_app_version() and is compared with the
    latest published release of UPDATE_CHECK_REPO. Test builds from main
    ("main-<commit>") and local builds ("dev") never report an update. The
    result is cached for VERSION_CHECK_TTL seconds; failures are cached too so
    an unreachable GitHub is not retried on every page load.
    """
    github_url = UPDATE_CHECK_REPO
    ver = None
    try:
        ver = get_app_version()
        key = (github_url, ver)
        now = time.monotonic()
        if (
            _version_cache["result"] is not None
            and _version_cache["key"] == key
            and now - _version_cache["checked_at"] < VERSION_CHECK_TTL
        ):
            return _version_cache["result"]

        result = (True, ver, ver, github_url)  # if we can't check, assume up to date
        try:
            # /releases/latest redirects to /releases/tag/<tag>, or to /releases
            # when nothing has been published yet
            response = requests.get(
                f"{github_url}/releases/latest", timeout=VERSION_CHECK_TIMEOUT
            )
            if response.status_code == 200 and "/releases/tag/" in response.url:
                latest_version = _strip_v(response.url.rstrip("/").split("/")[-1])
                is_up_to_date = (
                    not _is_release_version(ver) or ver == latest_version
                )
                result = (is_up_to_date, ver, latest_version, github_url)
        except requests.RequestException as e:
            logger.warning(f"Could not check for a new version: {e}")

        _version_cache.update(checked_at=now, key=key, result=result)
        return result
    except Exception as e:
        logger.error(f"Error checking for new version: {str(e)}", exc_info=True)
        return True, ver, ver, github_url

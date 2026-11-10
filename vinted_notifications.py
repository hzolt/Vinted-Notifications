import multiprocessing
import time
import os
import db
from apscheduler.schedulers.background import BackgroundScheduler
from logger import get_logger

# Get logger for this module
logger = get_logger(__name__)

# Starting sequence
# Db check
if not os.path.exists("./data/vinted_notifications.db"):
    logger.info("Database not found, creating a new one.")
    # Create the folder if it doesn't exist
    os.makedirs("./data", exist_ok=True)
    db.create_or_update_sqlite_db("initial_db.sql")
    logger.info("Database created successfully")

import core
from rss_feed_plugin.rss_feed import rss_feed_process
from web_ui_plugin.web_ui import web_ui_process


# Global process references
telegram_process = None
rss_process = None
scrape_process = None
item_extractor_process = None
dispatcher_process = None
current_query_refresh_delay = None

# Pause after an unexpected error in a worker loop, so a fault that repeats on
# every pass (a broken message template, a Vinted API change) fills the log at
# a readable pace instead of spinning on it.
WORKER_ERROR_BACKOFF_SECONDS = 2


def scraper_process(items_queue):
    logger.info("Scrape process started")

    # Get the query refresh delay from the database
    current_query_refresh_delay = int(db.get_parameter("query_refresh_delay"))
    logger.info(f"Using query refresh delay of {current_query_refresh_delay} seconds")

    scraper_scheduler = BackgroundScheduler()
    scraper_scheduler.add_job(
        core.process_items,
        "interval",
        seconds=current_query_refresh_delay,
        args=[items_queue],
        name="scraper",
    )
    scraper_scheduler.start()
    try:
        # Keep the process running
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        scraper_scheduler.shutdown()
        logger.info("Scrape process stopped")


def item_extractor(items_queue, new_items_queue):
    logger.info("Item extractor process started")
    try:
        while True:
            try:
                # Check if there's an item in the queue
                core.clear_item_queue(items_queue, new_items_queue)
            except Exception:
                # One bad batch must not end the loop: the scraper would carry on
                # logging "Scraped N items" while nothing is processed or notified.
                # Logging here puts the traceback in logs/vinted.log (the web UI's
                # log page); left uncaught it only reaches the container's stderr.
                # The batch was already taken off the queue, so it is dropped.
                logger.error(
                    "Error while processing a batch of scraped items; skipping it",
                    exc_info=True,
                )
                time.sleep(WORKER_ERROR_BACKOFF_SECONDS)
            time.sleep(0.1)  # Small sleep to prevent high CPU usage
    except (KeyboardInterrupt, SystemExit):
        logger.info("Item extractor process stopped")


def dispatcher_function(input_queue, rss_queue, telegram_queue):
    logger.info("Dispatcher process started")
    try:
        while True:
            try:
                # Get from input queue
                item = input_queue.get()
                # Send to RSS queue
                rss_queue.put(item)
                # Send to Telegram queue
                telegram_queue.put(item)
            except Exception:
                # Skip this item and keep dispatching the next ones
                logger.error(
                    "Error while dispatching a new item; skipping it", exc_info=True
                )
                time.sleep(WORKER_ERROR_BACKOFF_SECONDS)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Dispatcher process stopped")


def telegram_bot_process(queue):
    logger.info("Telegram bot process started")
    import asyncio

    try:
        # Import LeRobot
        from telegram_bot_plugin.telegram_bot import LeRobot

        # The bot will run with app.run_polling() which is already in the module
        asyncio.run(LeRobot(queue))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Telegram bot process stopped")
    except Exception as e:
        logger.error(f"Error in telegram bot process: {e}", exc_info=True)


def check_refresh_delay(items_queue):
    """Check if the query refresh delay has changed and update the scheduler if needed"""
    global scrape_process, current_query_refresh_delay

    # Check if the scheduler is running

    if scrape_process is None or not scrape_process.is_alive():
        return

    # Get the current value from the database
    try:
        value = db.get_parameter("query_refresh_delay")
        if value is None:
            # The read failed (db.py has logged why): keep the current delay
            # and look again on the next pass.
            return
        new_delay = int(value)

        # If the delay has changed, update the scheduler
        if new_delay != current_query_refresh_delay:
            logger.info(
                f"Query refresh delay changed from {current_query_refresh_delay} to {new_delay} seconds"
            )

            # Update the global variable
            current_query_refresh_delay = new_delay

            # Remove the existing job and add a new one with the updated interval
            scrape_process.terminate()
            scrape_process.join()
            scrape_process = multiprocessing.Process(
                target=scraper_process, args=(items_queue,)
            )
            scrape_process.start()

            logger.info(
                f"Scheduler updated with new refresh delay of {new_delay} seconds"
            )
    except Exception as e:
        logger.error(f"Error updating refresh delay: {e}", exc_info=True)


def restart_if_exited(name, process, target, args):
    """
    Return `process`, or a freshly started replacement if it has exited.

    The scraper, item extractor and dispatcher must run for the whole life of
    the app. When one of them is gone the web UI still answers and nothing looks
    wrong, but no item is scraped or notified any more.
    """
    if process is None or process.is_alive():
        return process
    logger.error(
        f"{name} process exited unexpectedly (exit code {process.exitcode}); restarting it"
    )
    process.join()
    replacement = multiprocessing.Process(target=target, args=args)
    replacement.start()
    return replacement


def restart_core_processes(items_queue, new_items_queue, telegram_queue, rss_queue):
    global scrape_process, item_extractor_process, dispatcher_process

    scrape_process = restart_if_exited(
        "Scrape", scrape_process, scraper_process, (items_queue,)
    )
    item_extractor_process = restart_if_exited(
        "Item extractor",
        item_extractor_process,
        item_extractor,
        (items_queue, new_items_queue),
    )
    dispatcher_process = restart_if_exited(
        "Dispatcher",
        dispatcher_process,
        dispatcher_function,
        (new_items_queue, rss_queue, telegram_queue),
    )


def monitor_processes(items_queue, new_items_queue, telegram_queue, rss_queue):
    global telegram_process, rss_process

    # Restart the scraper, item extractor or dispatcher if one has died
    restart_core_processes(items_queue, new_items_queue, telegram_queue, rss_queue)

    # Check if the query refresh delay has changed
    check_refresh_delay(items_queue)

    ### TELEGRAM ###
    telegram_is_running = telegram_process is not None and telegram_process.is_alive()
    # Check telegram process status
    telegram_flag = db.get_parameter("telegram_process_running")
    telegram_should_run = telegram_flag == "True"
    # Check if the telegram token and chat ID are set
    telegram_token = db.get_parameter("telegram_token")
    telegram_chat_id = db.get_parameter("telegram_chat_id")
    if not telegram_token or not telegram_chat_id:
        telegram_should_run = False
    if None in (telegram_flag, telegram_token, telegram_chat_id):
        # A read failed (db.py has logged why). That says nothing about whether
        # the bot should run, so leave it as it is until the next pass.
        telegram_should_run = telegram_is_running

    if telegram_should_run and not telegram_is_running:
        # Start telegram process
        logger.info("Starting telegram bot process.")
        telegram_process = multiprocessing.Process(
            target=telegram_bot_process, args=(telegram_queue,)
        )
        telegram_process.start()
    elif not telegram_should_run and telegram_is_running:
        # Stop telegram process
        logger.info("Stopping telegram bot process.")
        telegram_process.terminate()
        telegram_process.join()
        telegram_process = None

    ### RSS ###
    # Check RSS process status
    rss_is_running = rss_process is not None and rss_process.is_alive()
    rss_flag = db.get_parameter("rss_process_running")
    # None: the read failed (db.py has logged why), so leave RSS as it is
    rss_should_run = rss_is_running if rss_flag is None else rss_flag == "True"

    if rss_should_run and not rss_is_running:
        # Start RSS process
        logger.info("Starting RSS process based on database status")
        rss_process = multiprocessing.Process(
            target=rss_feed_process, args=(rss_queue,)
        )
        rss_process.start()
    elif not rss_should_run and rss_is_running:
        # Stop RSS process
        logger.info("Stopping RSS process based on database status")
        rss_process.terminate()
        rss_process.join()
        rss_process = None


def plugin_checker():
    # Get telegram and rss enable status
    telegram_enabled = db.get_parameter("telegram_enabled")
    logger.info("Telegram enabled: {}".format(telegram_enabled))
    rss_enabled = db.get_parameter("rss_enabled")
    logger.info("RSS enabled: {}".format(rss_enabled))

    # Reset process status at startup
    db.set_parameter("telegram_process_running", telegram_enabled)
    db.set_parameter("rss_process_running", rss_enabled)


if __name__ == "__main__":

    # Run db migrations
    current_version = db.get_parameter("version")
    # Check if there is a file that starts with the current version in the migrations folder. We keep comparing until
    # we find no migration files that start with the current version.
    migration_files = [f for f in os.listdir("migrations")]
    while True:
        migration_file = next(
            (f for f in migration_files if f.startswith(current_version)), None
        )
        if migration_file:
            logger.info(f"Running migration: {migration_file}")
            db.create_or_update_sqlite_db("./migrations/" + migration_file)
            # Increment the version
            current_version = db.get_parameter("version")
        else:
            break

    # AI filter column (fork addition, idempotent)
    if db.ensure_ai_schema():
        logger.info("Added AI filter columns to the database")
    if db.ensure_indexes():
        logger.info("Added an index on items to the database")

    # Plugin checker
    plugin_checker()

    # Create a shared queue
    items_queue = multiprocessing.Queue()
    new_items_queue = multiprocessing.Queue()
    rss_queue = multiprocessing.Queue()
    telegram_queue = multiprocessing.Queue()

    # 1. Create and start the scrape process
    # This process will scrape items and put them in the items_queue
    current_query_refresh_delay = int(db.get_parameter("query_refresh_delay"))
    scrape_process = multiprocessing.Process(
        target=scraper_process, args=(items_queue,)
    )
    scrape_process.start()

    # 2. Create the item extractor process
    # This process will extract items from the items_queue and put them in the new_items_queue
    item_extractor_process = multiprocessing.Process(
        target=item_extractor, args=(items_queue, new_items_queue)
    )
    item_extractor_process.start()

    # 3. Create the dispatcher process
    # This process will handle the new items and send them to the enabled services
    dispatcher_process = multiprocessing.Process(
        target=dispatcher_function,
        args=(
            new_items_queue,
            rss_queue,
            telegram_queue,
        ),
    )
    dispatcher_process.start()

    # 4. Set up a scheduler to monitor processes
    # This will check the process status in the database and start/stop processes as needed,
    # and restart the scrape, item extractor and dispatcher processes if one has died
    monitor_scheduler = BackgroundScheduler()
    monitor_scheduler.add_job(
        monitor_processes,
        "interval",
        seconds=5,
        args=[items_queue, new_items_queue, telegram_queue, rss_queue],
        name="process_monitor",
    )
    monitor_scheduler.start()

    # 5. Create and start the Web UI process
    # This process will provide a web interface to control the application
    web_ui_process_instance = multiprocessing.Process(target=web_ui_process)
    web_ui_process_instance.start()

    try:
        # Wait for processes to finish (which they won't unless interrupted)
        scrape_process.join()
        item_extractor_process.join()
        dispatcher_process.join()
        web_ui_process_instance.join()

        # plugins
        if telegram_process:
            telegram_process.join()
        if rss_process:
            rss_process.join()
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        logger.info("Main process interrupted")

        # Shutdown the monitor scheduler
        monitor_scheduler.shutdown()

        # Terminate all processes
        scrape_process.terminate()
        item_extractor_process.terminate()
        dispatcher_process.terminate()
        # Terminate web UI process
        web_ui_process_instance.terminate()

        # Plugins

        if telegram_process and telegram_process.is_alive():
            telegram_process.terminate()
            # Set the process status in the database
            db.set_parameter("telegram_process_running", "False")
        if rss_process and rss_process.is_alive():
            rss_process.terminate()
            # Set the process status in the database
            db.set_parameter("rss_process_running", "False")

        # Wait for all processes to terminate
        scrape_process.join()
        item_extractor_process.join()
        dispatcher_process.join()
        web_ui_process_instance.join()

        # Plugins
        if telegram_process:
            telegram_process.join()
        if rss_process:
            rss_process.join()

        logger.info("All processes terminated")

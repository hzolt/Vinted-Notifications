from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, filters
from telegram.error import BadRequest, NetworkError, RetryAfter
import db
import core
import asyncio
from logger import get_logger

# Get logger for this module
logger = get_logger(__name__)

# The jobs are scheduled before run_polling() starts the job queue, and
# APScheduler skips a job that starts more than 1 s late (its default
# misfire_grace_time). A slow first contact with Telegram was enough to skip
# the queue reader for good: the bot kept answering commands, but every
# notification stayed in the queue. None = run however late.
RUN_EVEN_IF_LATE = {"misfire_grace_time": None}

# Attempts per message for network errors and Telegram flood control
SEND_ATTEMPTS = 4


async def hello(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        ver = core.get_app_version()
        await update.message.reply_text(
            f"Hello {update.effective_user.first_name}! Vinted-Notifications is running under version {ver}.\n"
        )
    except Exception as e:
        logger.error(f"Error in hello command: {str(e)}", exc_info=True)
        try:
            await update.message.reply_text(
                "An error occurred. Please try again later."
            )
        except Exception as e2:
            logger.error(f"Error sending error message: {str(e2)}")


def allowed_chat_filter():
    """Filter that only lets through updates from the configured telegram_chat_id.

    Without it anyone who finds the bot could list, add or delete queries.
    The chat id is a number (negative for groups) or a public @username.
    """
    chat_id = str(db.get_parameter("telegram_chat_id") or "").strip()
    if chat_id.lstrip("-").isdigit():
        return filters.Chat(chat_id=int(chat_id))
    if chat_id:
        return filters.Chat(username=chat_id.lstrip("@"))
    return None


async def ignore_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log commands from other chats without answering them."""
    chat = update.effective_chat
    user = update.effective_user
    logger.warning(
        f"Ignored Telegram command from unauthorized chat {chat.id if chat else '?'}"
        f" (user id {user.id if user else '?'})"
    )


class LeRobot:
    def __init__(self, queue):
        from telegram.ext import ApplicationBuilder, CommandHandler

        try:

            self.app = (
                ApplicationBuilder().token(db.get_parameter("telegram_token")).build()
            )
            # The application's own bot: run_polling() opens its connection once
            # and keeps it, so a message is one request and one send cannot
            # close the connection under another
            self.bot = self.app.bot

            # Create the item queue to send to telegram
            self.new_items_queue = queue

            # Commands are only accepted from the configured chat
            allowed = allowed_chat_filter()
            if allowed is None:
                logger.error("telegram_chat_id is not set: Telegram commands are disabled")
            else:
                commands = {
                    # Verify if bot is running
                    "hello": hello,
                    # Keyword handlers
                    "add_query": self.add_query,
                    "remove_query": self.remove_query,
                    "queries": self.queries,
                    # Allowlist handlers
                    "clear_allowlist": self.clear_allowlist,
                    "add_country": self.add_country,
                    "remove_country": self.remove_country,
                    "allowlist": self.allowlist,
                }
                for command, callback in commands.items():
                    self.app.add_handler(
                        CommandHandler(command, callback, filters=allowed)
                    )
            # Anything else that looks like a command is logged and not answered
            self.app.add_handler(MessageHandler(filters.COMMAND, ignore_unauthorized))

            # TODO : Help command

            # TODO : Manage removals after current items have been processed.

            job_queue = self.app.job_queue
            # Set the commands
            job_queue.run_once(self.set_commands, when=1, job_kwargs=RUN_EVEN_IF_LATE)
            # Every day we check for a new version
            job_queue.run_repeating(self.check_version, interval=86400, first=1,
                                    job_kwargs=RUN_EVEN_IF_LATE)
            # Send the notifications from the queue (runs for the life of the bot)
            job_queue.run_once(self.check_telegram_queue, when=1, job_kwargs=RUN_EVEN_IF_LATE)

            self.app.run_polling()
        except Exception as e:
            logger.error(f"Error initializing bot: {str(e)}", exc_info=True)

    ### QUERIES ###

    # Add a query to the db
    async def add_query(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        try:
            query = context.args
            if not query:
                await update.message.reply_text("No query provided.")
                return

            # Split the message into name=query if it contains an equal sign before the url. Store them separately
            if "=http" in query[0]:
                name, url = query[0].split("=", 1)
            else:
                name = None
                url = query[0]
            # Process the query using the core function
            message, is_new_query = core.process_query(url, name)

            if is_new_query:
                # Create a string with all the keywords
                query_list = core.get_formatted_query_list()
                await update.message.reply_text(
                    f"{message} \nCurrent queries: \n{query_list}"
                )
            else:
                await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error adding query: {str(e)}", exc_info=True)
            try:
                await update.message.reply_text(
                    "An error occurred while adding the query. Please try again later."
                )
            except Exception as e2:
                logger.error(f"Error sending error message: {str(e2)}")

    # Remove a query from the db
    async def remove_query(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        try:
            number = context.args
            if not number:
                await update.message.reply_text("No number provided.")
                return

            # Process the removal using the core function
            if number[0] != "all":
                number[0] = db.get_query_id_by_rowid(number[0])
            message, success = core.process_remove_query(str(number[0]))

            if success:
                if number[0] == "all":
                    await update.message.reply_text(message)
                else:
                    # Get the updated list of queries
                    query_list = core.get_formatted_query_list()
                    await update.message.reply_text(
                        f"{message} \nCurrent queries: \n{query_list}"
                    )
            else:
                await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error removing query: {str(e)}", exc_info=True)
            try:
                await update.message.reply_text(
                    "An error occurred while removing the query. Please try again later."
                )
            except Exception as e2:
                logger.error(f"Error sending error message: {str(e2)}")

    # get all queries from the db
    async def queries(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            query_list = core.get_formatted_query_list()
            await update.message.reply_text(f"Current queries: \n{query_list}")
        except Exception as e:
            logger.error(f"Error retrieving queries: {str(e)}", exc_info=True)
            try:
                await update.message.reply_text(
                    "An error occurred while retrieving the queries. Please try again later."
                )
            except Exception as e2:
                logger.error(f"Error sending error message: {str(e2)}")

    ### ALLOWLIST ###

    async def clear_allowlist(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        try:
            db.clear_allowlist()
            await update.message.reply_text(
                "Allowlist cleared. All countries are allowed."
            )
        except Exception as e:
            logger.error(f"Error clearing allowlist: {str(e)}", exc_info=True)
            try:
                await update.message.reply_text(
                    "An error occurred while clearing the allowlist. Please try again later."
                )
            except Exception as e2:
                logger.error(f"Error sending error message: {str(e2)}")

    async def add_country(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        try:
            country = context.args
            if not country:
                await update.message.reply_text("No country provided")
                return

            # Process the country using the core function
            message, country_list = core.process_add_country(" ".join(country))

            await update.message.reply_text(
                f"{message} Current allowlist: {country_list}"
            )
        except Exception as e:
            logger.error(f"Error adding country to allowlist: {str(e)}", exc_info=True)
            try:
                await update.message.reply_text(
                    "An error occurred while adding the country to the allowlist. Please try again later."
                )
            except Exception as e2:
                logger.error(f"Error sending error message: {str(e2)}")

    async def remove_country(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        try:
            country = context.args
            if not country:
                await update.message.reply_text("No country provided")
                return

            # Process the country using the core function
            message, country_list = core.process_remove_country(" ".join(country))

            await update.message.reply_text(
                f"{message} Current allowlist: {country_list}"
            )
        except Exception as e:
            logger.error(
                f"Error removing country from allowlist: {str(e)}", exc_info=True
            )
            try:
                await update.message.reply_text(
                    "An error occurred while removing the country from the allowlist. Please try again later."
                )
            except Exception as e2:
                logger.error(f"Error sending error message: {str(e2)}")

    async def allowlist(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        try:
            if db.get_allowlist() == 0:
                await update.message.reply_text(
                    "No allowlist set. All countries are allowed."
                )
            else:
                await update.message.reply_text(
                    f"Current allowlist: {db.get_allowlist()}"
                )
        except Exception as e:
            logger.error(f"Error retrieving allowlist: {str(e)}", exc_info=True)
            try:
                await update.message.reply_text(
                    "An error occurred while retrieving the allowlist. Please try again later."
                )
            except Exception as e2:
                logger.error(f"Error sending error message: {str(e2)}")

    ### TELEGRAM SPECIFIC FUNCTIONS ###

    async def send_new_post(self, content, url, text, buy_url=None, buy_text=None):
        """Send one message. Returns True once Telegram accepted it.

        Network errors and timeouts are retried (a timed-out message may have
        arrived after all, so it can come twice); a rejected message is not."""
        first_line = (content or "").strip().split("\n", 1)[0][:80]
        for attempt in range(1, SEND_ATTEMPTS + 1):
            last = attempt == SEND_ATTEMPTS
            try:
                chat_ID = str(db.get_parameter("telegram_chat_id"))
                buttons = [[InlineKeyboardButton(text=text, url=url)]]
                if buy_url and buy_text:
                    buttons.append([InlineKeyboardButton(text=buy_text, url=buy_url)])
                await self.bot.send_message(
                    chat_ID,
                    content,
                    parse_mode="HTML",
                    read_timeout=40,
                    write_timeout=40,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                logger.info(f"Telegram: sent {first_line} | {url}")
                return True
            except RetryAfter as e:
                if last:
                    logger.error(f"Telegram: not sent, flood control: {first_line} | {url}")
                    return False
                retry_after = e.retry_after  # seconds, or a timedelta in newer versions
                if hasattr(retry_after, "total_seconds"):
                    retry_after = retry_after.total_seconds()
                wait = float(retry_after) + 2
                logger.warning(f"Telegram flood control: retrying in {wait:.0f} s")
                await asyncio.sleep(wait)
            except BadRequest as e:  # a NetworkError subclass, but retrying won't help
                logger.error(f"Telegram rejected the message: {e} | {first_line} | {url}")
                return False
            except NetworkError as e:  # includes TimedOut
                if last:
                    logger.error(f"Telegram: not sent after {attempt} attempts ({e}): {first_line} | {url}")
                    return False
                logger.warning(f"Telegram: {e.__class__.__name__}: {e}, retrying ({attempt}/{SEND_ATTEMPTS - 1})")
                await asyncio.sleep(5 * attempt)
            except Exception as e:
                logger.error(f"Error sending new post: {str(e)} | {first_line} | {url}", exc_info=True)
                return False
        return False

    async def check_version(self, context: ContextTypes.DEFAULT_TYPE):
        try:
            # get latest version from the repository
            should_update, VER, latest_version, url = core.check_version()

            if not should_update:
                await self.send_new_post(
                    f"Version {latest_version} is now available. Please update the bot.",
                    url,
                    "Open Github",
                )
        except Exception as e:
            logger.error(f"Error checking for new version: {str(e)}", exc_info=True)

    async def check_telegram_queue(self, context: ContextTypes.DEFAULT_TYPE):
        logger.info("Telegram: sending notifications from the queue")
        while True:
            # One bad entry must not stop every later notification
            try:
                if not self.new_items_queue.empty():
                    content, url, text, buy_url, buy_text = self.new_items_queue.get()
                    await self.send_new_post(content, url, text, buy_url, buy_text)
                else:
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error checking telegram queue: {str(e)}", exc_info=True)
                await asyncio.sleep(1)

    async def set_commands(self, context: ContextTypes.DEFAULT_TYPE):
        try:
            await self.bot.set_my_commands(
                [
                    ("hello", "Verify if bot is running"),
                    ("add_query", "Add a keyword to the bot"),
                    ("remove_query", "Remove a keyword from the bot"),
                    ("queries", "List all queries"),
                    ("clear_allowlist", "Clear the allowlist"),
                    ("add_country", "Add a country to the allowlist"),
                    ("remove_country", "Remove a country from the allowlist"),
                    ("allowlist", "List all countries in the allowlist"),
                ]
            )
            logger.info("Bot commands set successfully")
        except Exception as e:
            logger.error(f"Error setting bot commands: {str(e)}", exc_info=True)

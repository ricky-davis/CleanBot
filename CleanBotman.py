# github.com/hitem

import random
import logging
import pytz
import asyncio
import json
from datetime import datetime, timedelta
from discord.ext import commands, tasks
import discord
import os
import re
from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s]: %(message)s')
logger = logging.getLogger()

# Redirect all discord.* logs through our root handler
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.client").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)
logging.getLogger("discord.ext.commands").setLevel(logging.ERROR)

# Define intents
intents = discord.Intents.default()
intents.message_content = True

# Retrieve bot token from environment variable
TOKEN = os.environ.get('DISCORD_BOT_TOKEN')

# Define CET timezone
CET = pytz.timezone('Europe/Stockholm')

# File to store cleaner state
STATE_FILE = 'cleaner_state.json'  # Update this path as needed

# List of roles allowed to execute commands
MODERATOR_ROLES = {"Admins", "Super Friends"}  # Add role names as needed

# Define cleaning interval and cooldowns
CLEANING_INTERVAL_MINUTES = 15
DEFAULT_COOLDOWN_SECONDS = 10
HELP_COOLDOWN_SECONDS = 30

# Load initial state


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Error loading state file: {e}")
            return {}
    else:
        return {}


state = load_state()

# Initialize bot with intents
bot = commands.Bot(command_prefix='!', intents=intents)

# Dictionary to store cleaning tasks for each channel
cleaning_tasks = {}

DURATION_RE = re.compile(
    r"^last(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?$",
    re.IGNORECASE
)

def parse_last_duration(s: str) -> timedelta | None:
    m = DURATION_RE.match(s.strip())
    if not m:
        return None
    days = int(m.group('days') or 0)
    hours = int(m.group('hours') or 0)
    minutes = int(m.group('minutes') or 0)
    delta = timedelta(days=days, hours=hours, minutes=minutes)
    return delta if delta.total_seconds() > 0 else None

@bot.event
async def on_ready():
    logger.info("#############################################################")
    logger.info("# Created by hitem       #github.com/hitem      CleanerBot  #")
    logger.info("#############################################################")
    logger.info(f'Logged in as {bot.user.name}')
    for channel_id in state.keys():
        if channel_id not in cleaning_tasks:
            cleaning_tasks[channel_id] = tasks.loop(
                minutes=CLEANING_INTERVAL_MINUTES)(clean_old_messages)
        try:
            cleaning_tasks[channel_id].start(channel_id)
            logger.info(f"Started cleaner task for channel ID: {channel_id}")
        except RuntimeError:
            logger.warning(
                f"Task for channel ID: {channel_id} is already running")
    logger.info("Bot is ready to receive commands")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        logger.warning(f"Command {ctx.message.content} not found.")
    elif isinstance(error, commands.CommandOnCooldown):
        pass
    else:
        logger.error(f"An error occurred: {error}")


async def clean_old_messages(channel_id):
    config = state.get(str(channel_id))
    if not config:
        logger.warning(f"No configuration found for channel ID: {channel_id}")
        return

    # Find the guild and channel explicitly
    channel = None
    for guild in bot.guilds:
        for ch in guild.text_channels:
            if ch.id == int(channel_id):
                channel = ch
                break
        if channel:
            break

    if not channel:
        logger.warning(f"Channel not found: {channel_id}")
        return

    now = datetime.now(CET)  # Use timezone-aware datetime
    time_limit = now - timedelta(hours=config['time_to_keep'])

    deleted_count = await delete_messages(channel, time_limit)

    if deleted_count > 0:
        logger.info(
            f"Cleaned {deleted_count} messages in channel {channel_id}")
    else:
        logger.debug(f"No messages to clean in channel {channel_id}")


def has_moderator_role(ctx):
    return any(role.name in MODERATOR_ROLES for role in ctx.author.roles)


@bot.command(name='enablecleaner')
@commands.cooldown(1, DEFAULT_COOLDOWN_SECONDS, commands.BucketType.user)
async def enable_cleaner(ctx, channel_id: int = None):
    if not has_moderator_role(ctx):
        await ctx.send("You do not have the required permissions to use this command.")
        logger.warning(f"{ctx.author} tried to enable cleaner without required permissions")
        return

    # default to the channel where the command was run
    target_channel_id = channel_id or ctx.channel.id

    try:
        # Optional: sanity-check the bot can delete messages in that channel
        target_channel = ctx.guild.get_channel(target_channel_id)
        if target_channel:
            perms = target_channel.permissions_for(ctx.guild.me)
            if not perms.manage_messages:
                await ctx.send("I don’t have **Manage Messages** in that channel, so I won’t be able to clean it.")
                logger.warning(f"Missing Manage Messages in channel {target_channel_id}")
                return

        # Default to 24 hours
        state[str(target_channel_id)] = {'time_to_keep': 24}
        save_state()

        if target_channel_id not in cleaning_tasks:
            cleaning_tasks[target_channel_id] = tasks.loop(
                minutes=CLEANING_INTERVAL_MINUTES)(clean_old_messages)
        try:
            cleaning_tasks[target_channel_id].start(target_channel_id)
        except RuntimeError:
            logger.warning(f"Task for channel ID: {target_channel_id} is already running")

        await ctx.send(f"Cleaner enabled for channel ID: {target_channel_id}")
        logger.info(f"Cleaner enabled for channel ID: {target_channel_id} by {ctx.author}")
    except Exception as e:
        await ctx.send(f"Error enabling cleaner: {e}")
        logger.error(f"Error enabling cleaner for channel ID: {target_channel_id}: {e}")



@enable_cleaner.error
async def enable_cleaner_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        pass
    else:
        logger.error(f"An error occurred in enable_cleaner: {error}")


@bot.command(name='setcleaningtime')
@commands.cooldown(1, DEFAULT_COOLDOWN_SECONDS, commands.BucketType.user)
async def set_cleaning_time(ctx, hours: int):
    if has_moderator_role(ctx):
        channel_id = ctx.channel.id
        if hours not in range(1, 73):  # Allow time from 1 to 72 hours
            await ctx.send("Invalid time. Please set it to a value between 1 and 72 hours.")
            logger.warning(
                f"Invalid cleaning time set by {ctx.author}: {hours} hours")
            return

        if str(channel_id) in state:
            state[str(channel_id)]['time_to_keep'] = hours
            save_state()
            await ctx.send(f"Cleaning time set to {hours} hours for channel ID: {channel_id}")
            logger.info(
                f"Cleaning time set to {hours} hours for channel ID: {channel_id} by {ctx.author}")
        else:
            await ctx.send(f"Cleaner is not enabled for channel ID: {channel_id}")
            logger.warning(
                f"{ctx.author} tried to set cleaning time for a channel that is not enabled: {channel_id}")
    else:
        await ctx.send("You do not have the required permissions to use this command.")
        logger.warning(
            f"{ctx.author} tried to set cleaning time without required permissions")


@set_cleaning_time.error
async def set_cleaning_time_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        pass
    else:
        logger.error(f"An error occurred in set_cleaning_time: {error}")


@bot.command(name='testcleaner')
@commands.cooldown(1, DEFAULT_COOLDOWN_SECONDS, commands.BucketType.user)
async def test_cleaner(ctx, time: str):
    if has_moderator_role(ctx):
        channel_id = ctx.channel.id
        if str(channel_id) not in state:
            await ctx.send("Cleaner is not enabled.")
            logger.warning(
                f"{ctx.author} tried to test cleaner on a channel that is not enabled: {channel_id}")
            return

        channel = ctx.channel
        now = datetime.now(CET)  # timezone-aware

        # handle 'last...' forms (e.g., last5m, last1h25m, last2d)
        delta = parse_last_duration(time)
        if delta:
            start_time = now - delta
            await ctx.send(f"Deleting messages from the last {delta}.")
            logger.info(
                f"Testing cleaner: deleting messages NEWER than {start_time.isoformat()} in channel {channel_id}")
            deleted_count = await delete_messages(channel, older_than=None, newer_than=start_time)
            await ctx.send(f"Test complete. Deleted {deleted_count} messages.")
            logger.info(
                f"Test cleaner completed (last…). Deleted {deleted_count} messages in channel {channel_id}")
            return

        # Existing behavior: 'all'
        if time.lower() == 'all':
            await ctx.send("Deleting all messages in the channel.")
            logger.info(f"Testing cleaner: deleting all messages in channel {channel_id}")
            deleted_count = await delete_messages(channel, older_than=now, newer_than=datetime(1970, 1, 1, tzinfo=CET))
            await ctx.send(f"Test complete. Deleted {deleted_count} messages.")
            logger.info(f"Test cleaner completed. Deleted {deleted_count} messages in channel {channel_id}")
            return

        # Existing behavior: numeric hours => delete older than N hours
        try:
            hours = int(time)
            time_limit = now - timedelta(hours=hours)
            await ctx.send(f"Deleting messages older than {hours} hours.")
            logger.info(
                f"Testing cleaner: deleting messages older than {hours} hours in channel {channel_id}")
            deleted_count = await delete_messages(channel, older_than=time_limit)
            await ctx.send(f"Test complete. Deleted {deleted_count} messages.")
            logger.info(
                f"Test cleaner completed. Deleted {deleted_count} messages in channel {channel_id}")
        except ValueError:
            await ctx.send("Invalid time. Use 'all', a number of hours (e.g., `12`), or `last<Nd><Nh><Nm>` like `last35m`, `last1h25m`, `last2d`.")
            logger.error(f"Invalid time specified by {ctx.author} for testcleaner: {time}")
    else:
        await ctx.send("You do not have the required permissions to use this command.")
        logger.warning(f"{ctx.author} tried to test cleaner without required permissions")


@test_cleaner.error
async def test_cleaner_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        pass
    else:
        logger.error(f"An error occurred in test_cleaner: {error}")


@bot.command(name='cleanersetting')
@commands.cooldown(1, DEFAULT_COOLDOWN_SECONDS, commands.BucketType.user)
async def cleaner_setting(ctx):
    channel_id = str(ctx.channel.id)
    if channel_id in state:
        time_to_keep = state[channel_id]['time_to_keep']
        await ctx.send(f"Cleaner is enabled for this channel. Cleaning time is set to {time_to_keep} hours.")
        logger.info(
            f"{ctx.author} checked cleaner setting for channel ID: {channel_id} - enabled with {time_to_keep} hours")
    else:
        await ctx.send("Cleaner is not enabled for this channel.")
        logger.info(
            f"{ctx.author} checked cleaner setting for channel ID: {channel_id} - not enabled")


@cleaner_setting.error
async def cleaner_setting_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        pass
    else:
        logger.error(f"An error occurred in cleaner_setting: {error}")


@bot.command(name='checkpermissions')
@commands.cooldown(1, DEFAULT_COOLDOWN_SECONDS, commands.BucketType.user)
async def check_permissions(ctx):
    permissions = ctx.author.guild_permissions
    await ctx.send(f"Your permissions: {permissions}")
    logger.info(f"{ctx.author} checked their permissions")


@bot.command(name='listchannels')
@commands.cooldown(1, DEFAULT_COOLDOWN_SECONDS, commands.BucketType.user)
async def list_channels(ctx):
    if has_moderator_role(ctx):
        guild = ctx.guild
        channels_info = ""
        for channel in guild.text_channels:
            channels_info += f"Channel: {channel.name} (ID: {channel.id})\n"
        await ctx.send(f"Channels in this guild:\n{channels_info}")
        logger.info(f"{ctx.author} listed channels in guild {guild.id}")
    else:
        await ctx.send("You do not have the required permissions to use this command.")
        logger.warning(
            f"{ctx.author} tried to list channels without required permissions")


@bot.command(name='cleanerhelp')
@commands.cooldown(1, HELP_COOLDOWN_SECONDS, commands.BucketType.user)
async def cleaner_help(ctx):
    header = "**Cleaner Bot Commands**\n\n"
    footer = "Feel free to ask for help if you need more information."

    help_text = (
        "- `!enablecleaner [CHANNEL_ID]` - Enable the cleaner. If CHANNEL_ID is omitted, it enables in the current channel. Default interval: 24h.\n"
        "- `!setcleaningtime HOURS` - Set the cleaning interval for the current channel. HOURS must be between 1 and 72.\n"
        "- `!testcleaner TIME` - Test run. TIME can be 'all', a number of hours (e.g., `12`), or `last<Nd><Nh><Nm>` like `last35m`, `last1h25m`, `last2d`.\n"
        "- `!cleanersetting` - Check if the cleaner is enabled for the current channel and the cleaning interval.\n"
        "- `!checkpermissions` - Check your permissions id.\n"
        "- `!listchannels` - List all channels + channel_id.\n"
        "- `!cleanerhelp` - List all cleaner commands.\n\n"
    )

    embed = discord.Embed(
        title="Cleaner Bot Help",
        description=header + help_text + footer,
        colour=0x00FF00  # You can choose any color you like
    )

    embed = await attach_embed_info(ctx, embed)

    await ctx.send(embed=embed)
    logger.info(f"{ctx.author} used cleanerhelp command")


async def attach_embed_info(ctx=None, embed=None):
    embed.set_author(name="Cleaner Bot", icon_url=f"{ctx.guild.icon.url}")
    embed.set_thumbnail(url=f"{ctx.guild.icon.url}")
    embed.set_footer(text="by: hitem")
    return embed


@cleaner_help.error
async def cleaner_help_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        pass
    else:
        logger.error(f"An error occurred in cleaner_help: {error}")


async def delete_messages(channel, older_than: datetime | None, newer_than: datetime | None = None):
    deleted_count = 0
    messages_to_delete = []
    before_message = None

    while True:
        try:
            page = []
            async for msg in channel.history(limit=100, before=before_message):
                page.append(msg)
        except discord.errors.DiscordServerError as e:
            logger.warning(f"500 fetching history, retrying… ({e})")
            await asyncio.sleep(2 + random.random() * 3)
            continue

        if not page:
            break

        for msg in page:
            cond_old = (older_than is not None and msg.created_at < older_than)
            cond_new = (newer_than is not None and msg.created_at >= newer_than)
            if older_than is None and newer_than is None:
                # nothing to delete if no condition given
                continue
            if cond_old or cond_new:
                messages_to_delete.append(msg)

        before_message = page[-1]

    for msg in messages_to_delete:
        try:
            await msg.delete()
            deleted_count += 1
        except discord.Forbidden:
            logger.error(f"Forbidden deleting message {msg.id}")
        except discord.HTTPException as e:
            logger.error(f"HTTP error deleting message {msg.id}: {e}")
        await asyncio.sleep(1)

    return deleted_count


def save_state():
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
        logger.info("State saved successfully")
    except Exception as e:
        logger.error(f"Error saving state file: {e}")


# Run the bot
bot.run(TOKEN)

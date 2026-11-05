import os
import sys
import time
import asyncio
import traceback
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import config

DB_PATH = "bot_database.db"
KEEP_DIAGNOSTIC_REPORTS_COUNT = 3

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True


def generate_command_directory_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    """Dynamically builds a scannable directory by reading active commands across loaded cogs."""
    total_commands = len([c for c in bot.commands if not c.hidden])

    embed = discord.Embed(
        title="🤖 Project-Edith // Active Command Roster",
        description=(
            f"**Server Scope:** {guild.name}\n"
            "**Accepted Prefixes:** `.` or `;` | **Slash Commands:** `/`\n"
            f"**Total Available Directives:** `{total_commands}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.from_rgb(0, 168, 252),
        timestamp=datetime.now(timezone.utc)
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    cog_branding = {
        "Leveling": ("🎮 Progression & Leveling", "Track XP, voice minutes, and rank milestones"),
        "Bumping": ("🚀 Server Bump Engine", "Bump server ranking and claim +200 XP rewards"),
        "AFK": ("💤 AFK & Away Protocols", "Custom auto-responder away messages and nickname prefixes"),
        "Moderation": ("🛡️ Staff Moderation & Sanctions", "Infraction management, timeouts, kicks, and bans"),
        "Music": ("🎵 AtomMusic & Audio Lounge", "Interactive music controller, streaming playback, and queue"),
        "Announcements": ("📢 Broadcast Studio", "Formal server broadcasts and preview delivery"),
        "Revive": ("⚡ Chat Revive Protocols", "Server activity triggers and engaging discussion prompts"),
        "Welcome": ("🌐 Gateway & Welcome Portal", "Persistent server rules, channel maps, and guide panels"),
        "DiscordMemoryBackup": ("📦 Persistent Storage Vault", "SQLite snapshot persistence to Discord cloud"),
        "AuditLogs": ("📋 Audit & Message Monitoring", "Audit trail for edited and deleted messages")
    }

    for cog_name, cog in sorted(bot.cogs.items()):
        cog_commands = cog.get_commands()
        if not cog_commands:
            continue

        cmd_lines = []
        for cmd in cog_commands:
            if cmd.hidden:
                continue
            desc = cmd.help or cmd.description or "Execute command"
            first_sentence = desc.split("\n")[0].split(". ")[0].strip()
            if not first_sentence.endswith("."):
                first_sentence += "."
            cmd_lines.append(f"`{bot.command_prefix[0] if isinstance(bot.command_prefix, (list, tuple)) else '.'}{cmd.name}` — {first_sentence}")

        if cmd_lines:
            header_title, _ = cog_branding.get(cog_name, (f"📦 {cog_name}", ""))
            embed.add_field(
                name=header_title,
                value="\n".join(cmd_lines[:8]),
                inline=False
            )

    embed.set_footer(
        text="Project-Edith • Live Auto-Updating Directory",
        icon_url=bot.user.display_avatar.url if bot.user else None
    )
    return embed


async def resolve_or_create_commands_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """Finds by ID, finds by fuzzy name match, or automatically provisions a #bot-commands channel."""
    # 1. Check explicit ID from config or environment variable
    channel_id = int(os.getenv("COMMANDS_CHANNEL_ID", getattr(config, "COMMANDS_CHANNEL_ID", 0)))
    if channel_id and channel_id != 0:
        chan = guild.get_channel(channel_id)
        if chan:
            return chan
        try:
            return await guild.fetch_channel(channel_id)
        except Exception:
            pass

    # 2. Fuzzy name matching across existing text channels
    candidates = [
        "bot-commands", "commands", "bot-cmd", "bot-directives", 
        "cmd-list", "bot-spam", "terminal", "directives"
    ]
    for c in guild.text_channels:
        lower_name = c.name.lower()
        if any(term in lower_name for term in candidates):
            return c

    # 3. Fallback: Auto-provision #bot-commands under general or control category
    target_category = None
    for cat in guild.categories:
        if any(keyword in cat.name.upper() for keyword in ["BACKUP", "CONTROL", "BOT", "SYSTEM", "GENERAL"]):
            target_category = cat
            break

    try:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, manage_messages=True)
        }
        channel = await guild.create_text_channel(
            name="bot-commands",
            category=target_category,
            overwrites=overwrites,
            topic="Official bot commands hub. Dynamic command matrix updates here automatically."
        )
        print(f"[AUTO-CHANNEL] Provisioned #{channel.name} in {guild.name}")
        return channel
    except Exception as e:
        print(f"[AUTO-CHANNEL ERROR] Could not auto-create #bot-commands in {guild.name}: {e}")
        return None


async def update_command_channel_panel(bot: commands.Bot, guild: discord.Guild) -> tuple[bool, str]:
    """Finds or creates a permanent command panel in the designated commands channel and edits it in-place."""
    channel = await resolve_or_create_commands_channel(guild)
    if not channel:
        return False, "Could not find or automatically create a commands text channel."

    perms = channel.permissions_for(guild.me)
    if not perms.send_messages or not perms.embed_links:
        return False, f"Missing Send Messages or Embed Links permissions in #{channel.name}."

    embed = generate_command_directory_embed(bot, guild)

    try:
        target_msg = None

        # Check pinned messages first
        try:
            pins = await channel.pins()
            for p in pins:
                if p.author == bot.user and p.embeds:
                    title = p.embeds[0].title or ""
                    if any(term in title for term in ["Active Command Roster", "Command Matrix", "Command Roster"]):
                        target_msg = p
                        break
        except Exception:
            pass

        # If not pinned, check recent history
        if not target_msg:
            async for msg in channel.history(limit=50):
                if msg.author == bot.user and msg.embeds:
                    title = msg.embeds[0].title or ""
                    if any(term in title for term in ["Active Command Roster", "Command Matrix", "Command Roster"]):
                        target_msg = msg
                        break

        if target_msg:
            await target_msg.edit(embed=embed)
            return True, f"Successfully edited existing command panel in #{channel.name} (ID: `{target_msg.id}`)."
        else:
            new_msg = await channel.send(embed=embed)
            if perms.manage_messages:
                try:
                    await new_msg.pin(reason="Persistent Command Roster")
                except Exception:
                    pass
            return True, f"Created and pinned a fresh command panel in #{channel.name} (ID: `{new_msg.id}`)."
    except Exception as e:
        return False, f"Error updating #{channel.name}: {e}"


async def prune_stale_diagnostic_reports(channel: discord.TextChannel, bot_user: discord.ClientUser):
    """Retains strictly the newest 3 startup/restore report embeds and deletes older ones."""
    try:
        startup_messages = []
        async for msg in channel.history(limit=50):
            if msg.author == bot_user and msg.embeds:
                embed_title = msg.embeds[0].title or ""
                if "Startup Self-Test" in embed_title or "Operational Report" in embed_title:
                    startup_messages.append(msg)

        startup_messages.sort(key=lambda m: m.created_at, reverse=True)
        to_delete = startup_messages[KEEP_DIAGNOSTIC_REPORTS_COUNT - 1:]

        for old_msg in to_delete:
            try:
                await old_msg.delete()
                await asyncio.sleep(0.4)
            except discord.NotFound:
                pass
            except Exception as e:
                print(f"[REPORT PRUNE ERROR] {e}")

        if to_delete:
            print(f"[CLEANUP] Pruned {len(to_delete)} older diagnostic startup reports from #{channel.name}")
    except Exception as e:
        print(f"[REPORT CLEANUP ERROR] {e}")


class UtilityBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or(".", ";"),
            intents=intents,
            help_command=None
        )
        self.db: aiosqlite.Connection = None
        self._extensions_loaded = False
        self.boot_start_time = time.time()
        self.cog_load_status = {}
        self.slash_sync_count = 0
        self.restore_report = {
            "success": False,
            "filename": None,
            "timestamp": None,
            "filesize_kb": 0.0,
            "is_fresh": False,
            "error": None
        }

    async def restore_database_from_discord(self):
        backup_id = int(os.getenv("BACKUP_CHANNEL_ID", getattr(config, "BACKUP_CHANNEL_ID", 0)))
        if not backup_id or backup_id == 0:
            print("[RESTORE] BACKUP_CHANNEL_ID is unset. Starting clean local database.")
            self.restore_report["is_fresh"] = True
            return

        try:
            channel = self.get_channel(backup_id) or await self.fetch_channel(backup_id)
            print(f"[RESTORE] Scanning #{channel.name} for persistent backups...")

            async for message in channel.history(limit=50):
                for attachment in message.attachments:
                    if attachment.filename.endswith(".db"):
                        await attachment.save(DB_PATH)
                        size_kb = os.path.getsize(DB_PATH) / 1024
                        self.restore_report = {
                            "success": True,
                            "filename": attachment.filename,
                            "timestamp": message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                            "filesize_kb": round(size_kb, 2),
                            "is_fresh": False,
                            "error": None
                        }
                        print(f"[RESTORE] Restored storage from '{attachment.filename}' ({round(size_kb, 2)} KB)")
                        return
        except Exception as e:
            err_msg = str(e)
            print(f"[RESTORE ERROR] Could not retrieve snapshot: {err_msg}")
            self.restore_report["error"] = err_msg

        print("[RESTORE] No prior backup file found. Initializing clean database.")
        self.restore_report["is_fresh"] = True

    async def setup_hook(self):
        await self.restore_database_from_discord()
        self.db = await aiosqlite.connect(DB_PATH)
        await self.db.execute("PRAGMA journal_mode=WAL;")

        # Schemas
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS birthdays (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                day INTEGER NOT NULL,
                month INTEGER NOT NULL
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS levels (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                messages_count INTEGER DEFAULT 0,
                voice_minutes INTEGER DEFAULT 0
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS bump_status (
                guild_id INTEGER PRIMARY KEY,
                last_bump_timestamp INTEGER DEFAULT 0,
                last_bumper_id INTEGER DEFAULT 0,
                reminder_sent INTEGER DEFAULT 1,
                last_channel_id INTEGER DEFAULT 0,
                pre_reminder_sent INTEGER DEFAULT 1
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS weekly_level_snapshots (
                user_id INTEGER PRIMARY KEY,
                snapshot_xp INTEGER DEFAULT 0,
                snapshot_messages INTEGER DEFAULT 0,
                snapshot_voice INTEGER DEFAULT 0
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS revive_status (
                guild_id INTEGER PRIMARY KEY,
                last_revive_timestamp INTEGER DEFAULT 0,
                last_reviver_id INTEGER DEFAULT 0
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS afk_users (
                user_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                reason TEXT DEFAULT 'AFK',
                timestamp INTEGER NOT NULL,
                original_nickname TEXT
            )
        """)
        await self.db.commit()

        # Dynamic cog loading with duplicate protection
        if not self._extensions_loaded:
            cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
            if os.path.exists(cogs_dir):
                for filename in sorted(os.listdir(cogs_dir)):
                    if filename.endswith(".py") and not filename.startswith("__"):
                        cog_name = f"cogs.{filename[:-3]}"
                        if cog_name not in self.extensions:
                            try:
                                await self.load_extension(cog_name)
                                self.cog_load_status[filename[:-3]] = "OK"
                                print(f"[COG LOADED] {cog_name}")
                            except Exception as e:
                                self.cog_load_status[filename[:-3]] = f"FAILED: {e}"
                                print(f"[COG ERROR] Failed loading {cog_name}: {e}", file=sys.stderr)
            self._extensions_loaded = True

        # Global slash command sync
        try:
            synced = await self.tree.sync()
            self.slash_sync_count = len(synced)
            print(f"[SLASH] Globally synced {len(synced)} slash commands.")
        except Exception as e:
            self.slash_sync_count = -1
            print(f"[SLASH ERROR] Slash command sync failed: {e}", file=sys.stderr)

        self.tree.on_error = self.on_tree_error

    async def on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        err_tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        print(f"[SLASH ERROR in /{interaction.command.name if interaction.command else 'Unknown'}] {err_tb}", file=sys.stderr)

        msg = "⚠️ An error occurred while executing this directive. Staff has been notified."
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)

        if interaction.guild:
            err_chan = await self.get_or_create_error_log_channel(interaction.guild)
            if err_chan:
                embed = discord.Embed(
                    title=f"⚠️ Slash Command Error: `/{interaction.command.name if interaction.command else 'Unknown'}`",
                    description=f"**User:** {interaction.user.mention} (`{interaction.user.id}`)\n**Channel:** {interaction.channel.mention}\n```python\n{err_tb[:1700]}\n```",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc)
                )
                try:
                    await err_chan.send(embed=embed)
                except Exception:
                    pass

    async def get_or_create_error_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        err_id = int(os.getenv("BOT_ERRORS_CHANNEL_ID", getattr(config, "BOT_ERRORS_CHANNEL_ID", 0)))
        if err_id:
            chan = guild.get_channel(err_id)
            if chan:
                return chan

        for chan in guild.text_channels:
            if chan.name in ["bot-errors", "bot-error-logs", "error-logs"]:
                return chan

        backup_cat = discord.utils.get(guild.categories, name="🛠️ BACKUPS & CONTROL")
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)
            }
            director_role = discord.utils.get(guild.roles, name=getattr(config, "DIRECTOR_ROLE_NAME", "Director"))
            if director_role:
                overwrites[director_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)

            return await guild.create_text_channel(
                name="bot-errors",
                category=backup_cat,
                overwrites=overwrites,
                topic="Automated diagnostic logs, runtime errors, and startup telemetry."
            )
        except Exception as e:
            print(f"[CHANNEL PROVISION ERROR] Could not create #bot-errors: {e}")
            return None

    async def run_diagnostic_suite(self, guild: discord.Guild) -> tuple[discord.Embed, bool]:
        tests = []
        overall_healthy = True

        try:
            start_t = time.perf_counter()
            async with self.db.execute("SELECT 1") as cursor:
                row = await cursor.fetchone()
            db_ms = round((time.perf_counter() - start_t) * 1000, 2)
            if row and row[0] == 1:
                tests.append(("Database Engine", f"✅ PASS ({db_ms}ms)"))
            else:
                tests.append(("Database Engine", "❌ FAIL"))
                overall_healthy = False
        except Exception as e:
            tests.append(("Database Engine", f"❌ FAIL ({e})"))
            overall_healthy = False

        backup_id = int(os.getenv("BACKUP_CHANNEL_ID", getattr(config, "BACKUP_CHANNEL_ID", 0)))
        if backup_id:
            b_chan = guild.get_channel(backup_id)
            if b_chan:
                perms = b_chan.permissions_for(guild.me)
                if perms.send_messages and perms.attach_files:
                    tests.append(("Cloud Backup Vault", f"✅ PASS (#{b_chan.name})"))
                else:
                    tests.append(("Cloud Backup Vault", "⚠️ WARN (Missing Permissions)"))
                    overall_healthy = False
            else:
                tests.append(("Cloud Backup Vault", "⚠️ WARN (Channel not accessible)"))
        else:
            tests.append(("Cloud Backup Vault", "⚠️ WARN (BACKUP_CHANNEL_ID unset)"))

        director_name = getattr(config, "DIRECTOR_ROLE_NAME", "Director")
        director_role = discord.utils.get(guild.roles, name=director_name)
        if director_role:
            tests.append(("Authority Hierarchy", f"✅ PASS (@{director_name})"))
        else:
            tests.append(("Authority Hierarchy", f"⚠️ WARN (@{director_name} missing)"))

        failed_cogs = [name for name, status in self.cog_load_status.items() if status != "OK"]
        if not failed_cogs:
            tests.append(("Plugin Cog Suite", f"✅ PASS ({len(self.cog_load_status)} active)"))
        else:
            tests.append(("Plugin Cog Suite", f"❌ FAIL ({', '.join(failed_cogs)})"))
            overall_healthy = False

        boot_duration = round(time.time() - self.boot_start_time, 2)

        embed = discord.Embed(
            title="🛠️ Edith Subsystem Startup Self-Test",
            description=(
                f"**Environment:** Python on Render\n"
                f"**Guild Scope:** {guild.name} (`{guild.id}`)\n"
                f"**Boot Elapsed:** `{boot_duration}s` • **Gateway Latency:** `{round(self.latency * 1000, 1)}ms`"
            ),
            color=discord.Color.green() if overall_healthy else discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )

        for name, result in tests:
            embed.add_field(name=name, value=result, inline=True)

        if self.restore_report["success"]:
            restore_str = f"Restored `{self.restore_report['filename']}` ({self.restore_report['filesize_kb']} KB)"
        elif self.restore_report["is_fresh"]:
            restore_str = "Initialized clean database"
        else:
            restore_str = f"Error: `{self.restore_report.get('error', 'Unknown')}`"

        embed.add_field(name="Storage State", value=restore_str, inline=False)
        embed.set_footer(
            text=f"Project-Edith • Startup Diagnostics | Status: {'ALL SYSTEMS GO' if overall_healthy else 'OPERATING WITH WARNINGS'}"
        )
        return embed, overall_healthy

    async def on_ready(self):
        print(f"[READY] Operational as {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="over server | .help")
        )

        for guild in self.guilds:
            try:
                success, msg = await update_command_channel_panel(self, guild)
                print(f"[AUTO-PANEL in {guild.name}] {msg}")
            except Exception as e:
                print(f"[AUTO-PANEL ERROR in {guild.name}] {e}")

            err_channel = await self.get_or_create_error_log_channel(guild)
            if err_channel:
                try:
                    await prune_stale_diagnostic_reports(err_channel, self.user)
                    report_embed, _ = await self.run_diagnostic_suite(guild)
                    await err_channel.send(embed=report_embed)
                except Exception as e:
                    print(f"[DIAGNOSTICS DISPATCH ERROR] {e}")

    async def on_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandNotFound):
            return

        if isinstance(error, commands.MissingPermissions):
            await ctx.send("⛔ **Access Denied:** You do not have the required permissions.", delete_after=5)
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing required parameter: `{error.param.name}`. Run `.help {ctx.command}` for usage.", delete_after=6)
            return

        err_tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        print(f"[COMMAND ERROR in {ctx.command}] {err_tb}", file=sys.stderr)

        err_chan = await self.get_or_create_error_log_channel(ctx.guild)
        if err_chan:
            embed = discord.Embed(
                title=f"⚠️ Command Failure: `.{ctx.command}`",
                description=f"**User:** {ctx.author.mention} (`{ctx.author.id}`)\n**Channel:** {ctx.channel.mention}\n```python\n{err_tb[:1700]}\n```",
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc)
            )
            try:
                await err_chan.send(embed=embed)
            except Exception:
                pass

        await ctx.send(f"⚠️ An error occurred while executing `.{ctx.command}`. Staff has been notified.", delete_after=6)

    async def close(self):
        if self.db:
            try:
                await self.db.commit()
                await self.db.close()
            except Exception:
                pass
        await super().close()


bot = UtilityBot()


@bot.command(name="updatepanel", aliases=["refreshpanel", "fixpanel"])
@commands.has_permissions(administrator=True)
async def manual_update_panel(ctx: commands.Context):
    """Forces an immediate refresh of the command directory in the bot commands channel."""
    msg = await ctx.send("⏳ Locating commands channel and refreshing panel...")
    success, detail = await update_command_channel_panel(bot, ctx.guild)
    if success:
        await msg.edit(content=f"✅ **Panel Updated:** {detail}")
    else:
        await msg.edit(content=f"❌ **Panel Update Failed:** {detail}")


@bot.command(name="sync")
@commands.has_permissions(administrator=True)
async def instant_sync(ctx: commands.Context):
    """Instantly syncs slash commands directly to the current guild in 1 second and refreshes the command panel."""
    msg = await ctx.send("⏳ Registering slash commands and updating command roster...")
    try:
        bot.tree.copy_global_to(guild=ctx.guild)
        synced = await bot.tree.sync(guild=ctx.guild)

        success, detail = await update_command_channel_panel(bot, ctx.guild)

        status_text = f"✅ **Synced {len(synced)} slash commands!**"
        if success:
            status_text += f"\n📋 **Directory:** {detail}"
        else:
            status_text += f"\n⚠️ **Directory Warning:** {detail}"

        await msg.edit(content=status_text)
    except Exception as e:
        await msg.edit(content=f"❌ Sync failed: `{e}`")


@bot.command(name="list", aliases=["commands", "help"])
async def list_commands(ctx: commands.Context):
    """Generates an on-demand command directory embed in the active channel."""
    embed = generate_command_directory_embed(bot, ctx.guild)
    await ctx.send(embed=embed)


async def main():
    token = getattr(config, "TOKEN", None) or os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError("CRITICAL: DISCORD_TOKEN is missing from config.py and environment variables.")
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("[SHUTDOWN] Bot process terminated cleanly.")

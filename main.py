import os
import sys
import time
import asyncio
import aiosqlite
import discord
from discord.ext import commands
from datetime import datetime, timezone
import config

DB_PATH = "bot_database.db"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True


class UtilityBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=".",
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
                        print(f"[RESTORE] Restored persistent storage from '{attachment.filename}' ({round(size_kb, 2)} KB)")
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
        await self.db.commit()

        # Dynamic loading without double-load bug
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

        try:
            synced = await self.tree.sync()
            self.slash_sync_count = len(synced)
            print(f"[SLASH] Synced {len(synced)} slash commands.")
        except Exception as e:
            self.slash_sync_count = -1
            print(f"[SLASH ERROR] Slash command sync failed: {e}", file=sys.stderr)

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
                tests.append(("Database Read/Write", f"✅ PASS ({db_ms}ms)"))
            else:
                tests.append(("Database Read/Write", "❌ FAIL"))
                overall_healthy = False
        except Exception as e:
            tests.append(("Database Read/Write", f"❌ FAIL ({e})"))
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
                tests.append(("Cloud Backup Vault", "⚠️ WARN (Invalid Channel ID)"))
        else:
            tests.append(("Cloud Backup Vault", "⚠️ WARN (Channel Unset)"))

        director_name = getattr(config, "DIRECTOR_ROLE_NAME", "Director")
        director_role = discord.utils.get(guild.roles, name=director_name)
        if director_role:
            tests.append(("Authority Hierarchy", f"✅ PASS (@{director_name})"))
        else:
            tests.append(("Authority Hierarchy", f"⚠️ WARN (@{director_name} missing)"))

        failed_cogs = [name for name, status in self.cog_load_status.items() if status != "OK"]
        if not failed_cogs:
            tests.append(("Plugin Cog Suite", f"✅ PASS ({len(self.cog_load_status)} cogs)"))
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
        embed.set_footer(text=f"Project-Edith • Startup Diagnostics | Status: {'ALL SYSTEMS GO' if overall_healthy else 'WARNINGS PRESENT'}")
        return embed, overall_healthy

    async def on_ready(self):
        print(f"[READY] Operational as {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="over server | .help")
        )

        for guild in self.guilds:
            layout_cog = self.get_cog("LayoutRemapping")
            if layout_cog:
                try:
                    await layout_cog.setup_environment(guild)
                except Exception as e:
                    print(f"[LAYOUT SETUP ERROR] {e}")

            err_channel = await self.get_or_create_error_log_channel(guild)
            if err_channel:
                try:
                    report_embed, _ = await self.run_diagnostic_suite(guild)
                    await err_channel.send(embed=report_embed)
                    print(f"[DIAGNOSTICS] Diagnostic audit dispatched to #{err_channel.name}")
                except Exception as e:
                    print(f"[DIAGNOSTICS DISPATCH ERROR] {e}")

    async def close(self):
        if self.db:
            try:
                await self.db.commit()
                await self.db.close()
            except Exception:
                pass
        await super().close()
        await asyncio.sleep(0.5)


bot = UtilityBot()


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

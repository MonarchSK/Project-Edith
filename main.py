import os
import sys
import asyncio
import aiosqlite
import discord
from discord.ext import commands
from datetime import datetime, timezone
import config

DB_PATH = "bot_database.db"

# 1. Check token immediately before doing anything
TOKEN = config.TOKEN or os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("\n[CRITICAL ERROR] DISCORD_TOKEN is missing! Set it in Render Environment Variables.", file=sys.stderr)
    sys.exit(1)

# 2. Setup intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

class UtilityBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=".",
            intents=intents,
            help_command=None
        )
        self.db: aiosqlite.Connection = None
        self.restore_report = {
            "success": False,
            "filename": None,
            "timestamp": None,
            "filesize_kb": 0.0,
            "is_fresh": False
        }

    async def restore_database_from_discord(self):
        """Scans the backup channel and restores the latest snapshot."""
        backup_id = config.BACKUP_CHANNEL_ID
        if not backup_id or backup_id == 0:
            print("[RESTORE] BACKUP_CHANNEL_ID is 0 or unset. Starting fresh.")
            self.restore_report["is_fresh"] = True
            return

        try:
            channel = self.get_channel(backup_id) or await self.fetch_channel(backup_id)
        except Exception as e:
            print(f"[RESTORE ERROR] Could not access channel {backup_id}: {e}")
            self.restore_report["is_fresh"] = True
            return

        print(f"[RESTORE] Searching #{channel.name} for database snapshots...")
        try:
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
                            "is_fresh": False
                        }
                        print(f"[RESTORE] Restored successfully from '{attachment.filename}'")
                        return
        except Exception as e:
            print(f"[RESTORE ERROR] Failed during history scan: {e}")

        self.restore_report["is_fresh"] = True

    async def setup_hook(self):
        print("[SETUP] Opening SQLite database...")
        self.db = await aiosqlite.connect(DB_PATH)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS birthdays (
                user_id INTEGER PRIMARY KEY,
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
        await self.db.commit()

        # Load all cogs safely
        cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
        if os.path.exists(cogs_dir):
            for filename in os.listdir(cogs_dir):
                if filename.endswith(".py") and not filename.startswith("__"):
                    cog_name = f"cogs.{filename[:-3]}"
                    try:
                        await self.load_extension(cog_name)
                        print(f"[COG] Successfully loaded: {cog_name}")
                    except Exception as e:
                        print(f"[COG ERROR] Failed loading {cog_name}: {e}", file=sys.stderr)

        # Sync slash commands
        try:
            synced = await self.tree.sync()
            print(f"[SLASH] Synced {len(synced)} slash commands.")
        except Exception as e:
            print(f"[SLASH ERROR] Sync failed: {e}", file=sys.stderr)

    async def on_ready(self):
        print(f"[ONLINE] Logged in as: {self.user} (ID: {self.user.id})")

        # Database cloud check
        await self.restore_database_from_discord()

        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="over server | .help")
        )

        # Ensure Director environment exists
        for guild in self.guilds:
            layout_cog = self.get_cog("LayoutRemapping")
            if layout_cog:
                try:
                    await layout_cog.setup_environment(guild)
                except Exception as e:
                    print(f"[LAYOUT ERROR] {e}")

    async def close(self):
        if self.db:
            try:
                await self.db.commit()
                await self.db.close()
            except Exception:
                pass
        await super().close()

bot = UtilityBot()

async def main():
    async with bot:
        try:
            await bot.start(TOKEN)
        except discord.errors.LoginFailure:
            print("\n" + "="*50, file=sys.stderr)
            print("[CRITICAL] DISCORD_TOKEN is INVALID! Check your token in Render settings.", file=sys.stderr)
            print("="*50 + "\n", file=sys.stderr)
        except discord.errors.PrivilegedIntentsRequired:
            print("\n" + "="*50, file=sys.stderr)
            print("[CRITICAL] Privileged Intents disabled! Turn ON 'Server Members Intent' and 'Message Content Intent' in Discord Dev Portal.", file=sys.stderr)
            print("="*50 + "\n", file=sys.stderr)
        except Exception as e:
            print(f"\n[CRITICAL ERROR ENCOUNTERED] {type(e).__name__}: {e}", file=sys.stderr)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

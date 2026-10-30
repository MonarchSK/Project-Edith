import os
import sys
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
        """Scans #bot-memory for the latest .db backup file before serving requests."""
        if not config.BACKUP_CHANNEL_ID:
            print("[RESTORE] BACKUP_CHANNEL_ID not configured. Initializing fresh database.")
            self.restore_report["is_fresh"] = True
            return

        try:
            channel = self.get_channel(config.BACKUP_CHANNEL_ID) or await self.fetch_channel(config.BACKUP_CHANNEL_ID)
        except Exception as e:
            print(f"[RESTORE ERROR] Could not reach backup channel {config.BACKUP_CHANNEL_ID}: {e}")
            self.restore_report["is_fresh"] = True
            return

        print(f"[RESTORE] Scanning #{channel.name} for database snapshots...")
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
                        print(f"[RESTORE] Database restored from '{attachment.filename}' ({round(size_kb, 2)} KB)")
                        return
        except Exception as e:
            print(f"[RESTORE ERROR] Scan failed: {e}")

        print("[RESTORE] No previous snapshot located. Starting fresh database.")
        self.restore_report["is_fresh"] = True

    async def setup_hook(self):
        # 1. Connect local SQLite instance
        self.db = await aiosqlite.connect(DB_PATH)

        # 2. Guarantee table structure
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
        await self.db.commit()

        # 3. Load modular extensions from cogs/
        cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
        if os.path.exists(cogs_dir):
            for filename in os.listdir(cogs_dir):
                if filename.endswith(".py") and not filename.startswith("__"):
                    cog_name = f"cogs.{filename[:-3]}"
                    try:
                        await self.load_extension(cog_name)
                        print(f"[COG LOADED] {cog_name}")
                    except Exception as e:
                        print(f"[COG ERROR] Failed loading {cog_name}: {e}", file=sys.stderr)

        # 4. Register Persistent Views (Buttons work even after restarts)
        from cogs.birthdays import CleanBirthdayView
        from cogs.tickets import TicketPluginView, ActiveTicketControls
        from cogs.confessions import ConfessionPluginView
        from cogs.color_roles import ColorPluginPanel

        bday_cog = self.get_cog("Birthdays")
        if bday_cog:
            self.add_view(CleanBirthdayView(bday_cog))
        self.add_view(TicketPluginView())
        self.add_view(ActiveTicketControls())
        self.add_view(ConfessionPluginView(self))

        color_cog = self.get_cog("ColorRoles")
        if color_cog:
            self.add_view(ColorPluginPanel(color_cog))

        # 5. Sync Slash Commands
        try:
            synced = await self.tree.sync()
            print(f"[SLASH] Successfully synced {len(synced)} slash commands.")
        except Exception as e:
            print(f"[SLASH ERROR] Sync failed: {e}", file=sys.stderr)

    async def send_startup_embed(self):
        if not config.BACKUP_CHANNEL_ID:
            return

        try:
            channel = self.get_channel(config.BACKUP_CHANNEL_ID) or await self.fetch_channel(config.BACKUP_CHANNEL_ID)
        except Exception:
            return

        b_count, c_count = 0, 0
        try:
            async with self.db.execute("SELECT COUNT(*) FROM birthdays") as c:
                row = await c.fetchone()
                b_count = row[0] if row else 0
            async with self.db.execute("SELECT COUNT(*) FROM confessions") as c:
                row = await c.fetchone()
                c_count = row[0] if row else 0
        except Exception:
            pass

        if self.restore_report["success"]:
            embed = discord.Embed(
                title="🟢 Database Restored & Operational",
                description="Project-Edith restarted. Storage synchronized from Discord cloud backup.",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="📦 Backup File", value=f"`{self.restore_report['filename']}`", inline=True)
            embed.add_field(name="⚖️ Size", value=f"`{self.restore_report['filesize_kb']} KB`", inline=True)
        else:
            embed = discord.Embed(
                title="🟡 Clean Database Initialized",
                description="No historical snapshot found. Ready for first registrations.",
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc)
            )

        embed.add_field(name="🎂 Restored Birthdays", value=f"`{b_count}` records", inline=True)
        embed.add_field(name="🤫 Restored Confessions", value=f"`{c_count}` records", inline=True)
        embed.set_footer(text=f"{self.user.name} • Persistent Cloud Storage")
        await channel.send(embed=embed)

    async def on_ready(self):
        print(f"[READY] Operational as {self.user} (ID: {self.user.id})")

        # Database cloud sync check on boot
        await self.restore_database_from_discord()

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

        await self.send_startup_embed()

    async def close(self):
        """Gracefully commits SQLite and flushes active aiohttp sessions."""
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
    async with bot:
        await bot.start(config.TOKEN)

if __name__ == "__main__":
    if not config.TOKEN:
        raise ValueError("CRITICAL: DISCORD_TOKEN is missing from your environment variables.")

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("[SHUTDOWN] Bot process terminated cleanly.")

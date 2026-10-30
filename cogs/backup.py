import os
import asyncio
import sqlite3
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone
import config

DB_PATH = "bot_database.db"
KEEP_BACKUP_COUNT = 3
WAIT_BEFORE_PRUNE_SECONDS = 420

class DiscordMemoryBackup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.hourly_backup.start()

    def cog_unload(self):
        self.hourly_backup.cancel()

    def _create_snapshot(self) -> str:
        temp = "temp_backup.db"
        if os.path.exists(temp):
            os.remove(temp)
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(temp)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        return temp

    async def upload_backup_and_prune(self, reason: str = "Routine Hourly Sync"):
        if not config.BACKUP_CHANNEL_ID:
            return
        channel = self.bot.get_channel(config.BACKUP_CHANNEL_ID)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(config.BACKUP_CHANNEL_ID)
            except Exception:
                return

        temp_file = self._create_snapshot()
        filename = f"backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.db"
        discord_file = discord.File(temp_file, filename=filename)

        new_msg = await channel.send(
            content=f"📦 **Bot Memory Snapshot**\n**Trigger:** {reason}\n**Time:** `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`",
            file=discord_file
        )

        if os.path.exists(temp_file):
            os.remove(temp_file)

        await asyncio.sleep(WAIT_BEFORE_PRUNE_SECONDS)

        try:
            valid_backups = []
            async for msg in channel.history(limit=100):
                if msg.author == self.bot.user and any(a.filename.endswith(".db") for a in msg.attachments):
                    valid_backups.append(msg)

            valid_backups.sort(key=lambda m: m.created_at, reverse=True)
            for stale_msg in valid_backups[KEEP_BACKUP_COUNT:]:
                await stale_msg.delete()
        except Exception as e:
            print(f"[CLEANUP ERROR] {e}")

    @tasks.loop(hours=1)
    async def hourly_backup(self):
        await self.upload_backup_and_prune(reason="Automated Hourly Interval")

    @hourly_backup.before_loop
    async def before_hourly_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)

    @commands.command(name="backup")
    @commands.has_permissions(administrator=True)
    async def manual_backup_cmd(self, ctx: commands.Context):
        msg = await ctx.send("⏳ Generating snapshot and dispatching to `#bot-memory`...")
        asyncio.create_task(self.upload_backup_and_prune(reason=f"Manual execution by {ctx.author}"))
        await msg.edit(content="✅ Snapshot pushed to cloud storage.")

async def setup(bot):
    await bot.add_cog(DiscordMemoryBackup(bot))

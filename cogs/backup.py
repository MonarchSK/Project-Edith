import os
import asyncio
import sqlite3
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone
import config

DB_PATH = "bot_database.db"
KEEP_BACKUP_COUNT = 3
MIN_INTERVAL_BETWEEN_SNAPSHOTS = 600


class DiscordMemoryBackup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.is_dirty = False
        self.last_backup_time = 0
        self.backup_lock = asyncio.Lock()
        self.backup_worker_task = None
        self.hourly_backup.start()

    async def cog_load(self):
        self.backup_worker_task = asyncio.create_task(self._debounced_backup_worker())

    def cog_unload(self):
        self.hourly_backup.cancel()
        if self.backup_worker_task and not self.backup_worker_task.done():
            self.backup_worker_task.cancel()

    def _create_snapshot(self) -> str:
        temp = "temp_backup.db"
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except Exception:
                pass

        src = sqlite3.connect(DB_PATH, timeout=20.0)
        dst = sqlite3.connect(temp)
        with dst:
            src.backup(dst, pages=100)
        dst.close()
        src.close()
        return temp

    async def prune_stale_backups(self, channel: discord.TextChannel):
        """Scans channel history and deletes all backups except the 3 newest."""
        try:
            valid_backups = []
            async for msg in channel.history(limit=50):
                if msg.author == self.bot.user and any(a.filename.endswith(".db") for a in msg.attachments):
                    valid_backups.append(msg)

            valid_backups.sort(key=lambda m: m.created_at, reverse=True)
            to_delete = valid_backups[KEEP_BACKUP_COUNT:]
            for stale_msg in to_delete:
                try:
                    await stale_msg.delete()
                    await asyncio.sleep(0.5)
                except discord.NotFound:
                    pass
                except Exception as e:
                    print(f"[BACKUP PRUNE ERROR] Failed deleting message {stale_msg.id}: {e}")
        except Exception as e:
            print(f"[BACKUP PRUNE ERROR] {e}")

    async def upload_backup_and_prune(self, reason: str = "Routine Sync"):
        """Thread-safe snapshot export to #bot-memory with instant pruning of backups older than 3."""
        async with self.backup_lock:
            backup_id = int(os.getenv("BACKUP_CHANNEL_ID", getattr(config, "BACKUP_CHANNEL_ID", 0)))
            if not backup_id:
                return

            channel = self.bot.get_channel(backup_id)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(backup_id)
                except Exception:
                    return

            try:
                loop = asyncio.get_running_loop()
                temp_file = await loop.run_in_executor(None, self._create_snapshot)

                now_utc = datetime.now(timezone.utc)
                filename = f"backup_{now_utc.strftime('%Y%m%d_%H%M%S')}.db"
                discord_file = discord.File(temp_file, filename=filename)

                # Fetch database stats for telemetry embed
                b_count, l_count, c_count, bump_info = 0, 0, 0, "None"
                try:
                    async with self.bot.db.execute("SELECT COUNT(*) FROM birthdays") as cur:
                        b_count = (await cur.fetchone())[0]
                    async with self.bot.db.execute("SELECT COUNT(*) FROM levels") as cur:
                        l_count = (await cur.fetchone())[0]
                    async with self.bot.db.execute("SELECT COUNT(*) FROM confessions") as cur:
                        c_count = (await cur.fetchone())[0]
                    async with self.bot.db.execute("SELECT last_bump_timestamp FROM bump_status LIMIT 1") as cur:
                        row = await cur.fetchone()
                        if row and row[0]:
                            bump_info = f"<t:{row[0]}:R>"
                except Exception:
                    pass

                embed = discord.Embed(
                    title="📦 SQLite Cloud Snapshot Saved",
                    color=discord.Color.green(),
                    timestamp=now_utc
                )
                embed.add_field(name="Trigger Reason", value=f"`{reason}`", inline=True)
                embed.add_field(name="Retained Limit", value=f"`Last {KEEP_BACKUP_COUNT} Files`", inline=True)
                embed.add_field(name="Last Bump Recorded", value=bump_info, inline=True)
                embed.add_field(name="Birthdays", value=f"`{b_count}`", inline=True)
                embed.add_field(name="Tracked Members", value=f"`{l_count}`", inline=True)
                embed.add_field(name="Confessions", value=f"`{c_count}`", inline=True)
                embed.set_footer(text="Project-Edith • Persistent Storage Engine")

                await channel.send(embed=embed, file=discord_file)

                if os.path.exists(temp_file):
                    os.remove(temp_file)

                self.last_backup_time = now_utc.timestamp()
                self.is_dirty = False

            except Exception as e:
                print(f"[BACKUP UPLOAD ERROR] {e}")
                return

            await self.prune_stale_backups(channel)

    def mark_dirty(self, reason: str = "Database Mutation"):
        self.is_dirty = True

    async def _debounced_backup_worker(self):
        while not self.bot.is_closed():
            await asyncio.sleep(60)
            now = datetime.now(timezone.utc).timestamp()
            if self.is_dirty and (now - self.last_backup_time >= MIN_INTERVAL_BETWEEN_SNAPSHOTS):
                await self.upload_backup_and_prune(reason="Batch Activity Sync")

    @tasks.loop(hours=1)
    async def hourly_backup(self):
        await self.upload_backup_and_prune(reason="Scheduled 1-Hour Routine")

    @hourly_backup.before_loop
    async def before_hourly_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(120)

    @commands.command(name="backup")
    @commands.has_permissions(administrator=True)
    async def manual_backup_cmd(self, ctx: commands.Context):
        """Manually trigger persistent backup: .backup"""
        msg = await ctx.send("⏳ Generating snapshot and dispatching to `#bot-memory`...")
        await self.upload_backup_and_prune(reason=f"Manual execution by {ctx.author.name}")
        await msg.edit(content=f"✅ Database snapshot stored and older backups pruned (retaining last {KEEP_BACKUP_COUNT}).")


async def setup(bot):
    await bot.add_cog(DiscordMemoryBackup(bot))

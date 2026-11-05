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

        src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=20.0)
        dst = sqlite3.connect(temp, timeout=20.0)
        with dst:
            src.backup(dst, pages=100)
        dst.close()
        src.close()
        return temp

    async def prune_stale_backups_and_texts(self, channel: discord.TextChannel):
        """Strictly keeps only the last 3 backup attachments and purges all old text notices/logs."""
        try:
            backup_messages = []
            stray_bot_messages = []

            async for msg in channel.history(limit=100):
                if msg.author == self.bot.user:
                    # Check if this message has a database attachment
                    has_db = any(a.filename.endswith(".db") for a in msg.attachments)
                    if has_db:
                        backup_messages.append(msg)
                    else:
                        stray_bot_messages.append(msg)

            # 1. Prune all non-backup bot text messages in #bot-memory
            for stray in stray_bot_messages:
                try:
                    await stray.delete()
                    await asyncio.sleep(0.4)
                except discord.NotFound:
                    pass
                except Exception:
                    pass

            # 2. Sort backup archives newest -> oldest
            backup_messages.sort(key=lambda m: m.created_at, reverse=True)

            # 3. Retain strictly the newest 3 backups, delete the rest
            to_delete = backup_messages[KEEP_BACKUP_COUNT:]
            for stale_msg in to_delete:
                try:
                    await stale_msg.delete()
                    await asyncio.sleep(0.5)
                except discord.NotFound:
                    pass
                except Exception as e:
                    print(f"[BACKUP PRUNE ERROR] Failed deleting backup message {stale_msg.id}: {e}")

            if to_delete or stray_bot_messages:
                print(f"[BACKUP VAULT] Pruned {len(to_delete)} old backups and {len(stray_bot_messages)} text records. Retained: {min(len(backup_messages), KEEP_BACKUP_COUNT)}")
        except Exception as e:
            print(f"[BACKUP CLEANUP ERROR] {e}")

    async def upload_backup_and_prune(self, reason: str = "Routine Sync") -> tuple[bool, str]:
        """Thread-safe snapshot export to #bot-memory with strict retention enforcement."""
        async with self.backup_lock:
            backup_id = int(os.getenv("BACKUP_CHANNEL_ID", getattr(config, "BACKUP_CHANNEL_ID", 0)))
            if not backup_id or backup_id == 0:
                err = "BACKUP_CHANNEL_ID is unset or 0 in config/env."
                print(f"[BACKUP ERROR] {err}")
                return False, err

            channel = self.bot.get_channel(backup_id)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(backup_id)
                except Exception as e:
                    err = f"Could not find or fetch backup channel (ID: {backup_id}): {e}"
                    print(f"[BACKUP ERROR] {err}")
                    return False, err

            perms = channel.permissions_for(channel.guild.me)
            if not perms.send_messages or not perms.attach_files:
                err = f"Missing Send Messages or Attach Files permission in #{channel.name}."
                print(f"[BACKUP ERROR] {err}")
                return False, err

            temp_file = None
            try:
                loop = asyncio.get_running_loop()
                temp_file = await loop.run_in_executor(None, self._create_snapshot)

                now_utc = datetime.now(timezone.utc)
                filename = f"backup_{now_utc.strftime('%Y%m%d_%H%M%S')}.db"

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
                embed.add_field(name="Retained Limit", value=f"`Last {KEEP_BACKUP_COUNT} Snapshots`", inline=True)
                embed.add_field(name="Last Bump Recorded", value=bump_info, inline=True)
                embed.add_field(name="Birthdays", value=f"`{b_count}`", inline=True)
                embed.add_field(name="Tracked Members", value=f"`{l_count}`", inline=True)
                embed.add_field(name="Confessions", value=f"`{c_count}`", inline=True)
                embed.set_footer(text="Project-Edith • Persistent Storage Engine")

                with open(temp_file, "rb") as f:
                    discord_file = discord.File(f, filename=filename)
                    await channel.send(embed=embed, file=discord_file)

                self.last_backup_time = now_utc.timestamp()
                self.is_dirty = False
            except Exception as e:
                err = f"Failed during snapshot creation or upload: {e}"
                print(f"[BACKUP UPLOAD ERROR] {err}")
                return False, err
            finally:
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass

            # Prune to strictly 3 backups and clear stray texts
            await self.prune_stale_backups_and_texts(channel)
            return True, "Success"

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
        msg = await ctx.send("⏳ Generating snapshot and synchronizing `#bot-memory`...")
        success, detail = await self.upload_backup_and_prune(reason=f"Manual execution by {ctx.author.name}")
        if success:
            await msg.edit(content=f"✅ **Database snapshot saved!** Old files and text records pruned (strictly retaining last {KEEP_BACKUP_COUNT} backups).")
        else:
            await msg.edit(content=f"❌ **Backup Failed:** `{detail}`")


async def setup(bot):
    await bot.add_cog(DiscordMemoryBackup(bot))

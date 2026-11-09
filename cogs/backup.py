import os
import asyncio
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone
import config

DB_PATH = getattr(config, "DB_PATH", "bot_database.db")
KEEP_BACKUP_COUNT = 3
MIN_INTERVAL_BETWEEN_SNAPSHOTS = 600  # 10 minutes debounce


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
        # Check and restore snapshot on fresh container boots
        asyncio.create_task(self.restore_latest_backup_if_empty())

    def cog_unload(self):
        self.hourly_backup.cancel()
        if self.backup_worker_task and not self.backup_worker_task.done():
            self.backup_worker_task.cancel()

    def _get_active_db_path(self) -> str:
        return getattr(self.bot, "db_path", DB_PATH)

    def _create_snapshot(self) -> str:
        active_db = self._get_active_db_path()
        if not os.path.exists(active_db):
            raise FileNotFoundError(f"Source database file '{active_db}' does not exist.")

        temp_target = f"temp_backup_{os.getpid()}_{int(datetime.now(timezone.utc).timestamp())}.db"
        if os.path.exists(temp_target):
            try:
                os.remove(temp_target)
            except Exception:
                pass

        src = sqlite3.connect(f"file:{active_db}?mode=ro", uri=True, timeout=25.0)
        dst = sqlite3.connect(temp_target, timeout=25.0)
        try:
            with dst:
                src.backup(dst, pages=100)
        finally:
            dst.close()
            src.close()

        return temp_target

    async def get_backup_channel(self) -> discord.TextChannel | None:
        backup_id = int(os.getenv("BACKUP_CHANNEL_ID", getattr(config, "BACKUP_CHANNEL_ID", 0)))
        if not backup_id:
            return None

        channel = self.bot.get_channel(backup_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(backup_id)
            except Exception:
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def restore_latest_backup_if_empty(self):
        """Auto-recovers database state on fresh Render container boot if local DB has no records."""
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)  # Allow other cogs to initialize database handles

        if not hasattr(self.bot, "db") or not self.bot.db:
            return

        # Check if birthdays or levels already have data
        has_data = False
        try:
            async with self.bot.db.execute("SELECT COUNT(*) FROM birthdays") as cur:
                if (await cur.fetchone())[0] > 0:
                    has_data = True
        except Exception:
            pass

        if has_data:
            return

        channel = await self.get_backup_channel()
        if not channel:
            return

        try:
            latest_db_attachment = None
            async for msg in channel.history(limit=50):
                if msg.author == self.bot.user:
                    for att in msg.attachments:
                        if att.filename.endswith(".db"):
                            latest_db_attachment = att
                            break
                    if latest_db_attachment:
                        break

            if not latest_db_attachment:
                return

            print(f"[RECOVERY ENGINE] Fresh database detected. Restoring snapshot from #{channel.name} ({latest_db_attachment.filename})...")
            download_path = "restored_backup.db"
            await latest_db_attachment.save(download_path)

            # Safely replace active db data
            active_db = self._get_active_db_path()
            src = sqlite3.connect(download_path)
            dst = sqlite3.connect(active_db)
            try:
                with dst:
                    src.backup(dst)
                print("[RECOVERY ENGINE] Database state successfully restored from Discord cloud backup.")
            finally:
                src.close()
                dst.close()
                if os.path.exists(download_path):
                    os.remove(download_path)

        except Exception as e:
            print(f"[RECOVERY ERROR] Failed restoring backup snapshot: {e}")

    async def prune_stale_backups_and_texts(self, channel: discord.TextChannel):
        """Strictly keeps only the last 3 backup snapshots and purges all old text notices/logs."""
        try:
            backup_messages = []
            stray_bot_messages = []

            async for msg in channel.history(limit=100):
                if msg.author == self.bot.user:
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
            channel = await self.get_backup_channel()
            if not channel:
                err = "BACKUP_CHANNEL_ID is unset, invalid, or inaccessible."
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
                if hasattr(self.bot, "db") and self.bot.db:
                    try:
                        async with self.bot.db.execute("SELECT COUNT(*) FROM birthdays") as cur:
                            b_count = (await cur.fetchone())[0]
                    except Exception:
                        pass
                    try:
                        async with self.bot.db.execute("SELECT COUNT(*) FROM levels") as cur:
                            l_count = (await cur.fetchone())[0]
                    except Exception:
                        pass
                    try:
                        async with self.bot.db.execute("SELECT COUNT(*) FROM confessions") as cur:
                            c_count = (await cur.fetchone())[0]
                    except Exception:
                        pass
                    try:
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

    @app_commands.command(name="backup", description="Manually generate and upload a SQLite database snapshot.")
    @app_commands.checks.has_permissions(administrator=True)
    async def manual_backup_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        success, detail = await self.upload_backup_and_prune(reason=f"Slash command execution by {interaction.user.name}")
        if success:
            await interaction.followup.send(f"✅ **Database snapshot saved!** Old backups pruned to {KEEP_BACKUP_COUNT}.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ **Backup Failed:** `{detail}`", ephemeral=True)


async def setup(bot):
    await bot.add_cog(DiscordMemoryBackup(bot))

import os
import asyncio
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import config

RETENTION_DAYS = 3


class AuditLogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.audit_channel_cache: dict[int, int] = {}
        self.prune_old_audit_logs.start()

    def cog_unload(self):
        self.prune_old_audit_logs.cancel()

    async def get_or_create_audit_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Finds or provisions an #audit-logs channel inside the 🛠️ BACKUPS & CONTROL category."""
        # Check in-memory cache first
        if guild.id in self.audit_channel_cache:
            cached_chan = guild.get_channel(self.audit_channel_cache[guild.id])
            if cached_chan and isinstance(cached_chan, discord.TextChannel):
                return cached_chan

        # 1. Check explicit config channel ID
        cfg_id = int(os.getenv("AUDIT_LOGS_CHANNEL_ID", getattr(config, "AUDIT_LOGS_CHANNEL_ID", 0)))
        if cfg_id:
            chan = guild.get_channel(cfg_id)
            if chan and isinstance(chan, discord.TextChannel):
                self.audit_channel_cache[guild.id] = chan.id
                return chan

        # 2. Check for existing channel by name
        for c in guild.text_channels:
            if c.name.lower() in ["audit-logs", "message-logs", "msg-logs", "server-audits"]:
                self.audit_channel_cache[guild.id] = c.id
                return c

        # 3. Locate or create '🛠️ BACKUPS & CONTROL' category
        target_cat = None
        for cat in guild.categories:
            if "BACKUP" in cat.name.upper() or "CONTROL" in cat.name.upper():
                target_cat = cat
                break

        if not target_cat:
            try:
                target_cat = await guild.create_category(name="🛠️ BACKUPS & CONTROL")
            except Exception:
                target_cat = None

        # 4. Provision channel with secure role overwrites
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    embed_links=True,
                    read_message_history=True,
                    manage_messages=True
                )
            }
            director_name = getattr(config, "DIRECTOR_ROLE_NAME", "Director")
            director_role = discord.utils.get(guild.roles, name=director_name)
            if director_role:
                overwrites[director_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=False,
                    read_message_history=True
                )

            channel = await guild.create_text_channel(
                name="audit-logs",
                category=target_cat,
                overwrites=overwrites,
                topic="Automated audit trail for edited/deleted messages. Logs auto-purge after 3 days."
            )
            self.audit_channel_cache[guild.id] = channel.id
            print(f"[AUDIT] Created #{channel.name} under category '{target_cat.name if target_cat else 'None'}'")
            return channel
        except Exception as e:
            print(f"[AUDIT ERROR] Could not provision audit-logs channel: {e}")
            return None

    async def find_deletion_moderator(self, guild: discord.Guild, message: discord.Message) -> discord.Member | None:
        """Inspects Discord audit logs to identify if a moderator purged the message."""
        if not guild.me.guild_permissions.view_audit_log:
            return None

        # Short pause to allow Discord gateway to register the audit log record
        await asyncio.sleep(0.8)

        now = datetime.now(timezone.utc)
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.message_delete):
                if entry.target.id != message.author.id:
                    continue

                if hasattr(entry.extra, "channel") and entry.extra.channel.id != message.channel.id:
                    continue

                time_diff = (now - entry.created_at).total_seconds()
                if abs(time_diff) <= 6.0:
                    return entry.user
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[AUDIT LOG FETCH ERROR] {e}")

        return None

    # --- Listener: Message Deletions ---
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        channel = await self.get_or_create_audit_channel(message.guild)
        if not channel or message.channel.id == channel.id:
            return

        deleter = await self.find_deletion_moderator(message.guild, message)

        content = message.content.strip() if message.content else "*[No text content]*"
        if len(content) > 1024:
            content = content[:1020] + "..."

        embed = discord.Embed(
            title="🗑️ Message Deleted",
            description=f"A message from {message.author.mention} was deleted in {message.channel.mention}.",
            color=discord.Color.from_rgb(237, 66, 69),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(name=f"{message.author} ({message.author.id})", icon_url=message.author.display_avatar.url)
        embed.add_field(name="📄 Message Content", value=content, inline=False)

        # Attribution block
        if deleter and deleter.id != message.author.id:
            embed.add_field(name="🛡️ Action Taken By", value=f"{deleter.mention} (`{deleter.id}`)", inline=True)
        else:
            embed.add_field(name="🛡️ Action Taken By", value=f"{message.author.mention} *(Self-deletion / Unlogged)*", inline=True)

        if message.attachments:
            att_list = [f"• [`{att.filename}`]({att.url}) ({round(att.size / 1024, 1)} KB)" for att in message.attachments]
            embed.add_field(name=f"📎 Attachments ({len(message.attachments)})", value="\n".join(att_list[:5]), inline=False)
            
            # Preview image if attachment is an image format
            first_att = message.attachments[0]
            if first_att.content_type and first_att.content_type.startswith("image/"):
                embed.set_image(url=first_att.proxy_url or first_att.url)

        embed.set_footer(text=f"Channel: #{message.channel.name} • Message ID: {message.id}")

        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[AUDIT DISPATCH ERROR] {e}")

    # --- Listener: Bulk Message Deletions ---
    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages:
            return
        guild = messages[0].guild
        if not guild:
            return

        channel = await self.get_or_create_audit_channel(guild)
        if not channel or messages[0].channel.id == channel.id:
            return

        embed = discord.Embed(
            title="🧹 Bulk Messages Purged",
            description=f"**{len(messages)}** messages were bulk-deleted in {messages[0].channel.mention}.",
            color=discord.Color.from_rgb(237, 66, 69),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"Channel: #{messages[0].channel.name}")

        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[AUDIT BULK DISPATCH ERROR] {e}")

    # --- Listener: Message Edits ---
    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild:
            return

        # Skip pinned state mutations, embeds auto-loading, or identical content
        if before.content == after.content:
            return

        channel = await self.get_or_create_audit_channel(before.guild)
        if not channel or before.channel.id == channel.id:
            return

        before_content = before.content.strip() if before.content else "*[Empty]*"
        after_content = after.content.strip() if after.content else "*[Empty]*"

        if len(before_content) > 1000:
            before_content = before_content[:996] + "..."
        if len(after_content) > 1000:
            after_content = after_content[:996] + "..."

        embed = discord.Embed(
            title="✏️ Message Edited",
            description=f"A message from {before.author.mention} was modified in {before.channel.mention}. [Jump to Message]({after.jump_url})",
            color=discord.Color.from_rgb(254, 231, 92),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(name=f"{before.author} ({before.author.id})", icon_url=before.author.display_avatar.url)
        embed.add_field(name="🔴 Before Edit", value=before_content, inline=False)
        embed.add_field(name="🟢 After Edit", value=after_content, inline=False)
        embed.set_footer(text=f"Channel: #{before.channel.name} • Message ID: {before.id}")

        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[AUDIT DISPATCH ERROR] {e}")

    # --- Task: Auto-Delete Audit Logs Older Than 3 Days ---
    @tasks.loop(hours=2)
    async def prune_old_audit_logs(self):
        """Purges messages in the audit-logs channel older than the retention threshold using bulk delete."""
        now = datetime.now(timezone.utc)
        cutoff_date = now - timedelta(days=RETENTION_DAYS)
        bulk_limit_date = now - timedelta(days=14)

        for guild in self.bot.guilds:
            channel = await self.get_or_create_audit_channel(guild)
            if not channel:
                continue

            try:
                bulk_eligible = []
                single_eligible = []

                async for msg in channel.history(limit=250):
                    if msg.created_at < cutoff_date:
                        if msg.created_at > bulk_limit_date:
                            bulk_eligible.append(msg)
                        else:
                            single_eligible.append(msg)

                # 1. Fast purge using Discord's bulk API (<14 days old)
                if bulk_eligible:
                    for i in range(0, len(bulk_eligible), 100):
                        chunk = bulk_eligible[i:i + 100]
                        if len(chunk) == 1:
                            await chunk[0].delete()
                        else:
                            await channel.delete_messages(chunk)
                        await asyncio.sleep(1.0)

                # 2. Sequential fallback for messages older than 14 days
                for msg in single_eligible:
                    try:
                        await msg.delete()
                        await asyncio.sleep(0.6)
                    except discord.NotFound:
                        pass
                    except Exception:
                        pass

                total_purged = len(bulk_eligible) + len(single_eligible)
                if total_purged > 0:
                    print(f"[AUDIT RETENTION] Purged {total_purged} records older than {RETENTION_DAYS} days in #{channel.name}")
            except Exception as e:
                print(f"[AUDIT PURGE ERROR] {e}")

    @prune_old_audit_logs.before_loop
    async def before_audit_prune(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)


async def setup(bot):
    await bot.add_cog(AuditLogs(bot))

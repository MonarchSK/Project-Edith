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
        self.prune_old_audit_logs.start()

    def cog_unload(self):
        self.prune_old_audit_logs.cancel()

    async def get_or_create_audit_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Finds or creates an #audit-logs channel inside the 🛠️ BACKUPS & CONTROL category."""
        # 1. Check explicit config channel ID
        cfg_id = int(os.getenv("AUDIT_LOGS_CHANNEL_ID", getattr(config, "AUDIT_LOGS_CHANNEL_ID", 0)))
        if cfg_id:
            chan = guild.get_channel(cfg_id)
            if chan:
                return chan

        # 2. Check for existing channel by name
        for c in guild.text_channels:
            if c.name.lower() in ["audit-logs", "message-logs", "msg-logs", "server-audits"]:
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

        # 4. Provision channel with secure overwrites
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True, manage_messages=True)
            }
            director_role = discord.utils.get(guild.roles, name=getattr(config, "DIRECTOR_ROLE_NAME", "Director"))
            if director_role:
                overwrites[director_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)

            channel = await guild.create_text_channel(
                name="audit-logs",
                category=target_cat,
                overwrites=overwrites,
                topic="Automated audit trail for edited/deleted messages. Logs auto-purge after 3 days."
            )
            print(f"[AUDIT] Created #{channel.name} under category '{target_cat.name if target_cat else 'None'}'")
            return channel
        except Exception as e:
            print(f"[AUDIT ERROR] Could not provision audit-logs channel: {e}")
            return None

    # --- Listener: Message Deletions ---
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        channel = await self.get_or_create_audit_channel(message.guild)
        if not channel:
            return

        # Do not log deletions occurring inside the audit logs channel itself
        if message.channel.id == channel.id:
            return

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

        # Attachment tracking
        if message.attachments:
            att_list = [f"• [`{att.filename}`]({att.url}) ({round(att.size / 1024, 1)} KB)" for att in message.attachments]
            embed.add_field(name=f"📎 Attachments ({len(message.attachments)})", value="\n".join(att_list[:5]), inline=False)

        embed.set_footer(text=f"Channel: #{message.channel.name} • Message ID: {message.id}")

        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[AUDIT DISPATCH ERROR] {e}")

    # --- Listener: Message Edits ---
    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild:
            return

        # Ignore embeds loading, pinned state changes, or identical content
        if before.content == after.content:
            return

        channel = await self.get_or_create_audit_channel(before.guild)
        if not channel:
            return

        if before.channel.id == channel.id:
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
        """Purges messages in the audit-logs channel older than 3 days."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)

        for guild in self.bot.guilds:
            channel = await self.get_or_create_audit_channel(guild)
            if not channel:
                continue

            try:
                deleted_count = 0
                # Bulk purge messages older than cutoff but younger than 14 days
                async for msg in channel.history(limit=200):
                    if msg.created_at < cutoff_date:
                        try:
                            await msg.delete()
                            deleted_count += 1
                            await asyncio.sleep(0.5)
                        except discord.NotFound:
                            pass
                        except Exception:
                            pass

                if deleted_count > 0:
                    print(f"[AUDIT RETENTION] Purged {deleted_count} messages older than {RETENTION_DAYS} days in #{channel.name}")
            except Exception as e:
                print(f"[AUDIT PURGE ERROR] {e}")

    @prune_old_audit_logs.before_loop
    async def before_audit_prune(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)


async def setup(bot):
    await bot.add_cog(AuditLogs(bot))

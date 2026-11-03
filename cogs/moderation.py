import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import config

MAX_PURGE_LIMIT = 100
CONFIRMATION_DELETE_DELAY = 5


def is_mod_authorized(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or member.guild_permissions.ban_members:
        return True

    director_role = getattr(config, "DIRECTOR_ROLE_NAME", "Director").lower()
    allowed_roles = {director_role, "authority", "admin", "administrator", "moderator", "mod", "head mod"}
    return any(r.name.lower() in allowed_roles for r in member.roles)


def parse_time_duration(time_str: str) -> int:
    unit = time_str[-1].lower()
    try:
        val = int(time_str[:-1])
    except ValueError:
        return 0

    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return val * multipliers.get(unit, 0)


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def log_mod_action(self, embed: discord.Embed):
        log_id = int(os.getenv("TICKET_LOGS_CHANNEL_ID", getattr(config, "TICKET_LOGS_CHANNEL_ID", 0)))
        if not log_id:
            return
        channel = self.bot.get_channel(log_id)
        if channel:
            try:
                await channel.send(embed=embed)
            except Exception:
                pass

    async def check_hierarchy(self, actor: discord.Member, target: discord.Member) -> bool:
        if target == actor:
            return False
        if target.id == actor.guild.owner_id:
            return False
        if actor.id == actor.guild.owner_id:
            return True
        return actor.top_role > target.top_role

    async def execute_purge(self, channel: discord.TextChannel, amount: int, target_member: discord.Member = None) -> int:
        def check_filter(msg: discord.Message) -> bool:
            return msg.author.id == target_member.id if target_member else True
        deleted_messages = await channel.purge(limit=amount, check=check_filter, bulk=True)
        return len(deleted_messages)

    @commands.command(name="purge", aliases=["clear", "clean"])
    async def prefix_purge(self, ctx: commands.Context, amount: int, member: discord.Member = None):
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ **Access Denied:** Missing moderation authority.", delete_after=4)
            return

        if not (1 <= amount <= MAX_PURGE_LIMIT):
            await ctx.send(f"❌ Enter an amount between `1` and `{MAX_PURGE_LIMIT}`.", delete_after=4)
            return

        try:
            await ctx.message.delete()
        except Exception:
            pass

        count = await self.execute_purge(ctx.channel, amount, member)
        embed = discord.Embed(
            title="⚡ Messages Purged",
            description=f"Purged **{count}** messages from {ctx.channel.mention}." + (f" (Author: {member.mention})" if member else "") + f"\n\n⏳ *Self-destructing in {CONFIRMATION_DELETE_DELAY}s.*",
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc)
        )
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(CONFIRMATION_DELETE_DELAY)
        try:
            await msg.delete()
        except Exception:
            pass

    @commands.command(name="warn")
    async def prefix_warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ You do not have authority to issue warnings.", delete_after=4)
            return

        if not await self.check_hierarchy(ctx.author, member):
            await ctx.send("❌ You cannot warn a member with an equal or higher role than yours.", delete_after=5)
            return

        await self.bot.db.execute("""
            INSERT INTO warnings (guild_id, user_id, moderator_id, reason)
            VALUES (?, ?, ?, ?)
        """, (ctx.guild.id, member.id, ctx.author.id, reason))
        await self.bot.db.commit()

        backup_cog = self.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "mark_dirty"):
            backup_cog.mark_dirty(reason="User Warning Registered")

        async with self.bot.db.execute(
            "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, member.id)
        ) as cur:
            warn_count = (await cur.fetchone())[0]

        try:
            await member.send(f"⚠️ You received a formal warning in **{ctx.guild.name}**:\n**Reason:** {reason}\n**Total Warnings:** {warn_count}")
        except Exception:
            pass

        embed = discord.Embed(
            title="⚠️ Infraction Issued",
            description=f"{member.mention} has been warned by {ctx.author.mention}.\n**Reason:** {reason}\n**Infraction Count:** `{warn_count}`",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        await ctx.send(embed=embed)
        await self.log_mod_action(embed)

    @commands.command(name="warnings", aliases=["warns"])
    async def prefix_warnings(self, ctx: commands.Context, member: discord.Member = None):
        target = member or ctx.author
        async with self.bot.db.execute("""
            SELECT id, moderator_id, reason, timestamp FROM warnings
            WHERE guild_id = ? AND user_id = ?
            ORDER BY timestamp DESC
        """, (ctx.guild.id, target.id)) as cur:
            rows = await cur.fetchall()

        if not rows:
            await ctx.send(f"✅ {target.mention} has a clean record (0 infractions).")
            return

        embed = discord.Embed(
            title=f"📋 Infraction History — {target.display_name}",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        entries = []
        for wid, mod_id, reason, stamp in rows[:10]:
            try:
                epoch = int(datetime.fromisoformat(stamp).timestamp())
                time_display = f"<t:{epoch}:R>"
            except Exception:
                time_display = stamp
            entries.append(f"• **ID #{wid}** | <@{mod_id}>: `{reason}` ({time_display})")

        embed.description = f"Total Warnings: **{len(rows)}**\n\n" + "\n".join(entries)
        await ctx.send(embed=embed)

    @commands.command(name="clearwarns")
    @commands.has_permissions(administrator=True)
    async def prefix_clearwarns(self, ctx: commands.Context, member: discord.Member):
        await self.bot.db.execute("DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, member.id))
        await self.bot.db.commit()

        backup_cog = self.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "mark_dirty"):
            backup_cog.mark_dirty(reason="Cleared User Warnings")

        embed = discord.Embed(
            title="🧹 Warnings Cleared",
            description=f"All recorded infractions for {member.mention} have been wiped by {ctx.author.mention}.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        await self.log_mod_action(embed)

    @commands.command(name="mute", aliases=["timeout"])
    async def prefix_mute(self, ctx: commands.Context, member: discord.Member, duration: str = "10m", *, reason: str = "Rule violation"):
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ Access Denied: Moderation authority required.", delete_after=4)
            return

        if not await self.check_hierarchy(ctx.author, member):
            await ctx.send("❌ You cannot mute a member with an equal or higher role than yours.", delete_after=5)
            return

        secs = parse_time_duration(duration)
        if secs <= 0 or secs > 2419200:
            await ctx.send("❌ Invalid duration. Use format like `10m`, `2h`, `1d` (max 28 days).", delete_after=5)
            return

        until = datetime.now(timezone.utc) + timedelta(seconds=secs)
        try:
            await member.timeout(until, reason=f"By {ctx.author.name}: {reason}")
            try:
                await member.send(f"🔇 You have been timed out in **{ctx.guild.name}** for **{duration}**.\n**Reason:** {reason}")
            except Exception:
                pass

            embed = discord.Embed(
                title="🔇 Member Timed Out",
                description=f"{member.mention} has been silenced for **{duration}** (<t:{int(until.timestamp())}:R>).\n**Reason:** {reason}\n**Moderator:** {ctx.author.mention}",
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc)
            )
            await ctx.send(embed=embed)
            await self.log_mod_action(embed)
        except discord.Forbidden:
            await ctx.send("❌ Discord rejected timeout. Verify Edith's role is higher than the target member.", delete_after=5)

    @commands.command(name="unmute", aliases=["untimeout"])
    async def prefix_unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Timeout lifted early"):
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ Access Denied.", delete_after=4)
            return

        try:
            await member.timeout(None, reason=f"Lifted by {ctx.author.name}: {reason}")
            embed = discord.Embed(
                title="🔊 Timeout Lifted",
                description=f"{member.mention}'s timeout has been cleared by {ctx.author.mention}.\n**Reason:** {reason}",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            await ctx.send(embed=embed)
            await self.log_mod_action(embed)
        except Exception as e:
            await ctx.send(f"❌ Failed to lift timeout: `{e}`", delete_after=5)

    @commands.command(name="kick")
    async def prefix_kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Violating server directives"):
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ Access Denied.", delete_after=4)
            return

        if not await self.check_hierarchy(ctx.author, member):
            await ctx.send("❌ Hierarchy error: Target outranks you.", delete_after=5)
            return

        try:
            await member.send(f"👢 You have been kicked from **{ctx.guild.name}**.\n**Reason:** {reason}")
        except Exception:
            pass

        try:
            await member.kick(reason=f"Kicked by {ctx.author.name}: {reason}")
            embed = discord.Embed(
                title="👢 Member Kicked",
                description=f"{member.mention} (`{member.id}`)\n**Reason:** {reason}",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc)
            )
            await ctx.send(embed=embed)
            await self.log_mod_action(embed)
        except discord.Forbidden:
            await ctx.send("❌ Discord permission error. Ensure Edith's role is positioned above the target member.")

    @commands.command(name="ban")
    async def prefix_ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Severe infraction / ban"):
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ Access Denied.", delete_after=4)
            return

        if not await self.check_hierarchy(ctx.author, member):
            await ctx.send("❌ Hierarchy error: Target outranks you.", delete_after=5)
            return

        try:
            await member.send(f"🔨 You have been permanently banned from **{ctx.guild.name}**.\n**Reason:** {reason}")
        except Exception:
            pass

        try:
            await ctx.guild.ban(member, reason=f"Banned by {ctx.author.name}: {reason}", delete_message_days=1)
            embed = discord.Embed(
                title="🔨 Member Banned",
                description=f"**Target:** {member.mention} (`{member.id}`)\n**Enforcing Mod:** {ctx.author.mention}\n**Reason:** {reason}",
                color=discord.Color.dark_red(),
                timestamp=datetime.now(timezone.utc)
            )
            await ctx.send(embed=embed)
            await self.log_mod_action(embed)
        except discord.Forbidden:
            await ctx.send("❌ Discord permission error. Edith's top role must be higher than the member's role.")

    @commands.command(name="unban")
    async def prefix_unban(self, ctx: commands.Context, user_id: int, *, reason: str = "Pardon / Appeal accepted"):
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ Access Denied.", delete_after=4)
            return

        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=f"Pardoned by {ctx.author.name}: {reason}")
            embed = discord.Embed(
                title="🔓 Member Unbanned",
                description=f"**User:** {user.name} (`{user.id}`)\n**Authorized By:** {ctx.author.mention}\n**Reason:** {reason}",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            await ctx.send(embed=embed)
            await self.log_mod_action(embed)
        except discord.NotFound:
            await ctx.send(f"❌ User ID `{user_id}` is not currently banned.", delete_after=5)
        except Exception as e:
            await ctx.send(f"❌ Failed to unban user: `{e}`", delete_after=5)


async def setup(bot):
    await bot.add_cog(Moderation(bot))

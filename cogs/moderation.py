import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from typing import Union
import config

MAX_PURGE_LIMIT = 100
CONFIRMATION_DELETE_DELAY = 5


def is_mod_authorized(member: discord.Member) -> bool:
    """Verifies that the actor has Administrator, Director, or Moderator privileges."""
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or member.guild_permissions.ban_members or member.guild_permissions.moderate_members:
        return True

    director_role = getattr(config, "DIRECTOR_ROLE_NAME", "Director").lower()
    allowed_roles = {director_role, "authority", "admin", "administrator", "moderator", "mod", "head mod"}
    return any(r.name.lower() in allowed_roles for r in member.roles)


def parse_time_duration(time_str: str) -> int:
    """Parses duration strings like 10m, 2h, 1d into seconds."""
    time_str = time_str.strip()
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
        """Safely dispatches an audit record to the configured staff log channel."""
        try:
            log_id = int(os.getenv("TICKET_LOGS_CHANNEL_ID", getattr(config, "TICKET_LOGS_CHANNEL_ID", 0)))
            if not log_id or log_id == 0:
                return
            channel = self.bot.get_channel(log_id)
            if channel:
                await channel.send(embed=embed)
        except Exception as e:
            print(f"[MOD LOG ERROR] Could not dispatch mod log: {e}")

    def can_moderate(self, actor: discord.Member, target: discord.Member) -> tuple[bool, str]:
        """Validates role hierarchy between actor, target, and the bot itself."""
        if target.id == actor.id:
            return False, "You cannot execute moderation actions on yourself."

        guild = actor.guild
        if target.id == guild.owner_id:
            return False, "You cannot moderate the Server Owner."

        if actor.id != guild.owner_id and target.top_role >= actor.top_role:
            return False, f"Hierarchy error: {target.mention} has an equal or higher role than you."

        if target.top_role >= guild.me.top_role:
            return False, f"Hierarchy error: {target.mention}'s role is higher than or equal to Edith's role. Drag Edith's role higher in Server Settings."

        return True, "OK"

    # =========================================================================
    # WARN COMMANDS
    # =========================================================================
    @commands.command(name="warn")
    async def prefix_warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Issue an infraction notice: .warn @user spamming"""
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ **Access Denied:** Only staff and authority roles can issue warnings.", delete_after=5)
            return

        can_act, err_msg = self.can_moderate(ctx.author, member)
        if not can_act:
            await ctx.send(f"❌ {err_msg}", delete_after=6)
            return

        await self.bot.db.execute("""
            INSERT INTO warnings (guild_id, user_id, moderator_id, reason)
            VALUES (?, ?, ?, ?)
        """, (ctx.guild.id, member.id, ctx.author.id, reason))
        await self.bot.db.commit()

        # Mark database dirty for backup sync
        backup_cog = self.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "mark_dirty"):
            backup_cog.mark_dirty(reason="User Warning Registered")

        async with self.bot.db.execute(
            "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, member.id)
        ) as cur:
            warn_count = (await cur.fetchone())[0]

        # DM notification
        try:
            await member.send(f"⚠️ You received an official warning in **{ctx.guild.name}**:\n**Reason:** {reason}\n**Total Warnings:** {warn_count}")
        except Exception:
            pass

        embed = discord.Embed(
            title="⚠️ Infraction Issued",
            description=f"{member.mention} was warned by {ctx.author.mention}.\n\n• **Reason:** {reason}\n• **Total Warnings:** `{warn_count}`",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        await ctx.send(embed=embed)
        await self.log_mod_action(embed)

    @commands.command(name="warnings", aliases=["warns"])
    async def prefix_warnings(self, ctx: commands.Context, member: discord.Member = None):
        """Check infraction record: .warnings @user"""
        target = member or ctx.author
        async with self.bot.db.execute("""
            SELECT id, moderator_id, reason, timestamp FROM warnings
            WHERE guild_id = ? AND user_id = ?
            ORDER BY timestamp DESC
        """, (ctx.guild.id, target.id)) as cur:
            rows = await cur.fetchall()

        if not rows:
            await ctx.send(f"✅ {target.mention} has a spotless record (0 warnings).")
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
            entries.append(f"• **ID #{wid}** | Mod: <@{mod_id}> — `{reason}` ({time_display})")

        embed.description = f"Total Infractions: **{len(rows)}**\n\n" + "\n".join(entries)
        await ctx.send(embed=embed)

    @commands.command(name="clearwarns")
    @commands.has_permissions(administrator=True)
    async def prefix_clearwarns(self, ctx: commands.Context, member: discord.Member):
        """Wipe infraction history: .clearwarns @user"""
        await self.bot.db.execute("DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, member.id))
        await self.bot.db.commit()

        backup_cog = self.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "mark_dirty"):
            backup_cog.mark_dirty(reason="Cleared User Warnings")

        embed = discord.Embed(
            title="🧹 Infractions Wiped",
            description=f"All recorded infractions for {member.mention} have been cleared by {ctx.author.mention}.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        await self.log_mod_action(embed)

    # =========================================================================
    # MUTE / TIMEOUT COMMANDS
    # =========================================================================
    @commands.command(name="mute", aliases=["timeout"])
    async def prefix_mute(self, ctx: commands.Context, member: discord.Member, duration: str = "10m", *, reason: str = "Rule violation"):
        """Mute a user using Discord timeout: .mute @user 30m spamming"""
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ **Access Denied:** Missing moderation authority.", delete_after=5)
            return

        can_act, err_msg = self.can_moderate(ctx.author, member)
        if not can_act:
            await ctx.send(f"❌ {err_msg}", delete_after=6)
            return

        secs = parse_time_duration(duration)
        if secs <= 0 or secs > 2419200:
            await ctx.send("❌ Invalid duration. Use formats like `10m`, `2h`, `1d` (maximum is 28 days).", delete_after=5)
            return

        until = datetime.now(timezone.utc) + timedelta(seconds=secs)

        try:
            await member.send(f"🔇 You have been muted in **{ctx.guild.name}** for **{duration}**.\n**Reason:** {reason}")
        except Exception:
            pass

        try:
            await member.timeout(until, reason=f"By {ctx.author.name}: {reason}")
            embed = discord.Embed(
                title="🔇 Member Timed Out",
                description=f"{member.mention} has been silenced for **{duration}** (<t:{int(until.timestamp())}:R>).\n\n• **Reason:** {reason}\n• **Moderator:** {ctx.author.mention}",
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc)
            )
            await ctx.send(embed=embed)
            await self.log_mod_action(embed)
        except discord.Forbidden:
            await ctx.send("❌ Discord rejected timeout. Verify Edith has the **Moderate Members** permission and her role is above the target.", delete_after=7)
        except Exception as e:
            await ctx.send(f"❌ Failed to mute member: `{e}`", delete_after=6)

    @commands.command(name="unmute", aliases=["untimeout"])
    async def prefix_unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Timeout lifted early"):
        """Unmute a timed-out user: .unmute @user"""
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ **Access Denied.**", delete_after=4)
            return

        try:
            await member.timeout(None, reason=f"Lifted by {ctx.author.name}: {reason}")
            embed = discord.Embed(
                title="🔊 Timeout Lifted",
                description=f"{member.mention}'s timeout was removed by {ctx.author.mention}.\n**Reason:** {reason}",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            await ctx.send(embed=embed)
            await self.log_mod_action(embed)
        except Exception as e:
            await ctx.send(f"❌ Failed to lift timeout: `{e}`", delete_after=5)

    # =========================================================================
    # BAN & KICK COMMANDS
    # =========================================================================
    @commands.command(name="kick")
    async def prefix_kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Violating server directives"):
        """Kick a member: .kick @user disruption"""
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ **Access Denied.**", delete_after=4)
            return

        can_act, err_msg = self.can_moderate(ctx.author, member)
        if not can_act:
            await ctx.send(f"❌ {err_msg}", delete_after=6)
            return

        try:
            await member.send(f"👢 You have been kicked from **{ctx.guild.name}**.\n**Reason:** {reason}")
        except Exception:
            pass

        try:
            await member.kick(reason=f"Kicked by {ctx.author.name}: {reason}")
            embed = discord.Embed(
                title="👢 Member Kicked",
                description=f"{member.mention} (`{member.id}`) was kicked by {ctx.author.mention}.\n**Reason:** {reason}",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc)
            )
            await ctx.send(embed=embed)
            await self.log_mod_action(embed)
        except discord.Forbidden:
            await ctx.send("❌ Discord rejected kick. Ensure Edith's role is positioned higher than the member.", delete_after=6)

    @commands.command(name="ban")
    async def prefix_ban(self, ctx: commands.Context, target: Union[discord.Member, discord.User], *, reason: str = "Severe infraction"):
        """Ban a member or user ID: .ban @user [reason] or .ban 1234567890 [reason]"""
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ **Access Denied.**", delete_after=4)
            return

        if isinstance(target, discord.Member):
            can_act, err_msg = self.can_moderate(ctx.author, target)
            if not can_act:
                await ctx.send(f"❌ {err_msg}", delete_after=6)
                return

        try:
            await target.send(f"🔨 You have been banned from **{ctx.guild.name}**.\n**Reason:** {reason}")
        except Exception:
            pass

        try:
            await ctx.guild.ban(target, reason=f"Banned by {ctx.author.name}: {reason}", delete_message_days=1)
            embed = discord.Embed(
                title="🔨 Member Banned",
                description=f"**Target:** {target.mention} (`{target.id}`)\n**Enforcing Mod:** {ctx.author.mention}\n**Reason:** {reason}",
                color=discord.Color.dark_red(),
                timestamp=datetime.now(timezone.utc)
            )
            await ctx.send(embed=embed)
            await self.log_mod_action(embed)
        except discord.Forbidden:
            await ctx.send("❌ Discord rejected ban. Ensure Edith has the **Ban Members** permission and her role is above the target.", delete_after=7)
        except Exception as e:
            await ctx.send(f"❌ Failed to ban: `{e}`", delete_after=5)

    @commands.command(name="unban")
    async def prefix_unban(self, ctx: commands.Context, user_id: int, *, reason: str = "Pardon / Appeal accepted"):
        """Unban by User ID: .unban 1234567890 [reason]"""
        if not is_mod_authorized(ctx.author):
            await ctx.send("⛔ **Access Denied.**", delete_after=4)
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
            await ctx.send(f"❌ Failed to unban: `{e}`", delete_after=5)


async def setup(bot):
    await bot.add_cog(Moderation(bot))

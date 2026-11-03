import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import config

MAX_PURGE_LIMIT = 100
CONFIRMATION_DELETE_DELAY = 5


def is_purge_authorized(member: discord.Member) -> bool:
    """Verifies if the user is an Administrator, Director, or authorized staff."""
    if not isinstance(member, discord.Member):
        return False

    # 1. Native Discord Permissions
    if member.guild_permissions.administrator or member.guild_permissions.manage_messages:
        return True

    # 2. Configured Director Role
    director_role_name = getattr(config, "DIRECTOR_ROLE_NAME", "Director")
    if any(r.name.lower() == director_role_name.lower() for r in member.roles):
        return True

    # 3. Explicit Authority Role Names
    allowed_role_names = {"authority", "admin", "administrator", "director", "moderator", "head mod"}
    return any(r.name.lower() in allowed_role_names for r in member.roles)


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def execute_purge(
        self,
        channel: discord.TextChannel,
        author: discord.Member,
        amount: int,
        target_member: discord.Member = None
    ) -> int:
        """Core purge execution engine with optional member filter."""
        def check_filter(msg: discord.Message) -> bool:
            if target_member:
                return msg.author.id == target_member.id
            return True

        # purge messages (Discord API handles 14-day limit automatically with bulk=True)
        deleted_messages = await channel.purge(limit=amount, check=check_filter, bulk=True)
        return len(deleted_messages)

    # --- Prefix Command: .purge <amount> [@user] ---
    @commands.command(name="purge", aliases=["clear", "clean"])
    async def prefix_purge(self, ctx: commands.Context, amount: int, member: discord.Member = None):
        """Purge messages: .purge 20 or .purge 20 @user"""
        if not is_purge_authorized(ctx.author):
            embed = discord.Embed(
                title="⛔ Access Denied",
                description="This protocol is restricted to **Administrators**, **Directors**, and **Authorized Staff**.",
                color=discord.Color.red()
            )
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(4)
            try:
                await msg.delete()
                await ctx.message.delete()
            except Exception:
                pass
            return

        if not (1 <= amount <= MAX_PURGE_LIMIT):
            await ctx.send(f"❌ Please enter an amount between `1` and `{MAX_PURGE_LIMIT}`.", delete_after=4)
            try:
                await ctx.message.delete()
            except Exception:
                pass
            return

        # Delete command invoker message first so it does not skew the count
        try:
            await ctx.message.delete()
        except Exception:
            pass

        deleted_count = await self.execute_purge(
            channel=ctx.channel,
            author=ctx.author,
            amount=amount,
            target_member=member
        )

        desc = f"🧹 Successfully purged **{deleted_count}** messages from {ctx.channel.mention}."
        if member:
            desc = f"🧹 Successfully purged **{deleted_count}** messages sent by {member.mention}."

        embed = discord.Embed(
            title="⚡ Channel Purge Executed",
            description=desc,
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Authorized Operative", value=ctx.author.mention, inline=True)
        embed.set_footer(text=f"Purged from #{ctx.channel.name} • Auto-clearing in {CONFIRMATION_DELETE_DELAY}s")

        confirm_msg = await ctx.send(embed=embed)
        await asyncio.sleep(CONFIRMATION_DELETE_DELAY)
        try:
            await confirm_msg.delete()
        except Exception:
            pass

    # --- Slash Command: /purge <amount> [member] ---
    @app_commands.command(name="purge", description="Purge messages from the current channel (Authority only).")
    @app_commands.describe(
        amount="Number of messages to delete (1-100)",
        member="Optional: Delete messages exclusively from this member"
    )
    async def slash_purge(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, MAX_PURGE_LIMIT],
        member: discord.Member = None
    ):
        if not is_purge_authorized(interaction.user):
            embed = discord.Embed(
                title="⛔ Access Denied",
                description="This command is restricted to **Administrators**, **Directors**, and **Authorized Staff**.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Immediate deferral to prevent 404 Unknown Interaction
        await interaction.response.defer(ephemeral=True)

        deleted_count = await self.execute_purge(
            channel=interaction.channel,
            author=interaction.user,
            amount=amount,
            target_member=member
        )

        desc = f"🧹 Purged **{deleted_count}** messages from {interaction.channel.mention}."
        if member:
            desc = f"🧹 Purged **{deleted_count}** messages authored by {member.mention}."

        embed = discord.Embed(
            title="⚡ Channel Purge Complete",
            description=desc,
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Authorized By", value=interaction.user.mention, inline=True)
        embed.set_footer(text="Project-Edith • Moderation Gateway")

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Moderation(bot))

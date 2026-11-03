import time
import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import config

REVIVE_COOLDOWN_SECONDS = 7200  # 2 Hours cooldown between revives
REVIVE_ROLE_NAME = "Revive Pings"

ENGAGING_CONVERSATION_TOPICS = [
    "What game or show has everyone been hooked on recently?",
    "If you could travel anywhere tomorrow with all expenses paid, where would you go?",
    "Drop your current favorite song or album recommendation right now!",
    "Hot take time: What is an opinion you have that would start an argument here?",
    "What's the best meal you've eaten all month?",
    "If you could master any skill instantly, what are you picking?",
    "What was the highlight of your week so far?"
]


def is_revive_authorized(member: discord.Member) -> bool:
    """Verifies that the user holds Admin, Director, or explicit Authority status."""
    if not isinstance(member, discord.Member):
        return False

    if member.guild_permissions.administrator:
        return True

    director_role = getattr(config, "DIRECTOR_ROLE_NAME", "Director").lower()
    allowed_roles = {director_role, "authority", "admin", "administrator", "head mod"}
    return any(r.name.lower() in allowed_roles for r in member.roles)


async def get_or_create_revive_role(guild: discord.Guild) -> discord.Role | None:
    """Finds or creates a dedicated opt-in role for members who want revive alerts."""
    role = discord.utils.get(guild.roles, name=REVIVE_ROLE_NAME)
    if not role:
        try:
            role = await guild.create_role(
                name=REVIVE_ROLE_NAME,
                color=discord.Color.from_rgb(255, 107, 107),
                mentionable=True,
                reason="Automated Chat Revive Notification Role"
            )
        except discord.Forbidden:
            return None
    return role


# --- Persistent Opt-In Role Toggle View ---
class ReviveRoleToggleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Toggle Revive Pings",
        style=discord.ButtonStyle.secondary,
        emoji="💬",
        custom_id="btn_toggle_revive_ping_role"
    )
    async def toggle_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user

        role = await get_or_create_revive_role(guild)
        if not role:
            await interaction.followup.send("⚠️ Lacking bot permissions to manage roles.", ephemeral=True)
            return

        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Self-service revive ping opt-out")
                await interaction.followup.send(f"🔕 You have removed the **{REVIVE_ROLE_NAME}** role.", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ Discord rejected role change. Ensure Edith's role is above Revive Pings.", ephemeral=True)
        else:
            try:
                await member.add_roles(role, reason="Self-service revive ping opt-in")
                await interaction.followup.send(f"🔔 **{REVIVE_ROLE_NAME} role assigned!**", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ Discord rejected role change. Ensure Edith's role is above Revive Pings.", ephemeral=True)


class Revive(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(ReviveRoleToggleView())

    async def get_cooldown_status(self, guild_id: int) -> tuple[bool, int, str]:
        """Checks SQLite database to enforce persistent cooldown across restarts."""
        now = int(time.time())
        try:
            async with self.bot.db.execute(
                "SELECT last_revive_timestamp FROM revive_status WHERE guild_id = ?",
                (guild_id,)
            ) as cursor:
                row = await cursor.fetchone()
        except Exception:
            return True, 0, ""

        if row and row[0]:
            last_timestamp = row[0]
            diff = now - last_timestamp
            if diff < REVIVE_COOLDOWN_SECONDS:
                remaining = REVIVE_COOLDOWN_SECONDS - diff
                minutes, seconds = divmod(remaining, 60)
                hours, minutes = divmod(minutes, 60)
                time_str = f"{hours}h {minutes}m {seconds}s" if hours > 0 else f"{minutes}m {seconds}s"
                return False, remaining, time_str

        return True, 0, ""

    async def save_revive_timestamp(self, guild_id: int, user_id: int):
        now = int(time.time())
        await self.bot.db.execute("""
            INSERT INTO revive_status (guild_id, last_revive_timestamp, last_reviver_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                last_revive_timestamp = excluded.last_revive_timestamp,
                last_reviver_id = excluded.last_reviver_id
        """, (guild_id, now, user_id))
        await self.bot.db.commit()

        backup_cog = self.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "mark_dirty"):
            backup_cog.mark_dirty(reason="Chat Revive Triggered")

    def create_revive_embed(self, guild: discord.Guild, author: discord.Member, custom_topic: str = None) -> discord.Embed:
        chosen_topic = custom_topic or random.choice(ENGAGING_CONVERSATION_TOPICS)

        embed = discord.Embed(
            title="⚡ CHAT REVIVE PROTOCOL ACTIVATED",
            description=(
                f"Hey everyone! The chat is too quiet — jump in and break the silence!\n\n"
                f"🔥 **Discussion Starter:**\n"
                f"> *\"{chosen_topic}\"*\n\n"
                f"Drop your answers below and get the conversation moving!"
            ),
            color=discord.Color.from_rgb(255, 75, 75),
            timestamp=datetime.now(timezone.utc)
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if hasattr(config, "COLOR_BANNER") and config.COLOR_BANNER:
            embed.set_image(url=config.COLOR_BANNER)

        embed.add_field(name="Initiated By", value=author.mention, inline=True)
        embed.set_footer(text="Project-Edith • Server Pulse | Next revive in 2 hours")
        return embed

    # --- Slash Command: /revive [topic] ---
    @app_commands.command(name="revive", description="Alert opt-in revive members to awaken chat discussions (Authority only).")
    @app_commands.describe(topic="Optional custom question or topic to start the discussion")
    async def slash_revive(self, interaction: discord.Interaction, topic: str = None):
        if not is_revive_authorized(interaction.user):
            embed = discord.Embed(
                title="⛔ Authority Clearance Required",
                description="The chat revive command is restricted to **Administrators**, **Directors**, and **Authority Staff**.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        can_revive, remaining, time_str = await self.get_cooldown_status(interaction.guild_id)
        if not can_revive:
            ready_epoch = int(time.time()) + remaining
            embed = discord.Embed(
                title="⏳ Revive Cooldown Active",
                description=f"The chat revive protocol is on cooldown.\nYou can awaken chat again in **{time_str}** (<t:{ready_epoch}:R>).",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        role = await get_or_create_revive_role(interaction.guild)
        role_mention = role.mention if role else "@here"

        await self.save_revive_timestamp(interaction.guild_id, interaction.user.id)

        embed = self.create_revive_embed(interaction.guild, interaction.user, topic)
        view = ReviveRoleToggleView()

        try:
            await interaction.channel.send(
                content=f"📢 {role_mention}",
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
            )
            await interaction.followup.send("✅ Chat revive broadcast dispatched successfully.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Discord blocked the broadcast. Verify Edith's permissions to mention roles.", ephemeral=True)

    # --- Prefix Command: .revive [topic] ---
    @commands.command(name="revive", aliases=["chatrevive", "wake"])
    async def prefix_revive(self, ctx: commands.Context, *, topic: str = None):
        """Broadcast chat revival to Revive Pings role: .revive or .revive What is everyone playing?"""
        if not is_revive_authorized(ctx.author):
            try:
                await ctx.message.delete()
            except Exception:
                pass
            await ctx.send("⛔ **Access Denied:** Only Administrators, Directors, and Authority roles may use `.revive`.", delete_after=5)
            return

        can_revive, remaining, time_str = await self.get_cooldown_status(ctx.guild.id)
        if not can_revive:
            ready_epoch = int(time.time()) + remaining
            embed = discord.Embed(
                title="⏳ Revive Cooldown Active",
                description=f"Chat revive is cooling down.\nYou can awaken chat again in **{time_str}** (<t:{ready_epoch}:R>).",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed, delete_after=8)
            try:
                await ctx.message.delete()
            except Exception:
                pass
            return

        try:
            await ctx.message.delete()
        except Exception:
            pass

        role = await get_or_create_revive_role(ctx.guild)
        role_mention = role.mention if role else "@here"

        await self.save_revive_timestamp(ctx.guild.id, ctx.author.id)

        embed = self.create_revive_embed(ctx.guild, ctx.author, topic)
        view = ReviveRoleToggleView()

        await ctx.channel.send(
            content=f"📢 {role_mention}",
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
        )


async def setup(bot):
    await bot.add_cog(Revive(bot))

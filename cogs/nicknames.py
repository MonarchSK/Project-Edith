import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import config

REQUIRED_NICKNAME_LEVEL = 10


async def get_user_level(db, user_id: int) -> int:
    """Fetches the user's current level from persistent levels table."""
    try:
        async with db.execute("SELECT level FROM levels WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 1
    except Exception:
        return 1


def is_privileged_override(member: discord.Member) -> bool:
    """Checks if the member is a Director or Admin who bypasses level restrictions."""
    if member.guild_permissions.administrator:
        return True
    return any(r.name == getattr(config, "DIRECTOR_ROLE_NAME", "Director") for r in member.roles)


# --- Modal: Change Nickname ---
class NicknameModal(discord.ui.Modal, title="Update Server Nickname"):
    new_nick = discord.ui.TextInput(
        label="Desired Server Nickname",
        placeholder="Enter your new name (max 32 characters)...",
        min_length=1,
        max_length=32,
        required=True
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        member = interaction.user
        guild = interaction.guild
        desired_name = self.new_nick.value.strip()

        # Check level requirement
        current_level = await get_user_level(self.cog.bot.db, member.id)
        if current_level < REQUIRED_NICKNAME_LEVEL and not is_privileged_override(member):
            embed = discord.Embed(
                title="🔒 Level Requirement Not Met",
                description=(
                    f"You must be at least **Level {REQUIRED_NICKNAME_LEVEL}** to change your server nickname.\n\n"
                    f"• **Your Level:** `{current_level} / {REQUIRED_NICKNAME_LEVEL}`\n"
                    f"• **Remaining:** Earn more XP through text chats, voice channels, or server bumps!"
                ),
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Check Discord role hierarchy limitations
        if member.id == guild.owner_id:
            await interaction.followup.send("⚠️ Discord does not permit bots to alter the Server Owner's nickname.", ephemeral=True)
            return

        if guild.me.top_role <= member.top_role:
            await interaction.followup.send(
                "⚠️ **Hierarchy Restriction:** Your highest role is equal to or higher than Edith's highest role. "
                "Move Edith's role above your role in Server Settings to allow nickname modifications.",
                ephemeral=True
            )
            return

        try:
            old_name = member.display_name
            await member.edit(nick=desired_name, reason=f"Self-service nickname update (Level {current_level})")

            embed = discord.Embed(
                title="✅ Server Nickname Updated",
                description=(
                    f"Your server identity has been successfully modified.\n\n"
                    f"• **Previous Name:** `{old_name}`\n"
                    f"• **New Nickname:** `{desired_name}`\n"
                    f"• **Operative Level:** `{current_level}`"
                ),
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="Project-Edith • Identity Protocol")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send("❌ Discord rejected this change. Please verify bot permissions (`Manage Nicknames`).", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error modifying nickname: `{e}`", ephemeral=True)


# --- Persistent Panel View ---
class NicknamePanelView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Change Nickname",
        style=discord.ButtonStyle.primary,
        emoji="🏷️",
        custom_id="btn_nick_change_clean"
    )
    async def change_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        current_level = await get_user_level(self.cog.bot.db, member.id)

        if current_level < REQUIRED_NICKNAME_LEVEL and not is_privileged_override(member):
            embed = discord.Embed(
                title="🔒 Level 10 Required",
                description=(
                    f"Custom nickname modification is reserved for experienced server operatives.\n\n"
                    f"• **Required:** `Level {REQUIRED_NICKNAME_LEVEL}`\n"
                    f"• **Your Current Level:** `Level {current_level}`\n"
                    f"• **Progress:** `{current_level * 10}%` complete\n\n"
                    f"Level up by chatting in channels, spending time in voice, or running `.bump`!"
                ),
                color=discord.Color.from_rgb(230, 126, 34)
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.send_modal(NicknameModal(self.cog))

    @discord.ui.button(
        label="Reset to Default",
        style=discord.ButtonStyle.secondary,
        emoji="🔄",
        custom_id="btn_nick_reset_clean"
    )
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        guild = interaction.guild

        if member.id == guild.owner_id:
            await interaction.followup.send("⚠️ Discord does not permit bots to alter the Server Owner's nickname.", ephemeral=True)
            return

        if guild.me.top_role <= member.top_role:
            await interaction.followup.send("⚠️ **Hierarchy Restriction:** Edith's role is below your highest role.", ephemeral=True)
            return

        try:
            await member.edit(nick=None, reason="Self-service nickname reset to default")
            embed = discord.Embed(
                title="🔄 Nickname Reset",
                description=f"Your nickname has been cleared and restored to **{member.name}**.",
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Failed to reset nickname: `{e}`", ephemeral=True)

    @discord.ui.button(
        label="Check Eligibility",
        style=discord.ButtonStyle.success,
        emoji="⭐",
        custom_id="btn_nick_check_clean"
    )
    async def check_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        current_level = await get_user_level(self.cog.bot.db, member.id)
        is_eligible = current_level >= REQUIRED_NICKNAME_LEVEL or is_privileged_override(member)

        embed = discord.Embed(
            title="⭐ Nickname Protocol Clearance",
            color=discord.Color.green() if is_eligible else discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Operative", value=member.mention, inline=True)
        embed.add_field(name="Current Level", value=f"`Level {current_level}`", inline=True)
        embed.add_field(
            name="Access Status",
            value="🔓 **ELIGIBLE — Clearance Granted**" if is_eligible else f"🔒 **LOCKED — Requires Level {REQUIRED_NICKNAME_LEVEL}**",
            inline=False
        )
        embed.add_field(name="Active Display Name", value=f"`{member.display_name}`", inline=True)
        embed.add_field(name="Original Handle", value=f"`{member.name}`", inline=True)
        embed.set_footer(text="Project-Edith • Identity Services")

        await interaction.followup.send(embed=embed, ephemeral=True)


# --- Nickname Cog Engine ---
class Nicknames(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_view = NicknamePanelView(self)

    async def cog_load(self):
        """Self-registers persistent view once to prevent duplicate listeners and pop-up spam."""
        self.bot.add_view(self.active_view)

    def create_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🏷️ Identity Studio // Nickname Terminal",
            description=(
                "Customize how your alias appears throughout the community.\n\n"
                "**Clearance Protocol:**\n"
                f"• Access to custom aliases requires **Level {REQUIRED_NICKNAME_LEVEL}+**.\n"
                "• Earn XP and increase your rank by chatting, participating in voice channels, and server bumping.\n"
                "• Inappropriate names violating server rules are subject to moderation."
            ),
            color=discord.Color.from_rgb(52, 152, 219)
        )
        if hasattr(config, "COLOR_BANNER") and config.COLOR_BANNER:
            embed.set_image(url=config.COLOR_BANNER)
        elif hasattr(config, "TICKET_BANNER") and config.TICKET_BANNER:
            embed.set_image(url=config.TICKET_BANNER)

        embed.add_field(
            name="⚙️ Available Controls",
            value=(
                "• **🏷️ Change Nickname:** Submit your custom display name.\n"
                "• **🔄 Reset to Default:** Revert display name back to your base account username.\n"
                "• **⭐ Check Eligibility:** Verify your current operative clearance."
            ),
            inline=False
        )
        embed.set_footer(text="Select an option below to initiate request")
        return embed

    @commands.command(name="nickname_panel", aliases=["nickpanel", "nick_panel"])
    @commands.has_permissions(administrator=True)
    async def prefix_panel(self, ctx: commands.Context):
        """Deploy the Nickname Terminal: .nickname_panel"""
        embed = self.create_panel_embed()
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await ctx.channel.send(embed=embed, view=self.active_view)

    @app_commands.command(name="nickname_panel", description="Deploy the interactive Nickname Studio panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = self.create_panel_embed()
        await interaction.channel.send(embed=embed, view=self.active_view)
        await interaction.followup.send("✅ Identity Studio terminal deployed successfully.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Nicknames(bot))

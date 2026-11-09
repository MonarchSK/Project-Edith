import os
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import config


def is_welcome_authorized(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    director_role = getattr(config, "DIRECTOR_ROLE_NAME", "Director").lower()
    allowed_roles = {director_role, "authority", "admin", "administrator", "head mod"}
    return any(r.name.lower() in allowed_roles for r in member.roles)


class WelcomePortalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Server Rules", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="btn_welcome_view_rules")
    async def rules_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📜 Server Directives & Code of Conduct",
            description=(
                "**1. Respect Everyone:** Zero tolerance for toxicity, hate speech, or harassment.\n"
                "**2. Keep Channels On-Topic:** Use appropriate channels for media, commands, and bots.\n"
                "**3. No Unauthorized Promotion:** Advertising or solicitation in DMs is strictly prohibited.\n"
                "**4. Respect Discord ToS:** Follow all Discord Community Guidelines.\n"
                "**5. Staff Authority:** Decisions made by Administrators and Directors are final."
            ),
            color=discord.Color.from_rgb(88, 101, 242)
        )
        embed.set_footer(text="Project-Edith • Moderation Protocols")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Channel Directory", style=discord.ButtonStyle.secondary, emoji="🧭", custom_id="btn_welcome_view_directory")
    async def directory_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        embed = discord.Embed(
            title="🧭 Server Navigation Directory",
            description=(
                f"Welcome to **{guild.name}**! Quick navigation links:\n\n"
                f"• **💬 General Lounge:** Main discussion & hangout hub.\n"
                f"• **🎙️ Voice Lounges:** Low-latency voice, gaming lobbies, and party chats.\n"
                f"• **🎁 Giveaways:** Special events, XP milestones, and Nitro draws.\n"
                f"• **🛠️ Support Desk:** Open a private ticket with staff at any time.\n"
                f"• **🏷️ Identity Studio:** Customize your nickname and cosmetic colors."
            ),
            color=discord.Color.teal()
        )
        embed.set_footer(text="Project-Edith • Community Map")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Operative Roles", style=discord.ButtonStyle.success, emoji="🎨", custom_id="btn_welcome_view_roles")
    async def roles_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎨 Roles, Perks & Progression",
            description=(
                "• **Rank Milestones:** Chat and stay in voice to hit Levels **5, 10, 25, and 50** for exclusive roles!\n"
                "• **Server Boosters:** Enjoy an automatic **1.5x XP Multiplier** on all activities.\n"
                "• **Bump Pings:** Opt-in via `.bump` to catch periodic server reminders and earn free XP.\n"
                "• **Revive Alerts:** Opt-in via `.revive` to participate in surprise discussion topics."
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="Project-Edith • Rank Progression")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.portal_view = WelcomePortalView()

    async def cog_load(self):
        """Self-registers persistent view once to keep buttons functional across bot restarts."""
        self.bot.add_view(self.portal_view)

    def create_welcome_embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(
            title=f"🌐 WELCOME TO {guild.name.upper()}!",
            description=(
                f"Welcome to **{guild.name}** — your premiere destination for community hangouts, "
                f"gaming lobbies, active voice lounges, exclusive giveaways, and events!\n\n"
                f"Whether you're looking to squad up for games, share media, earn XP milestones, or relax in voice, "
                f"you have found your new digital home."
            ),
            color=discord.Color.from_rgb(0, 168, 252),
            timestamp=datetime.now(timezone.utc)
        )

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        banner_url = (
            getattr(config, "WELCOME_BANNER", None)
            or getattr(config, "COLOR_BANNER", None)
            or getattr(config, "TICKET_BANNER", None)
        )
        if banner_url:
            embed.set_image(url=banner_url)

        embed.add_field(
            name="💬 1. Community & Hangouts",
            value=(
                "Join our public text lounges to chat about hobbies, tech, music, and daily life. "
                "Active conversation spawns **Random XP Drops (10–1000 XP)** that you can claim in real time!"
            ),
            inline=False
        )

        embed.add_field(
            name="🎮 2. Gaming Lobbies & Voice Chats",
            value=(
                "Jump into open **Voice Lounges** to stream gameplay, listen to music, or hop into co-op matches. "
                "Earning voice time grants passive **10 XP per minute** toward your operative rank!"
            ),
            inline=False
        )

        embed.add_field(
            name="🎁 3. Giveaways & Exclusive Rewards",
            value=(
                "We host regular community giveaways, Discord Nitro drops, and custom server perks. "
                "**Server Boosters** receive an automatic **1.5x XP Boost** across chat and voice channels."
            ),
            inline=False
        )

        embed.add_field(
            name="🛠️ 4. Identity Studio & Assistance",
            value=(
                "• **Level 10+ Nicknames:** Unlock custom alias modification in our Identity Studio.\n"
                "• **Birthday Tracking:** Register your birthday to receive an automated celebration.\n"
                "• **Staff Dispatch:** Open a private ticket with our Directorate team anytime."
            ),
            inline=False
        )

        embed.set_footer(
            text=f"Project-Edith • Operational Gateway | Total Operatives: {guild.member_count}",
            icon_url=guild.icon.url if guild.icon else None
        )
        return embed

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Greets new members and optionally assigns the base member role."""
        guild = member.guild
        if member.bot:
            return

        # 1. Auto-assign default member role if configured
        role_name = getattr(config, "MEMBER_ROLE_NAME", None)
        if role_name:
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                try:
                    await member.add_roles(role, reason="Auto-role assigned on join")
                except discord.Forbidden:
                    pass

        # 2. Dispatch arrival greeting to welcome channel
        welcome_channel_id = int(os.getenv("WELCOME_CHANNEL_ID", getattr(config, "WELCOME_CHANNEL_ID", 0)))
        channel = guild.get_channel(welcome_channel_id) if welcome_channel_id else guild.system_channel
        if not channel:
            return

        embed = discord.Embed(
            title="👋 New Operative Arrived!",
            description=(
                f"Welcome {member.mention} to **{guild.name}**!\n\n"
                f"• Check out our server rules and navigation directory below.\n"
                f"• Level up by joining text chats and voice lounges!\n"
                f"• You are member **#{guild.member_count}**."
            ),
            color=discord.Color.from_rgb(0, 168, 252),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Project-Edith • Arrival Protocol")

        try:
            await channel.send(content=f"Welcome {member.mention}!", embed=embed, view=self.portal_view)
        except Exception as e:
            print(f"[WELCOME JOIN ERROR] {e}")

    @app_commands.command(name="welcome", description="Post the official long-form server welcome guide into a designated channel.")
    @app_commands.describe(channel="Channel where the welcome message should be published")
    async def slash_welcome(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not is_welcome_authorized(interaction.user):
            embed = discord.Embed(
                title="⛔ Authority Clearance Required",
                description="This protocol is restricted to **Administrators**, **Directors**, and **Authority Staff**.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        target_channel = channel or interaction.channel
        embed = self.create_welcome_embed(interaction.guild)

        try:
            await target_channel.send(embed=embed, view=self.portal_view)
            await interaction.followup.send(f"✅ Official server welcome guide deployed to {target_channel.mention}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(f"❌ Missing permissions to send messages in {target_channel.mention}.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Failed to dispatch welcome message: `{e}`", ephemeral=True)

    @commands.command(name="welcome", aliases=["postwelcome", "welcomepanel"])
    async def prefix_welcome(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Deploy the welcome guide: .welcome [#channel]"""
        if not is_welcome_authorized(ctx.author):
            try:
                await ctx.message.delete()
            except Exception:
                pass
            await ctx.send("⛔ **Access Denied:** Only Administrators, Directors, and Authority roles may run this command.", delete_after=5)
            return

        target_channel = channel or ctx.channel
        try:
            await ctx.message.delete()
        except Exception:
            pass

        embed = self.create_welcome_embed(ctx.guild)
        try:
            await target_channel.send(embed=embed, view=self.portal_view)
            if target_channel.id != ctx.channel.id:
                await ctx.send(f"✅ Welcome guide published to {target_channel.mention}.", delete_after=5)
        except discord.Forbidden:
            await ctx.send(f"❌ Edith lacks permissions to post in {target_channel.mention}.", delete_after=5)


async def setup(bot):
    await bot.add_cog(Welcome(bot))

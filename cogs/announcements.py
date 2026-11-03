import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import config


def is_announcement_authorized(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    director_role = getattr(config, "DIRECTOR_ROLE_NAME", "Director").lower()
    allowed_roles = {director_role, "authority", "admin", "administrator", "head mod"}
    return any(r.name.lower() in allowed_roles for r in member.roles)


def get_target_channel(guild: discord.Guild, channel: discord.TextChannel = None) -> discord.TextChannel:
    if channel:
        return channel
    announcement_id = int(getattr(config, "ANNOUNCEMENT_CHANNEL_ID", 0))
    if announcement_id:
        target = guild.get_channel(announcement_id)
        if target:
            return target
    for name in ["announcements", "updates", "news"]:
        target = discord.utils.get(guild.text_channels, name=name)
        if target:
            return target
    return guild.system_channel or guild.text_channels[0]


class BroadcastConfirmView(discord.ui.View):
    def __init__(self, author: discord.Member, target_channel: discord.TextChannel, embed: discord.Embed, ping_str: str = None):
        super().__init__(timeout=180)
        self.author = author
        self.target_channel = target_channel
        self.embed = embed
        self.ping_str = ping_str

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("⛔ Only the author can confirm this broadcast.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Broadcast Now", style=discord.ButtonStyle.success, emoji="🚀")
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)

        allowed_mentions = discord.AllowedMentions(everyone=True, roles=True, users=True)
        content = self.ping_str if self.ping_str else None

        try:
            await self.target_channel.send(content=content, embed=self.embed, allowed_mentions=allowed_mentions)
            await interaction.followup.send(f"✅ Announcement successfully broadcasted to {self.target_channel.mention}!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Broadcast error: `{e}`", ephemeral=True)

    @discord.ui.button(label="Discard", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="🗑️ Announcement preview discarded.", embed=None, view=self)


class AnnouncementModal(discord.ui.Modal):
    announcement_title = discord.ui.TextInput(
        label="Announcement Title",
        placeholder="e.g. Major Server Update & Event Kickoff",
        max_length=150,
        required=True
    )
    announcement_body = discord.ui.TextInput(
        label="Message Content (Supports Markdown)",
        style=discord.TextStyle.paragraph,
        placeholder="Type announcement content here...",
        max_length=3000,
        required=True
    )
    ping_mention = discord.ui.TextInput(
        label="Ping Option (@everyone, @here, Role, None)",
        placeholder="e.g. @everyone or leave blank",
        max_length=80,
        required=False
    )
    banner_url = discord.ui.TextInput(
        label="Banner Image URL (Optional)",
        placeholder="https://example.com/banner.png",
        max_length=300,
        required=False
    )

    def __init__(self, target_channel: discord.TextChannel):
        super().__init__(title="Draft Server Announcement")
        self.target_channel = target_channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title=f"📢 {self.announcement_title.value.strip()}",
            description=self.announcement_body.value.strip(),
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=datetime.now(timezone.utc)
        )

        banner = self.banner_url.value.strip()
        if banner and (banner.startswith("http://") or banner.startswith("https://")):
            embed.set_image(url=banner)
        elif hasattr(config, "COLOR_BANNER") and config.COLOR_BANNER:
            embed.set_image(url=config.COLOR_BANNER)

        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        embed.set_footer(
            text=f"Authorized Broadcast by {interaction.user.display_name} • {interaction.guild.name}",
            icon_url=interaction.user.display_avatar.url
        )

        ping_str = self.ping_mention.value.strip() if self.ping_mention.value else None
        preview_view = BroadcastConfirmView(
            author=interaction.user,
            target_channel=self.target_channel,
            embed=embed,
            ping_str=ping_str
        )

        preview_text = (
            f"🔍 **Announcement Preview Mode**\n"
            f"• **Target Destination:** {self.target_channel.mention}\n"
            f"• **Assigned Ping:** `{ping_str or 'None'}`\n\n"
            f"Review the embed below and click **Broadcast Now** to publish:"
        )

        await interaction.followup.send(content=preview_text, embed=embed, view=preview_view, ephemeral=True)


class Announcements(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="announce", description="Broadcast a formal server announcement (High Authority only).")
    @app_commands.describe(channel="Target channel for announcement")
    async def slash_announce(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not is_announcement_authorized(interaction.user):
            embed = discord.Embed(
                title="⛔ Authority Clearance Required",
                description="This protocol is strictly reserved for **Administrators**, **Directors**, and **Authority Staff**.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        destination = get_target_channel(interaction.guild, channel)
        await interaction.response.send_modal(AnnouncementModal(target_channel=destination))

    @commands.command(name="announce", aliases=["broadcast"])
    async def prefix_announce(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if not is_announcement_authorized(ctx.author):
            try:
                await ctx.message.delete()
            except Exception:
                pass
            await ctx.send("⛔ **Access Denied:** Only Administrators, Directors, and Authority roles may broadcast.", delete_after=5)
            return

        destination = get_target_channel(ctx.guild, channel)
        try:
            await ctx.message.delete()
        except Exception:
            pass

        embed = discord.Embed(
            title="📢 Launch Announcement Studio",
            description=f"Ready to draft an official broadcast for {destination.mention}.\nClick the button below to open the **Draft Studio**.",
            color=discord.Color.blue()
        )

        view = discord.ui.View(timeout=60)
        btn = discord.ui.Button(label="Open Draft Studio", style=discord.ButtonStyle.primary, emoji="📝")

        async def btn_callback(interaction: discord.Interaction):
            if not is_announcement_authorized(interaction.user):
                await interaction.response.send_message("⛔ Authority clearance required.", ephemeral=True)
                return
            await interaction.response.send_modal(AnnouncementModal(target_channel=destination))

        btn.callback = btn_callback
        view.add_item(btn)
        await ctx.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Announcements(bot))

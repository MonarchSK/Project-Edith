import os
import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import config

# --- Pool of Randomized Interview Questions for Team Applications ---
TEAM_QUESTION_POOL = [
    "What motivated you to apply for our staff team?",
    "How many hours per week can you actively dedicate to the server?",
    "How would you handle a member who refuses to stop arguing in public chat?",
    "What prior moderation or community management experience do you possess?",
    "If two staff members disagree in chat, what actions would you take?",
    "What timezone are you located in and during what hours are you most active?",
    "How would you react if an unauthorized user attempts to spam server channels?",
    "Tell us about a time you resolved a conflict peacefully."
]


# --- Helper: Create Isolated, Locked Channel ---
async def create_locked_channel(interaction: discord.Interaction, prefix: str, title: str, fields: list[tuple[str, str]], color: discord.Color):
    """Provisions a private channel visible ONLY to the user, Directors, and Admins."""
    guild = interaction.guild
    member = interaction.user
    timestamp = datetime.now(timezone.utc).strftime("%H%M")
    channel_name = f"{prefix}-{member.name[:10]}-{timestamp}"

    # Strict isolation: @everyone locked out completely
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, read_messages=False),
        member: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, manage_channels=True)
    }

    # Authorize Director role if present in server
    director_role = discord.utils.get(guild.roles, name=config.DIRECTOR_ROLE_NAME)
    if director_role:
        overwrites[director_role] = discord.PermissionOverwrite(
            view_channel=True, read_messages=True, send_messages=True, manage_channels=True
        )

    # Allow administrators and staff roles with manage_channels to view
    for role in guild.roles:
        if role.permissions.administrator or role.name.lower() in ["admin", "administrator", "head mod"]:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True)

    channel = await guild.create_text_channel(
        name=channel_name,
        overwrites=overwrites,
        topic=f"Private Desk // Operative: {member.name} ({member.id})"
    )

    embed = discord.Embed(
        title=title,
        description=f"🔒 **Locked Private Session**\nAuthorized Viewers: {member.mention}, Directors & Server Administrators.\n",
        color=color,
        timestamp=datetime.now(timezone.utc)
    )

    for name, value in fields:
        embed.add_field(name=name, value=value or "*No details provided*", inline=False)

    embed.set_footer(text="Project-Edith • Private Dispatch")

    await channel.send(
        content=f"🔔 Notification: {member.mention}",
        embed=embed,
        view=ActiveTicketControls()
    )
    return channel


# --- 1. General Support Modal ---
class GeneralSupportModal(discord.ui.Modal, title="🎫 General Support Request"):
    subject = discord.ui.TextInput(
        label="Inquiry / Subject",
        placeholder="Brief subject of your question or issue...",
        max_length=100,
        required=True
    )
    details = discord.ui.TextInput(
        label="Details & Information",
        style=discord.TextStyle.paragraph,
        placeholder="Explain what you need assistance with...",
        max_length=1000,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel = await create_locked_channel(
            interaction=interaction,
            prefix="ticket",
            title=f"🎫 Support Ticket: {self.subject.value}",
            fields=[
                ("Requester", interaction.user.mention),
                ("Subject", self.subject.value),
                ("Details", self.details.value)
            ],
            color=discord.Color.blue()
        )
        await interaction.followup.send(f"✅ Your private support channel has been created: {channel.mention}", ephemeral=True)


# --- 2. Team Application Modal with Random Questions ---
class TeamApplicationModal(discord.ui.Modal, title="📝 Staff Team Application"):
    def __init__(self):
        super().__init__()
        # Pick 3 questions randomly from the pool
        selected = random.sample(TEAM_QUESTION_POOL, 3)

        self.age_tz = discord.ui.TextInput(
            label="Age & Preferred Role",
            placeholder="e.g. 19 / Chat Moderator or Event Coordinator",
            max_length=80,
            required=True
        )
        self.q1 = discord.ui.TextInput(
            label=selected[0][:45],
            style=discord.TextStyle.paragraph,
            placeholder="Type your answer...",
            max_length=500,
            required=True
        )
        self.q2 = discord.ui.TextInput(
            label=selected[1][:45],
            style=discord.TextStyle.paragraph,
            placeholder="Type your answer...",
            max_length=500,
            required=True
        )
        self.q3 = discord.ui.TextInput(
            label=selected[2][:45],
            style=discord.TextStyle.paragraph,
            placeholder="Type your answer...",
            max_length=500,
            required=True
        )

        self.add_item(self.age_tz)
        self.add_item(self.q1)
        self.add_item(self.q2)
        self.add_item(self.q3)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel = await create_locked_channel(
            interaction=interaction,
            prefix="apply",
            title=f"📝 Staff Application: {interaction.user.name}",
            fields=[
                ("Applicant", interaction.user.mention),
                ("Profile & Desired Position", self.age_tz.value),
                (f"Question 1: {self.q1.label}", self.q1.value),
                (f"Question 2: {self.q2.label}", self.q2.value),
                (f"Question 3: {self.q3.label}", self.q3.value)
            ],
            color=discord.Color.gold()
        )
        await interaction.followup.send(f"✅ Your staff application has been dispatched: {channel.mention}", ephemeral=True)


# --- 3. Suggestion Modal ---
class SuggestionModal(discord.ui.Modal, title="💡 Community Suggestion"):
    title_input = discord.ui.TextInput(
        label="Suggestion Title",
        placeholder="Short headline for your idea...",
        max_length=100,
        required=True
    )
    details = discord.ui.TextInput(
        label="Suggestion Description",
        style=discord.TextStyle.paragraph,
        placeholder="How would this benefit the server? Provide details...",
        max_length=1000,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel = await create_locked_channel(
            interaction=interaction,
            prefix="suggest",
            title=f"💡 Server Suggestion: {self.title_input.value}",
            fields=[
                ("Submitted By", interaction.user.mention),
                ("Concept", self.title_input.value),
                ("Proposal", self.details.value)
            ],
            color=discord.Color.green()
        )
        await interaction.followup.send(f"✅ Your suggestion room has been initialized: {channel.mention}", ephemeral=True)


# --- 4. Bot Complaints / Issue Modal ---
class BotComplaintModal(discord.ui.Modal, title="⚠️ Bot Complaint / Bug Report"):
    issue_type = discord.ui.TextInput(
        label="Issue Type / Command",
        placeholder="e.g. Birthday Button Error, Color Bug, Downtime",
        max_length=80,
        required=True
    )
    details = discord.ui.TextInput(
        label="Complaint Description",
        style=discord.TextStyle.paragraph,
        placeholder="Describe what occurred, any error messages, or issues...",
        max_length=1000,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel = await create_locked_channel(
            interaction=interaction,
            prefix="complaint",
            title=f"⚠️ Bot Complaint: {self.issue_type.value}",
            fields=[
                ("Filed By", interaction.user.mention),
                ("Error / Problem Focus", self.issue_type.value),
                ("Full Report", self.details.value)
            ],
            color=discord.Color.red()
        )
        await interaction.followup.send(f"✅ Your complaint has been submitted privately: {channel.mention}", ephemeral=True)


# --- Persistent Inside-Channel Controls (Claim / Close) ---
class ActiveTicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Channel", style=discord.ButtonStyle.secondary, emoji="📌", custom_id="claim_ticket_btn")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name[:16]}"
        button.style = discord.ButtonStyle.success
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"📌 {interaction.user.mention} is now handling this channel.")

    @discord.ui.button(label="Close & Delete", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket_btn")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 Deleting private channel in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete(reason="Channel Closed via Ticket Controls")


# --- Main Dashboard Panel View ---
class TicketPluginView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="General Support", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="btn_open_general_ticket", row=0)
    async def support_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GeneralSupportModal())

    @discord.ui.button(label="Apply Team", style=discord.ButtonStyle.success, emoji="📝", custom_id="btn_open_team_apply", row=0)
    async def apply_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TeamApplicationModal())

    @discord.ui.button(label="Suggestion", style=discord.ButtonStyle.secondary, emoji="💡", custom_id="btn_open_suggestion", row=1)
    async def suggest_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SuggestionModal())

    @discord.ui.button(label="Bot Complaints", style=discord.ButtonStyle.danger, emoji="⚠️", custom_id="btn_open_bot_complaint", row=1)
    async def complaint_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BotComplaintModal())


# --- Ticket Cog Definition ---
class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Self-registers persistent views so buttons work across restarts without circular imports."""
        self.bot.add_view(TicketPluginView())
        self.bot.add_view(ActiveTicketControls())

    def create_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🛠️ Server Dispatch & Assistance Hub",
            description=(
                "Choose the appropriate category below to open a private session.\n\n"
                "• **🎫 General Support:** Get direct assistance from server staff.\n"
                "• **📝 Apply Team:** Fill out a staff interview questionnaire.\n"
                "• **💡 Suggestion:** Submit ideas and features for the community.\n"
                "• **⚠️ Bot Complaints:** Privately report bot bugs, failures, or issues."
            ),
            color=discord.Color.from_rgb(88, 101, 242)
        )
        if hasattr(config, "DEFAULT_ICON") and config.DEFAULT_ICON:
            embed.set_thumbnail(url=config.DEFAULT_ICON)
        if hasattr(config, "TICKET_BANNER") and config.TICKET_BANNER:
            embed.set_image(url=config.TICKET_BANNER)

        embed.add_field(
            name="🔒 Privacy & Security",
            value="All channels are locked and restricted exclusively to **you**, the **Directorate**, and **Server Administrators**.",
            inline=False
        )
        embed.set_footer(text="Click a button below to launch a private channel")
        return embed

    @commands.command(name="ticket_panel")
    @commands.has_permissions(administrator=True)
    async def prefix_panel(self, ctx: commands.Context):
        """Deploy the complete Dispatch Hub: .ticket_panel"""
        await ctx.channel.send(embed=self.create_panel_embed(), view=TicketPluginView())
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @app_commands.command(name="ticket_panel", description="Deploy the Server Dispatch & Assistance Hub panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_panel(self, interaction: discord.Interaction):
        await interaction.channel.send(embed=self.create_panel_embed(), view=TicketPluginView())
        await interaction.response.send_message("Dispatch hub deployed.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Tickets(bot))

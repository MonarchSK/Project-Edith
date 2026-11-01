import os
import io
import re
import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import config

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


def get_director_log_channel(guild: discord.Guild):
    """Finds the most suitable channel for archiving transcripts."""
    log_id = int(os.getenv("TICKET_LOGS_CHANNEL_ID", getattr(config, "TICKET_LOGS_CHANNEL_ID", 0)))
    if log_id:
        chan = guild.get_channel(log_id)
        if chan:
            return chan

    for name in ["ticket-logs", "bot-errors", "bot-memory"]:
        chan = discord.utils.get(guild.text_channels, name=name)
        if chan:
            return chan

    category = discord.utils.get(guild.categories, name="🛠️ BACKUPS & CONTROL")
    if category and category.text_channels:
        return category.text_channels[0]

    return None


async def generate_channel_transcript(channel: discord.TextChannel) -> tuple[io.BytesIO, int]:
    """Scrapes message history chronologically and formats into a UTF-8 text file."""
    messages = []
    async for msg in channel.history(limit=1000, oldest_first=True):
        time_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author_str = f"{msg.author} ({msg.author.id})"
        content = msg.content or "[No Text Content]"

        attachments_str = ""
        if msg.attachments:
            att_urls = [a.url for a in msg.attachments]
            attachments_str = f"\n  [Attachments: {' | '.join(att_urls)}]"

        embeds_str = ""
        if msg.embeds:
            embed_summaries = []
            for e in msg.embeds:
                title = e.title or "Untitled"
                embed_summaries.append(f"Embed: {title}")
            embeds_str = f"\n  [{' | '.join(embed_summaries)}]"

        messages.append(f"[{time_str}] {author_str}:\n  {content}{attachments_str}{embeds_str}\n")

    transcript_text = (
        f"=====================================================\n"
        f"TRANSCRIPT FOR CHANNEL: #{channel.name}\n"
        f"SERVER: {channel.guild.name} ({channel.guild.id})\n"
        f"EXPORTED AT: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"TOTAL ARCHIVED MESSAGES: {len(messages)}\n"
        f"=====================================================\n\n"
        + "\n".join(messages)
    )

    buffer = io.BytesIO(transcript_text.encode("utf-8"))
    buffer.seek(0)
    return buffer, len(messages)


def extract_creator_id(topic: str) -> int | None:
    if not topic:
        return None
    match = re.search(r"\((\d{15,21})\)", topic)
    return int(match.group(1)) if match else None


async def create_locked_channel(interaction: discord.Interaction, prefix: str, title: str, fields: list[tuple[str, str]], color: discord.Color):
    """Provisions a private locked channel, alerts Directors, and provides active controls."""
    guild = interaction.guild
    member = interaction.user
    timestamp = datetime.now(timezone.utc).strftime("%H%M")
    channel_name = f"{prefix}-{member.name[:10]}-{timestamp}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, read_messages=False),
        member: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, manage_channels=True)
    }

    director_role = discord.utils.get(guild.roles, name=config.DIRECTOR_ROLE_NAME)
    if director_role:
        overwrites[director_role] = discord.PermissionOverwrite(
            view_channel=True, read_messages=True, send_messages=True, manage_channels=True
        )

    for role in guild.roles:
        if role.permissions.administrator or role.name.lower() in ["admin", "administrator", "head mod"]:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True)

    channel = await guild.create_text_channel(
        name=channel_name,
        overwrites=overwrites,
        topic=f"Private Desk // Creator: {member.name} ({member.id})"
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

    notification_content = f"🔔 Notification: {member.mention}"
    if director_role:
        notification_content += f" | {director_role.mention}"

    await channel.send(
        content=notification_content,
        embed=embed,
        view=ActiveTicketControls(),
        allowed_mentions=discord.AllowedMentions(roles=True, users=True)
    )
    return channel


# --- Modals ---
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


class TeamApplicationModal(discord.ui.Modal, title="📝 Staff Team Application"):
    def __init__(self):
        super().__init__()
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


# --- Persistent Inside-Channel Controls ---
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
        await interaction.response.defer()
        channel = interaction.channel

        await channel.send("📑 Compiling full audit transcript and dispatching records...")

        buffer, count = await generate_channel_transcript(channel)
        raw_bytes = buffer.getvalue()
        filename = f"transcript-{channel.name}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt"

        dm_delivered = False
        creator_id = extract_creator_id(channel.topic)
        if creator_id:
            target_user = interaction.guild.get_member(creator_id)
            if not target_user:
                try:
                    target_user = await interaction.client.fetch_user(creator_id)
                except Exception:
                    target_user = None

            if target_user:
                user_file = discord.File(io.BytesIO(raw_bytes), filename=filename)
                user_embed = discord.Embed(
                    title="📑 Your Ticket Has Been Closed",
                    description=(
                        f"Hello **{target_user.name}**, your session in **#{channel.name}** "
                        f"on **{interaction.guild.name}** has been marked as closed.\n\n"
                        f"A full transcript of your conversation is attached below for your records."
                    ),
                    color=discord.Color.from_rgb(88, 101, 242),
                    timestamp=datetime.now(timezone.utc)
                )
                user_embed.add_field(name="Closed By", value=interaction.user.name, inline=True)
                user_embed.add_field(name="Archived Messages", value=f"`{count}` messages", inline=True)
                user_embed.set_footer(text=f"{interaction.guild.name} • Support Records")

                try:
                    await target_user.send(embed=user_embed, file=user_file)
                    dm_delivered = True
                except discord.Forbidden:
                    dm_delivered = False
                except Exception as e:
                    print(f"[USER DM TRANSCRIPT ERROR] {e}")
                    dm_delivered = False

        log_channel = get_director_log_channel(interaction.guild)
        if log_channel:
            staff_file = discord.File(io.BytesIO(raw_bytes), filename=filename)
            log_embed = discord.Embed(
                title="🗄️ Closed Ticket Archive",
                description="A private session has been finalized and purged from the server.",
                color=discord.Color.dark_grey(),
                timestamp=datetime.now(timezone.utc)
            )
            log_embed.add_field(name="Channel Name", value=f"`#{channel.name}`", inline=True)
            log_embed.add_field(name="Closed By", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Archived Messages", value=f"`{count}` messages", inline=True)
            log_embed.add_field(
                name="Creator DM Status",
                value="✅ Successfully Delivered" if dm_delivered else "⚠️ Not Delivered (DMs Closed / User Left)",
                inline=True
            )
            log_embed.add_field(name="Channel Topic", value=channel.topic or "*None*", inline=False)
            log_embed.set_footer(text="Project-Edith • Security Audit Vault")

            try:
                await log_channel.send(embed=log_embed, file=staff_file)
            except Exception as e:
                print(f"[STAFF LOG DISPATCH ERROR] {e}")

        await channel.send("🔒 **Archive complete.** Deleting channel in 5 seconds...")
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Closed by {interaction.user} (Transcript archived & DM dispatched)")
        except Exception:
            pass


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


# --- Cog Definition ---
class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_view = TicketPluginView()

    async def cog_load(self):
        """Self-registers persistent views once so buttons work across restarts without duplicates."""
        self.bot.add_view(self.active_view)
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
            value=(
                "All channels are restricted exclusively to **you**, the **Directorate**, and **Server Administrators**.\n"
                "Upon closing, an audit transcript is logged to staff records and a copy is sent to your DMs."
            ),
            inline=False
        )
        embed.set_footer(text="Click a button below to launch a private channel")
        return embed

    @commands.command(name="ticket_panel")
    @commands.has_permissions(administrator=True)
    async def prefix_panel(self, ctx: commands.Context):
        """Deploy the complete Dispatch Hub: .ticket_panel"""
        embed = self.create_panel_embed()
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await ctx.channel.send(embed=embed, view=self.active_view)

    @app_commands.command(name="ticket_panel", description="Deploy the Server Dispatch & Assistance Hub panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_panel(self, interaction: discord.Interaction):
        # 1. Immediately acknowledge interaction to extend deadline
        await interaction.response.defer(ephemeral=True)

        embed = self.create_panel_embed()
        # 2. Send the panel message to the current channel
        await interaction.channel.send(embed=embed, view=self.active_view)
        # 3. Use followup.send (NEVER interaction.response.send_message after defer)
        await interaction.followup.send("Dispatch hub deployed successfully.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Tickets(bot))

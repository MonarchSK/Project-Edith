import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import config

class TicketModal(discord.ui.Modal, title="🎫 New Support Ticket"):
    subject = discord.ui.TextInput(label="Reason / Subject", placeholder="What do you need assistance with?", max_length=100, required=True)
    details = discord.ui.TextInput(label="Inquiry Details", style=discord.TextStyle.paragraph, placeholder="Explain with details...", required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, read_messages=False),
            member: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, manage_channels=True)
        }

        channel = await guild.create_text_channel(name=f"ticket-{member.name}", overwrites=overwrites, reason=f"Ticket for {member.name}")
        embed = discord.Embed(
            title=f"🎫 Ticket: {self.subject.value}",
            description=f"**Opened By:** {member.mention}\n\n**Details:**\n{self.details.value or 'None provided.'}",
            color=discord.Color.blurple()
        )
        await channel.send(embed=embed, view=ActiveTicketControls())
        await interaction.response.send_message(f"Ticket opened: {channel.mention}", ephemeral=True)

class ActiveTicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.secondary, emoji="📌", custom_id="claim_ticket_btn")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        button.style = discord.ButtonStyle.success
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"📌 {interaction.user.mention} has claimed this ticket.")

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket_btn")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 Deleting ticket channel in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete(reason="Ticket Closed")

class TicketPluginView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="plugin_open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal())

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def create_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🧩 Support Desk Plugin",
            description="Have a question or report? Click below to launch an interactive support ticket.",
            color=discord.Color.from_rgb(88, 101, 242)
        )
        embed.set_thumbnail(url=config.DEFAULT_ICON)
        embed.set_image(url=config.TICKET_BANNER)
        embed.add_field(name="Privacy", value="• Isolated channels\n• Staff-only visibility", inline=True)
        embed.add_field(name="Response Speed", value="• Fast review\n• Claim tracking", inline=True)
        return embed

    @commands.command(name="ticket_panel")
    @commands.has_permissions(administrator=True)
    async def prefix_panel(self, ctx: commands.Context):
        await ctx.channel.send(embed=self.create_panel_embed(), view=TicketPluginView())
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @app_commands.command(name="ticket_panel", description="Deploy the Support Ticket Plugin.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_panel(self, interaction: discord.Interaction):
        await interaction.channel.send(embed=self.create_panel_embed(), view=TicketPluginView())
        await interaction.response.send_message("Ticket plugin deployed.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Tickets(bot))

import discord
from discord import app_commands
from discord.ext import commands
import config

class ConfessionModal(discord.ui.Modal, title="🤫 Anonymous Confession"):
    text = discord.ui.TextInput(
        label="Your Message",
        style=discord.TextStyle.paragraph,
        placeholder="Type here... Author identity is completely stripped.",
        required=True,
        max_length=1500
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        async with self.bot.db.execute("INSERT INTO confessions (content) VALUES (?)", (self.text.value,)) as cursor:
            cid = cursor.lastrowid
        await self.bot.db.commit()

        channel = interaction.guild.get_channel(config.CONFESSION_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("❌ Target confession channel is not found.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🤫 Confession #{cid}",
            description=self.text.value,
            color=discord.Color.from_rgb(113, 54, 186)
        )
        embed.set_footer(text="Anonymous Submission • Database Logged")
        await channel.send(embed=embed)
        await interaction.response.send_message("✅ Your confession was posted anonymously!", ephemeral=True)

class ConfessionPluginView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Submit Confession", style=discord.ButtonStyle.secondary, emoji="🤫", custom_id="plugin_confess_btn")
    async def submit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ConfessionModal(self.bot))

class Confessions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def create_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🤫 Confessions Booth Plugin",
            description="Share your thoughts anonymously with the community. Click below to launch the modal form.",
            color=discord.Color.from_rgb(113, 54, 186)
        )
        embed.set_thumbnail(url=config.DEFAULT_ICON)
        embed.set_image(url=config.CONFESSION_BANNER)
        embed.add_field(name="Security", value="• 100% Identity scrubbed\n• Auto-sequential numbering", inline=False)
        return embed

    @commands.command(name="confession_panel")
    @commands.has_permissions(administrator=True)
    async def prefix_panel(self, ctx: commands.Context):
        await ctx.channel.send(embed=self.create_panel_embed(), view=ConfessionPluginView(self.bot))
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @app_commands.command(name="confession_panel", description="Deploy the Confession Booth Plugin.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_panel(self, interaction: discord.Interaction):
        await interaction.channel.send(embed=self.create_panel_embed(), view=ConfessionPluginView(self.bot))
        await interaction.response.send_message("Confession panel deployed.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Confessions(bot))

import os
import asyncio
from datetime import datetime, timezone
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

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not hasattr(self.cog.bot, "db") or not self.cog.bot.db:
            await interaction.followup.send("⚠️ Database connection is still initializing. Please try again shortly.", ephemeral=True)
            return

        # Ensure the target confessions table exists
        await self.cog.init_db()

        content = self.text.value.strip()
        cid = None
        try:
            async with self.cog.bot.db.execute("INSERT INTO confessions (content) VALUES (?)", (content,)) as cursor:
                cid = cursor.lastrowid
            await self.cog.bot.db.commit()
        except Exception as e:
            print(f"[CONFESSION DB ERROR] {e}")
            await interaction.followup.send(f"⚠️ Failed to save confession to database: `{e}`", ephemeral=True)
            return

        # Notify backup engine of database mutation
        backup_cog = self.cog.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "mark_dirty"):
            backup_cog.mark_dirty(reason="Anonymous Confession Logged")

        target_channel_id = int(os.getenv("CONFESSION_CHANNEL_ID", getattr(config, "CONFESSION_CHANNEL_ID", 0)))
        channel = interaction.guild.get_channel(target_channel_id)
        if not channel:
            try:
                channel = await self.cog.bot.fetch_channel(target_channel_id)
            except Exception:
                channel = None

        if not channel or not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("❌ Target confession channel could not be found or is not configured.", ephemeral=True)
            return

        # Permission check
        perms = channel.permissions_for(interaction.guild.me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.followup.send(f"❌ Missing permissions (`Send Messages` or `Embed Links`) in {channel.mention}.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🤫 Confession #{cid}",
            description=content,
            color=discord.Color.from_rgb(113, 54, 186),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Anonymous Submission • Database Logged")

        try:
            await channel.send(embed=embed)
            await interaction.followup.send("✅ Your confession was posted anonymously!", ephemeral=True)
        except Exception as e:
            print(f"[CONFESSION DISPATCH ERROR] {e}")
            await interaction.followup.send(f"⚠️ Failed to deliver confession to channel: `{e}`", ephemeral=True)


class ConfessionPluginView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Submit Confession",
        style=discord.ButtonStyle.secondary,
        emoji="🤫",
        custom_id="plugin_confess_btn"
    )
    async def submit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ConfessionModal(self.cog))


class Confessions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_view = ConfessionPluginView(self)

    async def cog_load(self):
        """Self-registers persistent view once to preserve interactive buttons across bot reboots."""
        self.bot.add_view(self.active_view)
        if hasattr(self.bot, "db") and self.bot.db:
            await self.init_db()

    async def init_db(self):
        """Ensures the confessions table is created in SQLite."""
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL
            )
        """)
        await self.bot.db.commit()

    def create_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🤫 Confessions Booth Plugin",
            description="Share your thoughts anonymously with the community. Click below to launch the modal form.",
            color=discord.Color.from_rgb(113, 54, 186)
        )
        if getattr(config, "DEFAULT_ICON", None):
            embed.set_thumbnail(url=config.DEFAULT_ICON)
        if getattr(config, "CONFESSION_BANNER", None):
            embed.set_image(url=config.CONFESSION_BANNER)
            
        embed.add_field(name="Security", value="• 100% Identity scrubbed\n• Auto-sequential numbering", inline=False)
        embed.set_footer(text="Confessions Booth • Anonymous & Secure")
        return embed

    @commands.command(name="confession_panel")
    @commands.has_permissions(administrator=True)
    async def prefix_panel(self, ctx: commands.Context):
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await ctx.channel.send(embed=self.create_panel_embed(), view=self.active_view)

    @app_commands.command(name="confession_panel", description="Deploy the Confession Booth Plugin.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_panel(self, interaction: discord.Interaction):
        await interaction.channel.send(embed=self.create_panel_embed(), view=self.active_view)
        await interaction.response.send_message("Confession panel deployed.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Confessions(bot))

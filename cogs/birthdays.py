import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone
import config

class BirthdayModal(discord.ui.Modal, title="🎂 Register Birthday"):
    day = discord.ui.TextInput(label="Day of Birth (1-31)", placeholder="15", min_length=1, max_length=2, required=True)
    month = discord.ui.TextInput(label="Month of Birth (1-12)", placeholder="8", min_length=1, max_length=2, required=True)

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        try:
            d = int(self.day.value.strip())
            m = int(self.month.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Day and month must be numbers.", ephemeral=True)
            return

        if not (1 <= d <= 31 and 1 <= m <= 12):
            await interaction.response.send_message("❌ Invalid date. Enter valid day (1-31) and month (1-12).", ephemeral=True)
            return

        await self.bot.db.execute("INSERT OR REPLACE INTO birthdays (user_id, day, month) VALUES (?, ?, ?)", (interaction.user.id, d, m))
        await self.bot.db.commit()
        await interaction.response.send_message(f"🎉 Birthday registered for **{d}/{m}**!", ephemeral=True)

class BirthdayPluginView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Set Birthday", style=discord.ButtonStyle.primary, emoji="🎂", custom_id="plugin_set_bday")
    async def set_bday(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BirthdayModal(self.bot))

    @discord.ui.button(label="Check Saved", style=discord.ButtonStyle.secondary, emoji="🔍", custom_id="plugin_check_bday")
    async def check_bday(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.bot.db.execute("SELECT day, month FROM birthdays WHERE user_id = ?", (interaction.user.id,)) as cursor:
            row = await cursor.fetchone()
        if row:
            await interaction.response.send_message(f"📅 Your saved birthday: **{row[0]}/{row[1]}**", ephemeral=True)
        else:
            await interaction.response.send_message("❓ No saved birthday found. Use **Set Birthday** to register.", ephemeral=True)

class Birthdays(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_birthdays.start()

    def cog_unload(self):
        self.check_birthdays.cancel()

    def create_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎂 Birthday Studio Plugin",
            description="Register your special day to receive an automated shoutout at midnight!",
            color=discord.Color.from_rgb(235, 69, 158)
        )
        embed.set_thumbnail(url=config.DEFAULT_ICON)
        embed.set_image(url=config.BDAY_BANNER)
        embed.add_field(name="Features", value="• Pop-up dialog\n• Automated midnight checks\n• Permanent cloud sync", inline=False)
        return embed

    @tasks.loop(hours=24)
    async def check_birthdays(self):
        now = datetime.now(timezone.utc)
        async with self.bot.db.execute("SELECT user_id FROM birthdays WHERE day = ? AND month = ?", (now.day, now.month)) as cursor:
            users = await cursor.fetchall()
        if not users or not config.ANNOUNCEMENT_CHANNEL_ID:
            return

        channel = self.bot.get_channel(config.ANNOUNCEMENT_CHANNEL_ID)
        if channel:
            mentions = " ".join([f"<@{row[0]}>" for row in users])
            await channel.send(f"🎉 Happy Birthday to {mentions}! Have a fantastic day! 🎂")

    @check_birthdays.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @commands.command(name="birthday_panel")
    @commands.has_permissions(administrator=True)
    async def prefix_panel(self, ctx: commands.Context):
        await ctx.channel.send(embed=self.create_panel_embed(), view=BirthdayPluginView(self.bot))
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @app_commands.command(name="birthday_panel", description="Deploy the Birthday Plugin.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_panel(self, interaction: discord.Interaction):
        await interaction.channel.send(embed=self.create_panel_embed(), view=BirthdayPluginView(self.bot))
        await interaction.response.send_message("Birthday panel deployed.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Birthdays(bot))

import random
import asyncio
from datetime import datetime, date, timezone
import discord
from discord import app_commands
from discord.ext import commands, tasks
import config

RANDOM_BIRTHDAY_WISHES = [
    "Wishing you a year filled with happiness, health, and endless success! Have an amazing birthday! 🎂🎉",
    "May your day be as bright, joyful, and wonderful as you are! Happy Birthday! ✨🎈",
    "Another year older, wiser, and more incredible! Enjoy every moment of your special day! 🥳🍰",
    "Sending you warm smiles and the best vibes on your birthday! Have a blast! 🎊🎁",
    "May all your dreams and wishes come true this year. Happy Birthday to a valued member! 🌟🎂",
    "Cheers to another full trip around the sun! Hope your day is filled with cake and celebration! 🎈🥂",
    "Wishing you joy, great company, and lots of memorable moments today. Happy Birthday! 🎉💖",
    "May your upcoming year bring new opportunities, great memories, and lots of laughter! 🍰🎁",
    "Happy Birthday! Thank you for being such an awesome part of our server community! 🥳✨",
    "Here’s to celebrating you today! Eat plenty of cake and enjoy your special day to the fullest! 🎂🎊"
]

def get_days_until(day: int, month: int) -> int:
    today = date.today()
    try:
        target_this_year = date(today.year, month, day)
    except ValueError:
        target_this_year = date(today.year, month, 28)

    if target_this_year >= today:
        return (target_this_year - today).days
    else:
        try:
            target_next_year = date(today.year + 1, month, day)
        except ValueError:
            target_next_year = date(today.year + 1, month, 28)
        return (target_next_year - today).days


class CleanBirthdayModal(discord.ui.Modal, title="Register Your Birthday"):
    day = discord.ui.TextInput(
        label="Day of Birth (1-31)",
        placeholder="e.g. 15",
        min_length=1,
        max_length=2,
        required=True
    )
    month = discord.ui.TextInput(
        label="Month of Birth (1-12)",
        placeholder="e.g. 8 for August",
        min_length=1,
        max_length=2,
        required=True
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            d = int(self.day.value.strip())
            m = int(self.month.value.strip())
        except ValueError:
            await interaction.followup.send("❌ Day and month must be numbers.", ephemeral=True)
            return

        if not (1 <= d <= 31 and 1 <= m <= 12):
            await interaction.followup.send("❌ Please enter a valid day (1-31) and month (1-12).", ephemeral=True)
            return

        user_name = interaction.user.display_name
        await self.cog.save_birthday(interaction.user.id, user_name, d, m)
        days_left = get_days_until(d, m)

        countdown_text = "🎉 **Your birthday is TODAY!**" if days_left == 0 else f"**{days_left}** days remaining"

        await interaction.followup.send(
            f"✅ **Birthday Registered!**\n"
            f"• **Member:** {interaction.user.mention}\n"
            f"• **Date:** `{d:02d}/{m:02d}`\n"
            f"• **Countdown:** {countdown_text}\n"
            f"• *Database snapshot pushed to `#bot-memory`.*",
            ephemeral=True
        )


class MasterSetModal(discord.ui.Modal):
    def __init__(self, cog, target_member: discord.Member):
        super().__init__(title=f"Set Birthday: {target_member.name[:16]}")
        self.cog = cog
        self.target_member = target_member

        self.day = discord.ui.TextInput(
            label="Day (1-31)",
            placeholder="e.g. 24",
            min_length=1,
            max_length=2,
            required=True
        )
        self.month = discord.ui.TextInput(
            label="Month (1-12)",
            placeholder="e.g. 10",
            min_length=1,
            max_length=2,
            required=True
        )
        self.add_item(self.day)
        self.add_item(self.month)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            d = int(self.day.value.strip())
            m = int(self.month.value.strip())
        except ValueError:
            await interaction.followup.send("❌ Values must be numbers.", ephemeral=True)
            return

        if not (1 <= d <= 31 and 1 <= m <= 12):
            await interaction.followup.send("❌ Invalid date values.", ephemeral=True)
            return

        await self.cog.save_birthday(self.target_member.id, self.target_member.display_name, d, m)
        days_left = get_days_until(d, m)

        await interaction.followup.send(
            f"✅ **Birthday Added for {self.target_member.mention}!**\n"
            f"• **Date:** `{d:02d}/{m:02d}`\n"
            f"• **Days Left:** **{days_left}** days\n"
            f"• *Database snapshot pushed to `#bot-memory`.*",
            ephemeral=True
        )


class DirectorMemberSelect(discord.ui.UserSelect):
    def __init__(self, cog):
        super().__init__(
            placeholder="Choose a server member to set their birthday...",
            min_values=1,
            max_values=1
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        selected_user = self.values[0]
        member = interaction.guild.get_member(selected_user.id) or selected_user
        await interaction.response.send_modal(MasterSetModal(self.cog, member))


class MasterUserPickerView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=60)
        self.add_item(DirectorMemberSelect(cog))


class CleanBirthdayView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Set My Birthday",
        style=discord.ButtonStyle.primary,
        emoji="🎂",
        custom_id="bday_set_clean"
    )
    async def set_bday(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CleanBirthdayModal(self.cog))

    @discord.ui.button(
        label="Check Birthday",
        style=discord.ButtonStyle.secondary,
        emoji="🔍",
        custom_id="bday_check_clean"
    )
    async def check_bday(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        try:
            if not self.cog.bot.db:
                await interaction.followup.send("⚠️ Database connection initializing. Try again in a few seconds.", ephemeral=True)
                return

            await self.cog.bot.db.execute("""
                CREATE TABLE IF NOT EXISTS birthdays (
                    user_id INTEGER PRIMARY KEY,
                    user_name TEXT,
                    day INTEGER NOT NULL,
                    month INTEGER NOT NULL
                )
            """)
            await self.cog.bot.db.commit()

            async with self.cog.bot.db.execute(
                "SELECT user_name, day, month FROM birthdays WHERE user_id = ?",
                (interaction.user.id,)
            ) as cursor:
                record = await cursor.fetchone()

            if record:
                name, d, m = record[0] or interaction.user.display_name, record[1], record[2]
                days_left = get_days_until(d, m)
                countdown_text = "🎉 **TODAY IS YOUR BIRTHDAY!**" if days_left == 0 else f"**{days_left}** days remaining"

                embed = discord.Embed(
                    title=f"🎂 Birthday Details: {name}",
                    color=discord.Color.blue()
                )
                embed.add_field(name="Date", value=f"`{d:02d}/{m:02d}`", inline=True)
                embed.add_field(name="Countdown", value=countdown_text, inline=True)
                embed.set_footer(text="Registered in server database")
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(
                    "❓ You haven't registered your birthday yet. Click **Set My Birthday** to add it!",
                    ephemeral=True
                )
        except Exception as e:
            print(f"[CHECK BDAY ERROR] {e}")
            await interaction.followup.send(f"⚠️ Error checking birthday: `{e}`", ephemeral=True)

    @discord.ui.button(
        label="Master Register",
        style=discord.ButtonStyle.danger,
        emoji="👑",
        custom_id="bday_master_clean"
    )
    async def master_override(self, interaction: discord.Interaction, button: discord.ui.Button):
        has_director = any(r.name == config.DIRECTOR_ROLE_NAME for r in interaction.user.roles)
        if not (has_director or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("⛔ Only Directors and Admins can use Master Register.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Select a member from the dropdown below:",
            view=MasterUserPickerView(self.cog),
            ephemeral=True
        )


class Birthdays(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_birthdays.start()

    def cog_unload(self):
        self.check_birthdays.cancel()

    async def trigger_auto_backup(self, trigger_user: str):
        backup_cog = self.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "upload_backup_and_prune"):
            asyncio.create_task(
                backup_cog.upload_backup_and_prune(reason=f"Auto-Sync: Birthday saved for {trigger_user}")
            )

    async def save_birthday(self, user_id: int, user_name: str, day: int, month: int):
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS birthdays (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                day INTEGER NOT NULL,
                month INTEGER NOT NULL
            )
        """)

        await self.bot.db.execute("""
            INSERT INTO birthdays (user_id, user_name, day, month)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                user_name = excluded.user_name,
                day = excluded.day,
                month = excluded.month
        """, (user_id, user_name, day, month))
        await self.bot.db.commit()

        await self.trigger_auto_backup(user_name)

    async def create_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎂 Register the birthday",
            description="Select an option below to register your birthday or view your countdown.",
            color=discord.Color.from_rgb(235, 69, 158)
        )
        if config.BDAY_BANNER:
            embed.set_image(url=config.BDAY_BANNER)

        roster_lines = []
        try:
            async with self.bot.db.execute("SELECT user_id, user_name, day, month FROM birthdays") as cursor:
                records = await cursor.fetchall()
                if records:
                    enriched = []
                    for r in records:
                        days = get_days_until(r[2], r[3])
                        enriched.append((days, r[0], r[1], r[2], r[3]))
                    enriched.sort(key=lambda x: x[0])

                    for days, uid, name, d, m in enriched[:5]:
                        badge = "🎉 Today!" if days == 0 else f"in {days} days"
                        roster_lines.append(f"• **{name or 'Member'}** (<@{uid}>) — `{d:02d}/{m:02d}` ({badge})")
        except Exception:
            pass

        if roster_lines:
            embed.add_field(name="Upcoming Birthdays", value="\n".join(roster_lines), inline=False)

        embed.set_footer(text="Click 'Set My Birthday' to get started")
        return embed

    @tasks.loop(hours=24)
    async def check_birthdays(self):
        now = datetime.now(timezone.utc)
        async with self.bot.db.execute(
            "SELECT user_id, user_name FROM birthdays WHERE day = ? AND month = ?",
            (now.day, now.month)
        ) as cursor:
            celebrants = await cursor.fetchall()

        if not celebrants or not config.ANNOUNCEMENT_CHANNEL_ID:
            return

        channel = self.bot.get_channel(config.ANNOUNCEMENT_CHANNEL_ID)
        if channel:
            for uid, name in celebrants:
                random_wish = random.choice(RANDOM_BIRTHDAY_WISHES)
                embed = discord.Embed(
                    title="🎉 Happy Birthday!",
                    description=f"Happy Birthday to **{name}** (<@{uid}>)!",
                    color=discord.Color.gold()
                )
                embed.add_field(
                    name="A Special Birthday Wish For You",
                    value=f"> *\"{random_wish}\"*",
                    inline=False
                )
                await channel.send(content=f"🎂 <@{uid}>", embed=embed)

    @check_birthdays.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @commands.command(name="birthday_panel")
    @commands.has_permissions(administrator=True)
    async def prefix_panel(self, ctx: commands.Context):
        embed = await self.create_panel_embed()
        await ctx.channel.send(embed=embed, view=CleanBirthdayView(self))
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @app_commands.command(name="birthday_panel", description="Deploy the clean Birthday Panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_panel(self, interaction: discord.Interaction):
        embed = await self.create_panel_embed()
        await interaction.channel.send(embed=embed, view=CleanBirthdayView(self))
        await interaction.response.send_message("Birthday panel deployed.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Birthdays(bot))

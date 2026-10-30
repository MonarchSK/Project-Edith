import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone
import config

class BirthdayModal(discord.ui.Modal, title="🎂 Register Your Birthday"):
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
        try:
            d = int(self.day.value.strip())
            m = int(self.month.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Day and month must be numbers.", ephemeral=True)
            return

        if not (1 <= d <= 31 and 1 <= m <= 12):
            await interaction.response.send_message("❌ Invalid date. Enter a valid day (1-31) and month (1-12).", ephemeral=True)
            return

        user_name = interaction.user.display_name
        await self.cog.save_birthday(interaction.user.id, user_name, d, m)

        await interaction.response.send_message(
            f"🎉 Success, **{user_name}**! Your birthday has been saved for **{d}/{m}**.",
            ephemeral=True
        )

class MasterBirthdayModal(discord.ui.Modal, title="👑 Director: Manual Birthday Entry"):
    target_user = discord.ui.TextInput(
        label="Target User (ID or @mention)",
        placeholder="e.g. 123456789012345678 or @Username",
        required=True
    )
    day = discord.ui.TextInput(
        label="Day of Birth (1-31)",
        placeholder="e.g. 24",
        min_length=1,
        max_length=2,
        required=True
    )
    month = discord.ui.TextInput(
        label="Month of Birth (1-12)",
        placeholder="e.g. 12",
        min_length=1,
        max_length=2,
        required=True
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        raw_target = self.target_user.value.strip().replace("<@", "").replace(">", "").replace("!", "")
        try:
            user_id = int(raw_target)
        except ValueError:
            await interaction.response.send_message("❌ Invalid User ID. Please provide a numeric Discord ID or tag.", ephemeral=True)
            return

        try:
            d = int(self.day.value.strip())
            m = int(self.month.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Day and month must be numbers.", ephemeral=True)
            return

        if not (1 <= d <= 31 and 1 <= m <= 12):
            await interaction.response.send_message("❌ Invalid date values.", ephemeral=True)
            return

        # Attempt to resolve member name
        member = interaction.guild.get_member(user_id)
        user_name = member.display_name if member else f"User {user_id}"

        await self.cog.save_birthday(user_id, user_name, d, m)
        await interaction.response.send_message(
            f"✅ **Master Override:** Saved birthday for <@{user_id}> (**{user_name}**) as **{d}/{m}**.",
            ephemeral=True
        )

class BirthdayPluginView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Set My Birthday", style=discord.ButtonStyle.primary, emoji="🎂", custom_id="plugin_set_bday")
    async def set_bday(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BirthdayModal(self.cog))

    @discord.ui.button(label="Check Saved", style=discord.ButtonStyle.secondary, emoji="🔍", custom_id="plugin_check_bday")
    async def check_bday(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.cog.bot.db.execute(
            "SELECT user_name, day, month FROM birthdays WHERE user_id = ?",
            (interaction.user.id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            name, d, m = row[0], row[1], row[2]
            await interaction.response.send_message(
                f"📅 **{name}**, your registered birthday is **{d}/{m}**.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❓ You haven't registered your birthday yet. Click **Set My Birthday** to add it!",
                ephemeral=True
            )

    @discord.ui.button(label="Master Register", style=discord.ButtonStyle.danger, emoji="👑", custom_id="plugin_master_bday")
    async def master_register(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Allow only Director role or administrators
        has_director = any(r.name == config.DIRECTOR_ROLE_NAME for r in interaction.user.roles)
        if not (has_director or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("⛔ Access Denied: Only Directors can manually register birthdays for others.", ephemeral=True)
            return

        await interaction.response.send_modal(MasterBirthdayModal(self.cog))

class Birthdays(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_birthdays.start()

    def cog_unload(self):
        self.check_birthdays.cancel()

    async def save_birthday(self, user_id: int, user_name: str, day: int, month: int):
        """Inserts or updates birthday records with name tracking."""
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS birthdays (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                day INTEGER NOT NULL,
                month INTEGER NOT NULL
            )
        """)
        # In case the table already existed without user_name column, run safe column alter
        try:
            await self.bot.db.execute("ALTER TABLE birthdays ADD COLUMN user_name TEXT")
        except Exception:
            pass

        await self.bot.db.execute("""
            INSERT INTO birthdays (user_id, user_name, day, month)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                user_name = excluded.user_name,
                day = excluded.day,
                month = excluded.month
        """, (user_id, user_name, day, month))
        await self.bot.db.commit()

    async def create_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎂 Birthday Studio Plugin",
            description="Register your special day to receive an automated shoutout in the community!\n\n"
                        "Click **Set My Birthday** to open your personal registration modal.",
            color=discord.Color.from_rgb(235, 69, 158)
        )
        if config.DEFAULT_ICON:
            embed.set_thumbnail(url=config.DEFAULT_ICON)
        if config.BDAY_BANNER:
            embed.set_image(url=config.BDAY_BANNER)

        # Retrieve the latest 5 registrations to show on the panel
        recent_text = "No birthdays registered yet."
        try:
            async with self.bot.db.execute(
                "SELECT user_id, user_name, day, month FROM birthdays ORDER BY rowid DESC LIMIT 5"
            ) as cursor:
                records = await cursor.fetchall()
                if records:
                    recent_text = "\n".join([
                        f"• **{row[1] or 'Member'}** (<@{row[0]}>) — `{row[2]}/{row[3]}`"
                        for row in records
                    ])
        except Exception:
            pass

        embed.add_field(name="📋 Recent Registrations", value=recent_text, inline=False)
        embed.add_field(
            name="👑 Master Controls",
            value=f"Directors can click **Master Register** or run `.{config.DIRECTOR_ROLE_NAME.lower()}setbday` to add others.",
            inline=False
        )
        embed.set_footer(text="Project-Edith • Cloud SQLite Synchronized")
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
            names = ", ".join([f"**{row[1]}** (<@{row[0]}>)" for row in celebrants])
            await channel.send(f"🎉 Today is a special day! Happy Birthday to {names}! 🎂🎈")

    @check_birthdays.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    # --- Director Master Command (Prefix) ---
    @commands.command(name="setuserbday")
    @commands.has_permissions(administrator=True)
    async def prefix_set_user_bday(self, ctx: commands.Context, member: discord.Member, day: int, month: int):
        """Master command: .setuserbday @user 15 8"""
        if not (1 <= day <= 31 and 1 <= month <= 12):
            await ctx.send("❌ Invalid day (1-31) or month (1-12).")
            return
        await self.save_birthday(member.id, member.display_name, day, month)
        await ctx.send(f"✅ Registered birthday for **{member.display_name}** as **{day}/{month}**.")

    # --- Director Master Command (Slash) ---
    @app_commands.command(name="setuserbday", description="Director command to register a birthday for any member.")
    @app_commands.describe(member="The member to register", day="Day (1-31)", month="Month (1-12)")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_set_user_bday(self, interaction: discord.Interaction, member: discord.Member, day: int, month: int):
        if not (1 <= day <= 31 and 1 <= month <= 12):
            await interaction.response.send_message("❌ Invalid day (1-31) or month (1-12).", ephemeral=True)
            return
        await self.save_birthday(member.id, member.display_name, day, month)
        await interaction.response.send_message(f"✅ Registered birthday for {member.mention} (**{member.display_name}**) as **{day}/{month}**.", ephemeral=True)

    # --- Panel Deployment Commands ---
    @commands.command(name="birthday_panel")
    @commands.has_permissions(administrator=True)
    async def prefix_panel(self, ctx: commands.Context):
        """Deploy the Birthday Studio: .birthday_panel"""
        embed = await self.create_panel_embed()
        await ctx.channel.send(embed=embed, view=BirthdayPluginView(self))
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @app_commands.command(name="birthday_panel", description="Deploy the Birthday Studio Plugin Panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_panel(self, interaction: discord.Interaction):
        embed = await self.create_panel_embed()
        await interaction.channel.send(embed=embed, view=BirthdayPluginView(self))
        await interaction.response.send_message("Birthday panel deployed.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Birthdays(bot))

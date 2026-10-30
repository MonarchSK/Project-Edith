import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, date, timezone
import config

# --- HELPER: Countdown Calculation ---
def calculate_days_until(day: int, month: int) -> int:
    """Calculates days left until the next birthday instance."""
    today = date.today()
    try:
        bday_this_year = date(today.year, month, day)
    except ValueError:
        # Handles Feb 29 on non-leap years
        bday_this_year = date(today.year, month, 28)

    if bday_this_year >= today:
        delta = (bday_this_year - today).days
    else:
        try:
            bday_next_year = date(today.year + 1, month, day)
        except ValueError:
            bday_next_year = date(today.year + 1, month, 28)
        delta = (bday_next_year - today).days
    return delta


# --- MODAL 1: Personal Registration with Custom Wish ---
class FuturisticBirthdayModal(discord.ui.Modal, title="⚡ CYBER-CALENDAR // REGISTRATION"):
    day = discord.ui.TextInput(
        label="Day of Genesis (1-31)",
        placeholder="DD (e.g. 15)",
        min_length=1,
        max_length=2,
        required=True
    )
    month = discord.ui.TextInput(
        label="Cycle Month (1-12)",
        placeholder="MM (e.g. 08 for August)",
        min_length=1,
        max_length=2,
        required=True
    )
    custom_wish = discord.ui.TextInput(
        label="Custom Birthday Broadcast Wish",
        style=discord.TextStyle.paragraph,
        placeholder="Enter your personal broadcast wish when your day arrives...",
        default="May your cybernetic lifespan expand into endless prosperity! 🚀🎉",
        required=False,
        max_length=300
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            d = int(self.day.value.strip())
            m = int(self.month.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Syntax Error: Cycle day and month must be numeric integers.", ephemeral=True)
            return

        if not (1 <= d <= 31 and 1 <= m <= 12):
            await interaction.response.send_message("❌ Temporal Out-of-Bounds: Enter valid day (1-31) and month (1-12).", ephemeral=True)
            return

        user_name = interaction.user.display_name
        wish = self.custom_wish.value.strip() or "Happy Birthday! Wishing you an awesome cycle ahead! 🎂"

        await self.cog.save_birthday(interaction.user.id, user_name, d, m, wish)
        days_left = calculate_days_until(d, m)

        await interaction.response.send_message(
            f"💠 **QUANTUM SYNC CONFIRMED**\n"
            f"• **Operative:** {interaction.user.mention} (`{user_name}`)\n"
            f"• **Saved Date:** `{d:02d}/{m:02d}`\n"
            f"• **Countdown:** **{days_left}** days remaining\n"
            f"• **Custom Wish Stored:** *\"{wish}\"*",
            ephemeral=True
        )


# --- MODAL 2: Master Control Form (Triggered after selecting @member) ---
class MasterSetModal(discord.ui.Modal):
    def __init__(self, cog, target_member: discord.Member):
        super().__init__(title=f"👑 OVERRIDE: {target_member.name[:16]}")
        self.cog = cog
        self.target_member = target_member

        self.day = discord.ui.TextInput(
            label="Day of Birth (1-31)",
            placeholder="DD (e.g. 24)",
            min_length=1,
            max_length=2,
            required=True
        )
        self.month = discord.ui.TextInput(
            label="Month of Birth (1-12)",
            placeholder="MM (e.g. 10)",
            min_length=1,
            max_length=2,
            required=True
        )
        self.custom_wish = discord.ui.TextInput(
            label="Custom Celebration Broadcast Wish",
            style=discord.TextStyle.paragraph,
            placeholder="Custom birthday broadcast message...",
            default="Happy Birthday from the Directorate! Have an incredible year ahead! 🎂✨",
            required=False,
            max_length=300
        )
        self.add_item(self.day)
        self.add_item(self.month)
        self.add_item(self.custom_wish)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            d = int(self.day.value.strip())
            m = int(self.month.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Values must be integers.", ephemeral=True)
            return

        if not (1 <= d <= 31 and 1 <= m <= 12):
            await interaction.response.send_message("❌ Invalid date parameters.", ephemeral=True)
            return

        wish = self.custom_wish.value.strip() or "Happy Birthday! Wishing you joy and success! 🎂"
        await self.cog.save_birthday(self.target_member.id, self.target_member.display_name, d, m, wish)
        days_left = calculate_days_until(d, m)

        await interaction.response.send_message(
            f"🛡️ **MASTER REGISTRY UPDATED**\n"
            f"• **Target:** {self.target_member.mention}\n"
            f"• **Assigned Date:** `{d:02d}/{m:02d}`\n"
            f"• **Next Occurrence:** In **{days_left}** days\n"
            f"• **Custom Message:** *\"{wish}\"*",
            ephemeral=True
        )


# --- INTERACTIVE USER SELECTOR VIEW (NATIVE @ PICKER) ---
class DirectorMemberSelect(discord.ui.UserSelect):
    def __init__(self, cog):
        super().__init__(
            placeholder="Search & choose a server member...",
            min_values=1,
            max_values=1
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        selected_user = self.values[0]
        # Resolve to guild member if possible
        member = interaction.guild.get_member(selected_user.id) or selected_user
        await interaction.response.send_modal(MasterSetModal(self.cog, member))


class MasterUserPickerView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=60)
        self.add_item(DirectorMemberSelect(cog))


# --- FUTURISTIC MAIN DASHBOARD VIEW ---
class FuturisticBirthdayView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Sync My Birthday",
        style=discord.ButtonStyle.primary,
        emoji="⚡",
        custom_id="cyber_bday_sync"
    )
    async def sync_bday(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FuturisticBirthdayModal(self.cog))

    @discord.ui.button(
        label="Telemetry & Countdown",
        style=discord.ButtonStyle.secondary,
        emoji="📡",
        custom_id="cyber_bday_telemetry"
    )
    async def check_telemetry(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.cog.bot.db.execute(
            "SELECT user_name, day, month, custom_wish FROM birthdays WHERE user_id = ?",
            (interaction.user.id,)
        ) as cursor:
            record = await cursor.fetchone()

        if record:
            name, d, m, wish = record[0], record[1], record[2], record[3]
            days_left = calculate_days_until(d, m)
            countdown_text = "🎉 **TODAY IS YOUR CYCLE DAY!**" if days_left == 0 else f"⏳ **{days_left}** standard days remaining"

            embed = discord.Embed(
                title=f"📡 Telemetry Scan: {name}",
                description="Status verification from Cloud SQLite memory bank.",
                color=discord.Color.cyan()
            )
            embed.add_field(name="Registered Coordinate", value=f"`{d:02d}/{m:02d}`", inline=True)
            embed.add_field(name="Countdown Status", value=countdown_text, inline=True)
            embed.add_field(name="Equipped Custom Wish", value=f"> *\"{wish or 'Default Celebration Wish'}\"*", inline=False)
            embed.set_footer(text="Project-Edith • Quantum Storage")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(
                "⚠️ **No Record Located**: Your temporal coordinates are not yet logged. Click **Sync My Birthday** to initialize.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Master Override",
        style=discord.ButtonStyle.danger,
        emoji="👑",
        custom_id="cyber_bday_master"
    )
    async def master_override(self, interaction: discord.Interaction, button: discord.ui.Button):
        has_director = any(r.name == config.DIRECTOR_ROLE_NAME for r in interaction.user.roles)
        if not (has_director or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("⛔ **Access Denied**: Directorate credentials required.", ephemeral=True)
            return

        view = MasterUserPickerView(self.cog)
        await interaction.response.send_message(
            "👑 **Select an operative from the database list below:**",
            view=view,
            ephemeral=True
        )


# --- MAIN COG ENGINE ---
class Birthdays(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_birthdays.start()

    def cog_unload(self):
        self.check_birthdays.cancel()

    async def save_birthday(self, user_id: int, user_name: str, day: int, month: int, wish: str):
        """Saves or updates birthday data with custom wishes."""
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS birthdays (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                day INTEGER NOT NULL,
                month INTEGER NOT NULL,
                custom_wish TEXT
            )
        """)
        # Safe migration for new custom_wish column
        try:
            await self.bot.db.execute("ALTER TABLE birthdays ADD COLUMN custom_wish TEXT")
        except Exception:
            pass
        try:
            await self.bot.db.execute("ALTER TABLE birthdays ADD COLUMN user_name TEXT")
        except Exception:
            pass

        await self.bot.db.execute("""
            INSERT INTO birthdays (user_id, user_name, day, month, custom_wish)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                user_name = excluded.user_name,
                day = excluded.day,
                month = excluded.month,
                custom_wish = excluded.custom_wish
        """, (user_id, user_name, day, month, wish))
        await self.bot.db.commit()

    async def create_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="⚡ PROJECT-EDITH // BIRTHDAY STUDIO HUD",
            description=(
                "```diff\n"
                "+ [SYSTEM ONLINE] TEMPORAL TELEMETRY MATRIX READY\n"
                "--------------------------------------------------\n"
                "Automatic cycle detection and customized celebrations.\n"
                "```"
            ),
            color=discord.Color.from_rgb(0, 240, 255)  # Cyber Cyan
        )
        embed.set_thumbnail(url=config.DEFAULT_ICON)
        embed.set_image(url=config.BDAY_BANNER)

        # Retrieve next 5 upcoming birthdays sorted by countdown
        roster_lines = []
        try:
            async with self.bot.db.execute("SELECT user_id, user_name, day, month FROM birthdays") as cursor:
                records = await cursor.fetchall()
                if records:
                    enriched = []
                    for r in records:
                        days = calculate_days_until(r[2], r[3])
                        enriched.append((days, r[0], r[1], r[2], r[3]))
                    enriched.sort(key=lambda x: x[0])

                    for days, uid, name, d, m in enriched[:5]:
                        badge = "🎯 [TODAY]" if days == 0 else f"⏳ in **{days}** days"
                        roster_lines.append(f"• **{name or 'Operative'}** (<@{uid}>) ➔ `{d:02d}/{m:02d}` ({badge})")
        except Exception:
            pass

        embed.add_field(
            name="🛰️ Upcoming Chrono-Transmissions",
            value="\n".join(roster_lines) if roster_lines else "*No operative dates logged.*",
            inline=False
        )
        embed.add_field(
            name="🎮 Available Protocols",
            value=(
                "• **⚡ Sync My Birthday:** Open pop-up to log your date and custom wish.\n"
                "• **📡 Telemetry & Countdown:** Inspect your days left and registered message.\n"
                "• **👑 Master Override:** Directorate user-picker to set any member's date."
            ),
            inline=False
        )
        embed.set_footer(text="Core Architecture: Project-Edith • 24/7 Cloud Persistence")
        return embed

    @tasks.loop(hours=24)
    async def check_birthdays(self):
        now = datetime.now(timezone.utc)
        async with self.bot.db.execute(
            "SELECT user_id, user_name, custom_wish FROM birthdays WHERE day = ? AND month = ?",
            (now.day, now.month)
        ) as cursor:
            celebrants = await cursor.fetchall()

        if not celebrants or not config.ANNOUNCEMENT_CHANNEL_ID:
            return

        channel = self.bot.get_channel(config.ANNOUNCEMENT_CHANNEL_ID)
        if channel:
            for uid, name, wish in celebrants:
                embed = discord.Embed(
                    title="🎉 CELEBRATION PROTOCOL TRIGGERED!",
                    description=f"Today we celebrate operative **{name}** (<@{uid}>)!",
                    color=discord.Color.gold()
                )
                embed.add_field(
                    name="💬 Personal Broadcast Wish",
                    value=f"> *\"{wish or 'Happy Birthday from the entire community!'}\"*",
                    inline=False
                )
                embed.set_thumbnail(url="https://media.giphy.com/media/l41YcGT5ShJa0nCM0/giphy.gif")
                embed.set_footer(text="Project-Edith Celebration Matrix")
                await channel.send(content=f"🎊 Attention <@{uid}>!", embed=embed)

    @check_birthdays.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    # --- Deployment Commands ---
    @commands.command(name="birthday_panel")
    @commands.has_permissions(administrator=True)
    async def prefix_panel(self, ctx: commands.Context):
        """Deploy Futuristic Birthday HUD: .birthday_panel"""
        embed = await self.create_panel_embed()
        await ctx.channel.send(embed=embed, view=FuturisticBirthdayView(self))
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @app_commands.command(name="birthday_panel", description="Deploy the futuristic Cyber Birthday Studio HUD.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_panel(self, interaction: discord.Interaction):
        embed = await self.create_panel_embed()
        await interaction.channel.send(embed=embed, view=FuturisticBirthdayView(self))
        await interaction.response.send_message("Futuristic Birthday Studio deployed.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Birthdays(bot))

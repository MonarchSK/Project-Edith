import time
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone
import config

BUMP_COOLDOWN_SECONDS = 7200  # 2 Hours
DISBOARD_BOT_ID = 302050872383242240
BUMP_REWARD_XP = 200
COOLDOWN_DELETE_DELAY = 10  # Seconds before cooldown message self-destructs


class BumpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Toggle Bump Pings",
        style=discord.ButtonStyle.secondary,
        emoji="🔔",
        custom_id="btn_toggle_bump_ping_role"
    )
    async def toggle_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user
        role_name = getattr(config, "BUMP_PING_ROLE_NAME", "Bump Pings")

        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            try:
                role = await guild.create_role(
                    name=role_name,
                    color=discord.Color.from_rgb(0, 168, 252),
                    mentionable=True,
                    reason="Automated Bump Notification Role"
                )
            except discord.Forbidden:
                await interaction.followup.send("⚠️ Lacking permissions to manage roles.", ephemeral=True)
                return

        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Self-service bump ping opt-out")
                await interaction.followup.send(f"🔕 You have removed the **{role_name}** role.", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ Discord rejected role change. Ensure Edith's role is above Bump Pings.", ephemeral=True)
        else:
            try:
                await member.add_roles(role, reason="Self-service bump ping opt-in")
                await interaction.followup.send(f"🔔 **{role_name} role assigned!** You'll be notified when the server can be bumped.", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ Discord rejected role change. Ensure Edith's role is above Bump Pings.", ephemeral=True)


class Bumping(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bump_lock = asyncio.Lock()
        self.bump_reminder_loop.start()

    async def cog_load(self):
        self.bot.add_view(BumpView())

    def cog_unload(self):
        self.bump_reminder_loop.cancel()

    async def get_bump_status(self, guild_id: int) -> tuple[int, int, int, int]:
        """Returns (last_bump_timestamp, last_bumper_id, reminder_sent, last_channel_id)."""
        async with self.bot.db.execute(
            "SELECT last_bump_timestamp, last_bumper_id, reminder_sent, last_channel_id FROM bump_status WHERE guild_id = ?",
            (guild_id,)
        ) as cur:
            row = await cur.fetchone()

        if row:
            return row[0], row[1], row[2], row[3]
        return 0, 0, 1, 0

    async def save_bump(self, guild_id: int, user_id: int, channel_id: int):
        now = int(time.time())
        await self.bot.db.execute("""
            INSERT INTO bump_status (guild_id, last_bump_timestamp, last_bumper_id, reminder_sent, last_channel_id, pre_reminder_sent)
            VALUES (?, ?, ?, 0, ?, 0)
            ON CONFLICT(guild_id) DO UPDATE SET
                last_bump_timestamp = excluded.last_bump_timestamp,
                last_bumper_id = excluded.last_bumper_id,
                reminder_sent = 0,
                last_channel_id = excluded.last_channel_id,
                pre_reminder_sent = 0
        """, (guild_id, now, user_id, channel_id))
        await self.bot.db.commit()

        backup_cog = self.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "mark_dirty"):
            backup_cog.mark_dirty(reason="Server Bump Recorded")

    def build_cooldown_embed(self, remaining_seconds: int) -> discord.Embed:
        ready_epoch = int(time.time()) + remaining_seconds
        mins, secs = divmod(remaining_seconds, 60)
        hours, mins = divmod(mins, 60)
        time_str = f"{hours}h {mins}m {secs}s" if hours > 0 else f"{mins}m {secs}s"

        embed = discord.Embed(
            title="⏳ Bump Cooldown Active",
            description=(
                f"The server is currently on bump cooldown.\n"
                f"You can bump again in **{time_str}** (<t:{ready_epoch}:R>).\n\n"
                f"⏳ *This notice will self-destruct in **{COOLDOWN_DELETE_DELAY}** seconds.*"
            ),
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Project-Edith • Server Promotion Hub")
        return embed

    async def process_successful_bump(self, guild: discord.Guild, member: discord.Member, channel: discord.TextChannel):
        """Dispatches celebration embed, awards XP, and locks in the new cooldown."""
        async with self.bump_lock:
            await self.save_bump(guild.id, member.id, channel.id)

            level_cog = self.bot.get_cog("Leveling")
            if level_cog and hasattr(level_cog, "add_xp"):
                await level_cog.add_xp(member, BUMP_REWARD_XP, reason="Server Bump Reward", channel=channel)

            next_bump_time = int(time.time()) + BUMP_COOLDOWN_SECONDS
            embed = discord.Embed(
                title="🚀 SERVER BUMP RECORDED!",
                description=(
                    f"Thank you {member.mention} for bumping the server!\n\n"
                    f"✨ **Reward:** Received **+{BUMP_REWARD_XP} XP**\n"
                    f"⏰ **Next Bump Available:** <t:{next_bump_time}:R> (<t:{next_bump_time}:t>)\n\n"
                    f"Click below to get pinged when the next bump is ready!"
                ),
                color=discord.Color.from_rgb(0, 168, 252),
                timestamp=datetime.now(timezone.utc)
            )
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            embed.set_footer(text="Project-Edith • Server Promotion Hub")

            view = BumpView()
            await channel.send(embed=embed, view=view)

    # --- Prefix Command: .bump / ;bump ---
    @commands.command(name="bump")
    async def prefix_bump(self, ctx: commands.Context):
        """Boost server status and earn +200 XP: .bump"""
        # Delete user command immediately to keep chat clean
        try:
            await ctx.message.delete()
        except Exception:
            pass

        now = int(time.time())
        last_bump, _, _, _ = await self.get_bump_status(ctx.guild.id)
        diff = now - last_bump

        if diff < BUMP_COOLDOWN_SECONDS:
            remaining = BUMP_COOLDOWN_SECONDS - diff
            embed = self.build_cooldown_embed(remaining)
            await ctx.send(embed=embed, delete_after=COOLDOWN_DELETE_DELAY)
            return

        await self.process_successful_bump(ctx.guild, ctx.author, ctx.channel)

    # --- Slash Command: /bump ---
    @app_commands.command(name="bump", description="Boost server ranking, lock in next timer, and earn +200 XP.")
    async def slash_bump(self, interaction: discord.Interaction):
        now = int(time.time())
        last_bump, _, _, _ = await self.get_bump_status(interaction.guild_id)
        diff = now - last_bump

        if diff < BUMP_COOLDOWN_SECONDS:
            remaining = BUMP_COOLDOWN_SECONDS - diff
            embed = self.build_cooldown_embed(remaining)
            # Ephemeral so chat is not cluttered
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.process_successful_bump(interaction.guild, interaction.user, interaction.channel)
        await interaction.followup.send("✅ Server bump verified and recorded!", ephemeral=True)

    # --- Disboard Bot Bump Detection ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return

        # Crucial: Ignore user command prefixes completely to avoid double triggers
        if message.content.startswith((".", ";")):
            return

        # Check if message is from Disboard bot
        if message.author.id == DISBOARD_BOT_ID and message.embeds:
            description = (message.embeds[0].description or "").lower()
            # Standard Disboard success phrases
            if "bump done" in description or "check it on disboard" in description:
                # Find bumper from recent chat history
                bumper = None
                async for prev_msg in message.channel.history(limit=5, before=message):
                    if not prev_msg.author.bot and "/bump" in prev_msg.content.lower():
                        bumper = prev_msg.author
                        break
                    elif not prev_msg.author.bot:
                        bumper = prev_msg.author
                        break

                target_user = bumper or message.guild.me
                await self.process_successful_bump(message.guild, target_user, message.channel)

    # --- Scheduled 2-Hour Reminder Loop ---
    @tasks.loop(minutes=1)
    async def bump_reminder_loop(self):
        now = int(time.time())

        for guild in self.bot.guilds:
            last_bump, _, reminder_sent, last_channel_id = await self.get_bump_status(guild.id)
            if last_bump == 0 or reminder_sent == 1:
                continue

            if now - last_bump >= BUMP_COOLDOWN_SECONDS:
                # Mark reminder sent immediately to block duplicate loops
                await self.bot.db.execute(
                    "UPDATE bump_status SET reminder_sent = 1 WHERE guild_id = ?",
                    (guild.id,)
                )
                await self.bot.db.commit()

                channel = guild.get_channel(last_channel_id) or guild.system_channel
                if not channel:
                    continue

                role_name = getattr(config, "BUMP_PING_ROLE_NAME", "Bump Pings")
                role = discord.utils.get(guild.roles, name=role_name)
                mention_str = role.mention if role else "@here"

                embed = discord.Embed(
                    title="⏰ SERVER BUMP READY!",
                    description=(
                        f"The 2-hour cooldown has elapsed!\n\n"
                        f"Run `.bump` or `/bump` now to boost the server and claim **+{BUMP_REWARD_XP} XP**!"
                    ),
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc)
                )
                if guild.icon:
                    embed.set_thumbnail(url=guild.icon.url)
                embed.set_footer(text="Project-Edith • Server Promotion Hub")

                view = BumpView()
                try:
                    await channel.send(
                        content=f"📢 {mention_str}",
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
                    )
                except Exception as e:
                    print(f"[BUMP REMINDER ERROR] Could not dispatch reminder: {e}")

    @bump_reminder_loop.before_loop
    async def before_bump_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Bumping(bot))

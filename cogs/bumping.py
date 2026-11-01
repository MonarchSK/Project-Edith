import time
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone
import config

BUMP_COOLDOWN_SECONDS = 7200  # 2 Hours
BUMP_XP_REWARD = 250          # Massive XP boost for bumping server

class Bumping(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_bump_reminder.start()

    def cog_unload(self):
        self.check_bump_reminder.cancel()

    async def get_bump_status(self, guild_id: int):
        async with self.bot.db.execute(
            "SELECT last_bump_timestamp, last_bumper_id, reminder_sent, last_channel_id FROM bump_status WHERE guild_id = ?",
            (guild_id,)
        ) as cursor:
            return await cursor.fetchone()

    async def execute_bump(self, guild: discord.Guild, user: discord.Member, channel: discord.TextChannel) -> tuple[bool, str, int]:
        now = int(time.time())
        row = await self.get_bump_status(guild.id)

        if row:
            last_bump, _, _, _ = row
            diff = now - last_bump
            if diff < BUMP_COOLDOWN_SECONDS:
                remaining_secs = BUMP_COOLDOWN_SECONDS - diff
                minutes, seconds = divmod(remaining_secs, 60)
                hours, minutes = divmod(minutes, 60)
                time_str = f"{hours}h {minutes}m {seconds}s" if hours > 0 else f"{minutes}m {seconds}s"
                return False, time_str, remaining_secs

        # Update or set new bump state
        await self.bot.db.execute("""
            INSERT INTO bump_status (guild_id, last_bump_timestamp, last_bumper_id, reminder_sent, last_channel_id)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                last_bump_timestamp = excluded.last_bump_timestamp,
                last_bumper_id = excluded.last_bumper_id,
                reminder_sent = 0,
                last_channel_id = excluded.last_channel_id
        """, (guild.id, now, user.id, channel.id))
        await self.bot.db.commit()

        # Award bumper with massive XP boost
        leveling_cog = self.bot.get_cog("Leveling")
        if leveling_cog:
            await leveling_cog.add_xp(user, BUMP_XP_REWARD, reason="Server Bump Reward", channel=channel)

        return True, "Success", 0

    @commands.command(name="bump")
    async def prefix_bump(self, ctx: commands.Context):
        """Bump the server to earn 250 XP and boost server ranking."""
        success, time_str, remaining = await self.execute_bump(ctx.guild, ctx.author, ctx.channel)
        if not success:
            embed = discord.Embed(
                title="⏳ Bump Cooldown Active",
                description=f"This server was already bumped recently!\nYou can bump again in **{time_str}**.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(
            title="🚀 SERVER BUMPED SUCCESSFULLY!",
            description=(
                f"**{ctx.guild.name}** has been bumped to the top!\n\n"
                f"• **Bumper:** {ctx.author.mention}\n"
                f"• **XP Reward:** `+{BUMP_XP_REWARD} XP` awarded\n"
                f"• **Next Bump:** <t:{int(time.time()) + BUMP_COOLDOWN_SECONDS}:R>"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        embed.set_footer(text="Project-Edith • Server Bump Engine")
        await ctx.send(embed=embed)

    @app_commands.command(name="bump", description="Bump the server to earn XP and boost visibility.")
    async def slash_bump(self, interaction: discord.Interaction):
        success, time_str, remaining = await self.execute_bump(interaction.guild, interaction.user, interaction.channel)
        if not success:
            embed = discord.Embed(
                title="⏳ Bump Cooldown Active",
                description=f"This server was already bumped recently!\nYou can bump again in **{time_str}**.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="🚀 SERVER BUMPED SUCCESSFULLY!",
            description=(
                f"**{interaction.guild.name}** has been bumped to the top!\n\n"
                f"• **Bumper:** {interaction.user.mention}\n"
                f"• **XP Reward:** `+{BUMP_XP_REWARD} XP` awarded\n"
                f"• **Next Bump:** <t:{int(time.time()) + BUMP_COOLDOWN_SECONDS}:R>"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.set_footer(text="Project-Edith • Server Bump Engine")
        await interaction.response.send_message(embed=embed)

    # --- Background Cooldown Check & Notification Dispatcher ---
    @tasks.loop(seconds=30)
    async def check_bump_reminder(self):
        now = int(time.time())
        async with self.bot.db.execute("""
            SELECT guild_id, last_bump_timestamp, last_bumper_id, last_channel_id
            FROM bump_status
            WHERE reminder_sent = 0
        """) as cursor:
            rows = await cursor.fetchall()

        for gid, last_bump, bumper_id, chan_id in rows:
            if now - last_bump >= BUMP_COOLDOWN_SECONDS:
                # Mark reminder sent
                await self.bot.db.execute("UPDATE bump_status SET reminder_sent = 1 WHERE guild_id = ?", (gid,))
                await self.bot.db.commit()

                guild = self.bot.get_guild(gid)
                if not guild:
                    continue

                channel = guild.get_channel(chan_id)
                if channel:
                    embed = discord.Embed(
                        title="🔔 Time to Bump the Server!",
                        description=(
                            "The 2-hour cooldown has expired!\n"
                            "Use `.bump` or `/bump` now to claim **+250 XP** and boost the server!"
                        ),
                        color=discord.Color.blue()
                    )
                    bumper = guild.get_member(bumper_id)
                    ping_text = f"📢 {bumper.mention}" if bumper else "📢 Hey everyone!"
                    try:
                        await channel.send(content=ping_text, embed=embed)
                    except Exception:
                        pass

    @check_bump_reminder.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Bumping(bot))

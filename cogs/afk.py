import time
import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

# 15+ Distinct Built-In Custom AFK Status Presets
DEFAULT_AFK_PRESETS = [
    "Busy studying & cramming for exams 📚",
    "Grinding ranked games — do not disturb 🎮",
    "Sleeping / Power-napping 💤",
    "Eating food / Out grabbing dinner 🍔",
    "At the gym lifting weights 🏋️",
    "Driving / Commuting right now 🚗",
    "Deep in a coding sprint / debugging 💻",
    "In an important meeting / work call 📞",
    "Taking a mental health / screen break 🌿",
    "Watching a movie / binge-watching series 🍿",
    "Handling household chores / errands 🧹",
    "Away from keyboard — BRB soon ⏳",
    "Offline spending time with family 🏡",
    "Reading a book / chilling out 📖",
    "Cooking a meal in the kitchen 🍳",
    "Do Not Disturb — ping only for emergencies 🔕"
]

AFK_CONFIRM_DISAPPEAR_SECONDS = 10


class AFK(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recently_set = set()

    async def set_user_afk(self, guild: discord.Guild, member: discord.Member, reason: str) -> discord.Embed:
        now = int(time.time())
        original_nick = member.nick

        # Save record to SQLite
        await self.bot.db.execute("""
            INSERT INTO afk_users (user_id, guild_id, reason, timestamp, original_nickname)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                guild_id = excluded.guild_id,
                reason = excluded.reason,
                timestamp = excluded.timestamp,
                original_nickname = excluded.original_nickname
        """, (member.id, guild.id, reason, now, original_nick))
        await self.bot.db.commit()

        # Mark cloud backup dirty
        backup_cog = self.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "mark_dirty"):
            backup_cog.mark_dirty(reason="User AFK Status Registered")

        # Update member display name with [AFK] prefix
        try:
            current_name = member.display_name
            if not current_name.startswith("[AFK] "):
                new_nick = f"[AFK] {current_name}"[:32]
                await member.edit(nick=new_nick, reason="User entered AFK status")
        except discord.Forbidden:
            pass

        embed = discord.Embed(
            title="💤 Status: AFK Set",
            description=(
                f"{member.mention} is now marked as **AFK**.\n\n"
                f"💬 **Message:**\n> *\"{reason}\"*\n\n"
                f"⏳ *This notice will disappear in **{AFK_CONFIRM_DISAPPEAR_SECONDS}** seconds.*"
            ),
            color=discord.Color.from_rgb(114, 137, 218),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Send any message in chat to remove your AFK status.")
        return embed

    async def afk_preset_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        choices = [
            app_commands.Choice(name=preset[:100], value=preset)
            for preset in DEFAULT_AFK_PRESETS
            if current.lower() in preset.lower()
        ]
        return choices[:25]

    # --- Prefix Command: .afk [reason] or ;afk [reason] ---
    @commands.command(name="afk")
    async def prefix_afk(self, ctx: commands.Context, *, reason: str = None):
        """Set your AFK status with auto-deleting confirmation: .afk studying"""
        final_reason = reason.strip() if reason else random.choice(DEFAULT_AFK_PRESETS)
        self.recently_set.add(ctx.author.id)

        # Delete the trigger command (.afk ...) so chat stays clean
        try:
            await ctx.message.delete()
        except Exception:
            pass

        embed = await self.set_user_afk(ctx.guild, ctx.author, final_reason)
        # Disappear automatically after 10 seconds
        await ctx.send(embed=embed, delete_after=AFK_CONFIRM_DISAPPEAR_SECONDS)

    # --- Slash Command: /afk [message] ---
    @app_commands.command(name="afk", description="Set your AFK status with a custom message or preset.")
    @app_commands.describe(message="Choose a preset or type your own custom away message")
    @app_commands.autocomplete(message=afk_preset_autocomplete)
    async def slash_afk(self, interaction: discord.Interaction, message: str = None):
        final_reason = message.strip() if message else random.choice(DEFAULT_AFK_PRESETS)
        self.recently_set.add(interaction.user.id)
        embed = await self.set_user_afk(interaction.guild, interaction.user, final_reason)
        
        # Post confirmation to channel that self-destructs in 10 seconds
        await interaction.response.send_message(embed=embed)
        await asyncio.sleep(AFK_CONFIRM_DISAPPEAR_SECONDS)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        # Crucial Fix: Skip command messages entirely so .afk or ;afk doesn't trigger on_message
        if message.content.startswith((".", ";")):
            return

        # 1. Clear AFK status when the member talks
        if message.author.id in self.recently_set:
            self.recently_set.remove(message.author.id)
        else:
            async with self.bot.db.execute(
                "SELECT reason, timestamp, original_nickname FROM afk_users WHERE user_id = ?",
                (message.author.id,)
            ) as cur:
                afk_record = await cur.fetchone()

            if afk_record:
                _, afk_time, original_nick = afk_record
                await self.bot.db.execute("DELETE FROM afk_users WHERE user_id = ?", (message.author.id,))
                await self.bot.db.commit()

                # Restore original nickname
                try:
                    await message.author.edit(nick=original_nick, reason="User returned from AFK")
                except discord.Forbidden:
                    pass

                embed = discord.Embed(
                    title="👋 Welcome Back!",
                    description=(
                        f"{message.author.mention}, your AFK status has been cleared.\n"
                        f"You were away for <t:{afk_time}:R>.\n\n"
                        f"⏳ *Auto-clearing in {AFK_CONFIRM_DISAPPEAR_SECONDS}s.*"
                    ),
                    color=discord.Color.green()
                )
                await message.channel.send(embed=embed, delete_after=AFK_CONFIRM_DISAPPEAR_SECONDS)

        # 2. Inform users who mention an AFK member
        if message.mentions:
            for mentioned in message.mentions:
                if mentioned.id == message.author.id or mentioned.bot:
                    continue

                async with self.bot.db.execute(
                    "SELECT reason, timestamp FROM afk_users WHERE user_id = ?",
                    (mentioned.id,)
                ) as cur:
                    target_afk = await cur.fetchone()

                if target_afk:
                    reason, afk_time = target_afk
                    embed = discord.Embed(
                        title="💤 Member is AFK",
                        description=(
                            f"**{mentioned.display_name}** went AFK <t:{afk_time}:R>.\n\n"
                            f"💬 **Message:**\n> *\"{reason}\"*\n\n"
                            f"⏳ *Auto-clearing in {AFK_CONFIRM_DISAPPEAR_SECONDS}s.*"
                        ),
                        color=discord.Color.gold(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    embed.set_thumbnail(url=mentioned.display_avatar.url)
                    await message.channel.send(embed=embed, delete_after=AFK_CONFIRM_DISAPPEAR_SECONDS)


async def setup(bot):
    await bot.add_cog(AFK(bot))

import time
import random
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone
import config

MAX_LEVEL = 50
MSG_COOLDOWN_SECONDS = 60
MSG_XP_MIN = 15
MSG_XP_MAX = 25
VOICE_XP_PER_MINUTE = 10

# Milestone rewards mapping: level -> (role_name, role_color_hex)
MILESTONE_ROLES = {
    5: ("Bronze Operative", 0xCD7F32),
    10: ("Silver Operative", 0xC0C0C0),
    25: ("Gold Operative", 0xFFD700),
    50: ("Mythic Pinnacle", 0x9B59B6)
}


def calculate_xp_for_level(level: int) -> int:
    """Standard progressive XP curve capped at level 50."""
    if level <= 0:
        return 0
    if level >= MAX_LEVEL:
        return 5 * (MAX_LEVEL ** 2) + (50 * MAX_LEVEL) + 100
    return 5 * (level ** 2) + (50 * level) + 100


def get_progress_bar(current: int, target: int, length: int = 10) -> str:
    """Generates a text progress bar."""
    if target <= 0:
        return "▰" * length
    ratio = min(max(current / target, 0.0), 1.0)
    filled = int(ratio * length)
    empty = length - filled
    return f"{'▰' * filled}{'▱' * empty} {int(ratio * 100)}%"


class Leveling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.msg_cooldowns = {}   # {user_id: timestamp}
        self.voice_tracking = {}  # {user_id: join_timestamp}
        self.voice_xp_loop.start()

    def cog_unload(self):
        self.voice_xp_loop.cancel()

    async def _assign_milestone_roles(self, member: discord.Member, current_level: int) -> list[str]:
        """Ensures all milestone roles up to the current level are assigned to the member."""
        awarded_role_names = []
        guild = member.guild

        for milestone_lvl, (role_name, role_color) in sorted(MILESTONE_ROLES.items()):
            if current_level >= milestone_lvl:
                # Find role or create if not present
                role = discord.utils.get(guild.roles, name=role_name)
                if not role:
                    try:
                        role = await guild.create_role(
                            name=role_name,
                            color=discord.Color(role_color),
                            reason=f"Automated Level {milestone_lvl} Milestone Role"
                        )
                    except discord.Forbidden:
                        print(f"[ROLE ERROR] Missing permissions to create role '{role_name}'.")
                        continue

                # Assign role if member does not have it yet
                if role not in member.roles:
                    try:
                        await member.add_roles(role, reason=f"Level {milestone_lvl} milestone reached")
                        awarded_role_names.append(role.name)
                    except discord.Forbidden:
                        print(f"[ROLE ERROR] Missing hierarchy permissions to give '{role_name}' to {member.name}.")

        return awarded_role_names

    async def get_or_create_user(self, user_id: int, username: str) -> dict:
        async with self.bot.db.execute(
            "SELECT user_id, user_name, xp, level, messages_count, voice_minutes FROM levels WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            return {
                "user_id": row[0],
                "user_name": row[1],
                "xp": row[2],
                "level": row[3],
                "messages_count": row[4],
                "voice_minutes": row[5]
            }

        await self.bot.db.execute("""
            INSERT INTO levels (user_id, user_name, xp, level, messages_count, voice_minutes)
            VALUES (?, ?, 0, 1, 0, 0)
        """, (user_id, username))
        await self.bot.db.commit()

        return {
            "user_id": user_id,
            "user_name": username,
            "xp": 0,
            "level": 1,
            "messages_count": 0,
            "voice_minutes": 0
        }

    async def add_xp(self, user: discord.Member, amount: int, reason: str = "Activity", channel: discord.TextChannel = None):
        """Grants XP to member, calculates level progression, awards milestone roles, and announces level-ups."""
        if user.bot or not isinstance(user, discord.Member):
            return

        data = await self.get_or_create_user(user.id, user.display_name)
        if data["level"] >= MAX_LEVEL:
            return  # Member reached Level 50 cap

        new_xp = data["xp"] + amount
        current_level = data["level"]
        old_level = current_level
        leveled_up = False

        while current_level < MAX_LEVEL:
            needed = calculate_xp_for_level(current_level)
            if new_xp >= needed:
                new_xp -= needed
                current_level += 1
                leveled_up = True
            else:
                break

        if current_level >= MAX_LEVEL:
            current_level = MAX_LEVEL
            new_xp = calculate_xp_for_level(MAX_LEVEL)

        await self.bot.db.execute("""
            UPDATE levels
            SET xp = ?, level = ?, user_name = ?
            WHERE user_id = ?
        """, (new_xp, current_level, user.display_name, user.id))
        await self.bot.db.commit()

        # Mark backup engine as modified for queued snapshotting
        backup_cog = self.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "mark_dirty"):
            backup_cog.mark_dirty()

        # Handle level up and milestone role awards
        if leveled_up:
            new_roles_awarded = await self._assign_milestone_roles(user, current_level)

            target_chan = channel
            if not target_chan and config.ANNOUNCEMENT_CHANNEL_ID:
                target_chan = self.bot.get_channel(config.ANNOUNCEMENT_CHANNEL_ID)

            if target_chan:
                embed = discord.Embed(
                    title="🎉 LEVEL ADVANCEMENT!",
                    description=f"Congratulations {user.mention}! You advanced from **Level {old_level}** to **Level {current_level} / {MAX_LEVEL}**!",
                    color=discord.Color.gold()
                )

                if new_roles_awarded:
                    roles_formatted = ", ".join([f"**{r}**" for r in new_roles_awarded])
                    embed.add_field(
                        name="🎖️ Milestone Reward Unlocked!",
                        value=f"You have been granted: {roles_formatted}",
                        inline=False
                    )

                if current_level == MAX_LEVEL:
                    embed.add_field(
                        name="👑 PINNACLE REACHED",
                        value="You have achieved the ultimate rank of Level 50! Maximum respect.",
                        inline=False
                    )

                embed.set_thumbnail(url=user.display_avatar.url)
                embed.set_footer(text="Project-Edith • Level Progression")
                try:
                    await target_chan.send(embed=embed)
                except Exception:
                    pass

    # --- Text Message XP Listener ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        # Ignore bot prefix commands
        if message.content.startswith("."):
            return

        now = time.time()
        last_time = self.msg_cooldowns.get(message.author.id, 0)
        if now - last_time < MSG_COOLDOWN_SECONDS:
            return

        self.msg_cooldowns[message.author.id] = now
        xp_gain = random.randint(MSG_XP_MIN, MSG_XP_MAX)

        await self.bot.db.execute("""
            INSERT INTO levels (user_id, user_name, xp, level, messages_count, voice_minutes)
            VALUES (?, ?, 0, 1, 1, 0)
            ON CONFLICT(user_id) DO UPDATE SET messages_count = messages_count + 1
        """, (message.author.id, message.author.display_name))
        await self.bot.db.commit()

        await self.add_xp(message.author, xp_gain, reason="Message XP", channel=message.channel)

    # --- Voice State Listener & XP Tracker ---
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        if not before.channel and after.channel:
            self.voice_tracking[member.id] = time.time()
        elif before.channel and not after.channel:
            self.voice_tracking.pop(member.id, None)

    @tasks.loop(minutes=1)
    async def voice_xp_loop(self):
        """Iterates over voice channels, awarding XP to eligible members."""
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                # Disqualify if user is alone or in AFK channel
                if len(vc.members) < 2 or vc == guild.afk_channel:
                    continue

                for member in vc.members:
                    if member.bot or member.voice.self_deaf or member.voice.deaf:
                        continue

                    await self.bot.db.execute("""
                        INSERT INTO levels (user_id, user_name, xp, level, messages_count, voice_minutes)
                        VALUES (?, ?, 0, 1, 0, 1)
                        ON CONFLICT(user_id) DO UPDATE SET voice_minutes = voice_minutes + 1
                    """, (member.id, member.display_name))
                    await self.bot.db.commit()

                    await self.add_xp(member, VOICE_XP_PER_MINUTE, reason="Voice Channel XP")

    @voice_xp_loop.before_loop
    async def before_voice_loop(self):
        await self.bot.wait_until_ready()

    # --- Rank Command ---
    @commands.command(name="rank")
    async def rank_cmd(self, ctx: commands.Context, member: discord.Member = None):
        """Check your rank or another member's level card."""
        target = member or ctx.author
        data = await self.get_or_create_user(target.id, target.display_name)

        needed = calculate_xp_for_level(data["level"])
        bar = get_progress_bar(data["xp"], needed)

        async with self.bot.db.execute("""
            SELECT user_id FROM levels ORDER BY level DESC, xp DESC
        """) as cursor:
            all_users = await cursor.fetchall()

        rank_pos = 1
        for idx, row in enumerate(all_users):
            if row[0] == target.id:
                rank_pos = idx + 1
                break

        embed = discord.Embed(
            title=f"📊 Rank Card — {target.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🏆 Server Rank", value=f"`#{rank_pos}` of `{len(all_users)}`", inline=True)
        embed.add_field(name="⭐ Level", value=f"`{data['level']} / {MAX_LEVEL}`", inline=True)
        embed.add_field(name="✨ Current XP", value=f"`{data['xp']} / {needed} XP`", inline=True)
        embed.add_field(name="📈 Progress to Level Up", value=f"`{bar}`", inline=False)
        embed.add_field(name="💬 Messages", value=f"`{data['messages_count']}`", inline=True)
        embed.add_field(name="🎙️ Voice Time", value=f"`{data['voice_minutes']} mins`", inline=True)

        # Show next milestone role preview
        upcoming_milestones = [lvl for lvl in sorted(MILESTONE_ROLES.keys()) if lvl > data["level"]]
        if upcoming_milestones:
            next_lvl = upcoming_milestones[0]
            role_name = MILESTONE_ROLES[next_lvl][0]
            embed.add_field(name="🎯 Next Role Unlock", value=f"**{role_name}** at Level `{next_lvl}`", inline=False)
        else:
            embed.add_field(name="👑 Rank Status", value="All milestone roles unlocked!", inline=False)

        embed.set_footer(text="Project-Edith • Level Progression")
        await ctx.send(embed=embed)

    @app_commands.command(name="rank", description="Check your rank, level, and XP progression.")
    @app_commands.describe(member="Member to inspect")
    async def slash_rank(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        data = await self.get_or_create_user(target.id, target.display_name)

        needed = calculate_xp_for_level(data["level"])
        bar = get_progress_bar(data["xp"], needed)

        async with self.bot.db.execute("SELECT user_id FROM levels ORDER BY level DESC, xp DESC") as cursor:
            all_users = await cursor.fetchall()

        rank_pos = 1
        for idx, row in enumerate(all_users):
            if row[0] == target.id:
                rank_pos = idx + 1
                break

        embed = discord.Embed(
            title=f"📊 Rank Card — {target.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🏆 Server Rank", value=f"`#{rank_pos}` of `{len(all_users)}`", inline=True)
        embed.add_field(name="⭐ Level", value=f"`{data['level']} / {MAX_LEVEL}`", inline=True)
        embed.add_field(name="✨ Current XP", value=f"`{data['xp']} / {needed} XP`", inline=True)
        embed.add_field(name="📈 Progress to Level Up", value=f"`{bar}`", inline=False)
        embed.add_field(name="💬 Messages", value=f"`{data['messages_count']}`", inline=True)
        embed.add_field(name="🎙️ Voice Time", value=f"`{data['voice_minutes']} mins`", inline=True)

        upcoming_milestones = [lvl for lvl in sorted(MILESTONE_ROLES.keys()) if lvl > data["level"]]
        if upcoming_milestones:
            next_lvl = upcoming_milestones[0]
            role_name = MILESTONE_ROLES[next_lvl][0]
            embed.add_field(name="🎯 Next Role Unlock", value=f"**{role_name}** at Level `{next_lvl}`", inline=False)
        else:
            embed.add_field(name="👑 Rank Status", value="All milestone roles unlocked!", inline=False)

        embed.set_footer(text="Project-Edith • Level Progression")
        await interaction.response.send_message(embed=embed)

    # --- Leaderboard Command ---
    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard_cmd(self, ctx: commands.Context):
        """View the top 10 members on the server."""
        async with self.bot.db.execute("""
            SELECT user_id, user_name, level, xp, messages_count
            FROM levels
            ORDER BY level DESC, xp DESC
            LIMIT 10
        """) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            await ctx.send("No member activity recorded yet.")
            return

        embed = discord.Embed(
            title="🏆 Server Activity Leaderboard",
            description="Top 10 operatives ranked by level and XP:\n",
            color=discord.Color.gold()
        )

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, (uid, name, lvl, xp, msgs) in enumerate(rows):
            medal = medals[idx] if idx < 3 else f"`#{idx + 1}`"
            display_name = name or f"User {uid}"
            lines.append(f"{medal} **{display_name}** (<@{uid}>)\n   ↳ Level **{lvl}** • `{xp} XP` • `{msgs} msgs`")

        embed.add_field(name="Rankings", value="\n\n".join(lines), inline=False)
        embed.set_footer(text="Project-Edith • Leveling Matrix")
        await ctx.send(embed=embed)

    @app_commands.command(name="leaderboard", description="View the top 10 server members by level and XP.")
    async def slash_leaderboard(self, interaction: discord.Interaction):
        async with self.bot.db.execute("""
            SELECT user_id, user_name, level, xp, messages_count
            FROM levels
            ORDER BY level DESC, xp DESC
            LIMIT 10
        """) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            await interaction.response.send_message("No member activity recorded yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🏆 Server Activity Leaderboard",
            description="Top 10 operatives ranked by level and XP:\n",
            color=discord.Color.gold()
        )

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, (uid, name, lvl, xp, msgs) in enumerate(rows):
            medal = medals[idx] if idx < 3 else f"`#{idx + 1}`"
            display_name = name or f"User {uid}"
            lines.append(f"{medal} **{display_name}** (<@{uid}>)\n   ↳ Level **{lvl}** • `{xp} XP` • `{msgs} msgs`")

        embed.add_field(name="Rankings", value="\n\n".join(lines), inline=False)
        embed.set_footer(text="Project-Edith • Leveling Matrix")
        await interaction.response.send_message(embed=embed)

    # --- Director Master Command to Manually Set Levels (For Testing) ---
    @commands.command(name="setlevel")
    @commands.has_permissions(administrator=True)
    async def set_level_cmd(self, ctx: commands.Context, member: discord.Member, target_level: int):
        """Admin command to manually adjust member levels: .setlevel @user 10"""
        if not (1 <= target_level <= MAX_LEVEL):
            await ctx.send(f"❌ Level must be between 1 and {MAX_LEVEL}.")
            return

        await self.get_or_create_user(member.id, member.display_name)
        await self.bot.db.execute("""
            UPDATE levels
            SET level = ?, xp = 0
            WHERE user_id = ?
        """, (target_level, member.id))
        await self.bot.db.commit()

        # Sync milestone roles immediately
        awarded = await self._assign_milestone_roles(member, target_level)
        msg = f"✅ Set {member.mention}'s rank to **Level {target_level}**."
        if awarded:
            msg += f" Assigned roles: {', '.join(awarded)}"
        await ctx.send(msg)


async def setup(bot):
    await bot.add_cog(Leveling(bot))

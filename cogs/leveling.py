import time
import math
import random
import asyncio
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
BOOSTER_XP_MULTIPLIER = 1.5  # 1.5x XP Boost for Server Boosters

# Random XP Drop Settings
DROP_CHANCE = 0.03            # 3% chance per eligible chat message
DROP_XP_MIN = 10
DROP_XP_MAX = 1000
DROP_TIMEOUT_SECONDS = 90     # Despawns after 90 seconds if unclaimed

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


def is_server_booster(member: discord.Member) -> bool:
    """Checks if the user has boosted the server using Discord's premium_since attribute."""
    if not isinstance(member, discord.Member):
        return False
    return member.premium_since is not None


# --- Interactive Random XP Drop View ---
class XpDropClaimView(discord.ui.View):
    def __init__(self, cog, xp_amount: int):
        super().__init__(timeout=DROP_TIMEOUT_SECONDS)
        self.cog = cog
        self.xp_amount = xp_amount
        self.claimed = False
        self.message = None

    @discord.ui.button(label="Claim XP Drop", style=discord.ButtonStyle.success, emoji="🎁", custom_id="btn_claim_xp_drop")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.claimed:
            await interaction.response.send_message("❌ This XP Drop has already been claimed!", ephemeral=True)
            return

        self.claimed = True
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name[:16]}"
        button.style = discord.ButtonStyle.secondary

        user = interaction.user
        awarded_xp = self.xp_amount
        if is_server_booster(user):
            awarded_xp = math.ceil(self.xp_amount * BOOSTER_XP_MULTIPLIER)

        await self.cog.add_xp(user, self.xp_amount, reason="Random XP Drop Claim", channel=interaction.channel)

        embed = discord.Embed(
            title="🎁 XP Drop Claimed!",
            description=(
                f"**{user.mention}** reacted first and claimed **+{self.xp_amount} XP**!\n"
                + (f"🚀 *Booster 1.5x Multiplier applied: Received **+{awarded_xp} XP**!*" if is_server_booster(user) else "")
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="Project-Edith • Supply Drop Claimed")

        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        if not self.claimed and self.message:
            for child in self.children:
                child.disabled = True
                child.label = "Expired"
            embed = discord.Embed(
                title="📦 XP Drop Expired",
                description="Nobody claimed this supply drop in time. Better luck next time!",
                color=discord.Color.dark_grey()
            )
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass


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
        """Grants XP, calculates level progression, awards milestone roles, and announces to the Announcement Channel."""
        if user.bot or not isinstance(user, discord.Member):
            return

        final_xp = amount
        if is_server_booster(user):
            final_xp = math.ceil(amount * BOOSTER_XP_MULTIPLIER)

        data = await self.get_or_create_user(user.id, user.display_name)
        if data["level"] >= MAX_LEVEL:
            return

        new_xp = data["xp"] + final_xp
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

        backup_cog = self.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "mark_dirty"):
            backup_cog.mark_dirty()

        if leveled_up:
            new_roles_awarded = await self._assign_milestone_roles(user, current_level)

            # Prioritize the Announcement Channel for Level-Up Messages
            target_chan = None
            announcement_id = int(getattr(config, "ANNOUNCEMENT_CHANNEL_ID", 0))
            if announcement_id and announcement_id != 0:
                target_chan = self.bot.get_channel(announcement_id)
                if not target_chan:
                    try:
                        target_chan = await self.bot.fetch_channel(announcement_id)
                    except Exception:
                        target_chan = None

            # Fallback to local channel if no announcement channel is found
            if not target_chan:
                target_chan = channel

            if target_chan:
                embed = discord.Embed(
                    title="🎉 LEVEL ADVANCEMENT!",
                    description=f"Congratulations {user.mention}! You advanced from **Level {old_level}** to **Level {current_level} / {MAX_LEVEL}**!",
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc)
                )

                if is_server_booster(user):
                    embed.add_field(
                        name="🚀 Booster Perk Active",
                        value="`1.5x XP Multiplier` applied to all activities!",
                        inline=False
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
                embed.set_footer(text="Project-Edith • Server Leveling Hub")
                try:
                    await target_chan.send(content=f"📢 Attention {user.mention}!", embed=embed)
                except Exception as e:
                    print(f"[LEVEL UP DISPATCH ERROR] {e}")

    async def trigger_xp_drop(self, channel: discord.TextChannel, amount: int = None):
        """Spawns an interactive XP drop into the designated channel."""
        drop_val = amount or random.randint(DROP_XP_MIN, DROP_XP_MAX)

        if drop_val >= 600:
            rarity = "🌟 LEGENDARY SUPPLY CRATE"
            color = discord.Color.gold()
        elif drop_val >= 250:
            rarity = "💎 RARE XP CACHE"
            color = discord.Color.purple()
        else:
            rarity = "📦 XP SUPPLY DROP"
            color = discord.Color.teal()

        embed = discord.Embed(
            title=rarity,
            description=(
                f"A wild supply drop has appeared in chat!\n\n"
                f"• **Potential Reward:** **`+{drop_val} XP`**\n"
                f"• **Status:** Unclaimed\n"
                f"• **Instruction:** Click the button below first to claim it!\n"
                f"*(Disappears <t:{int(time.time()) + DROP_TIMEOUT_SECONDS}:R>)*"
            ),
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Project-Edith • Random Supply Drops")

        view = XpDropClaimView(self, drop_val)
        msg = await channel.send(embed=embed, view=view)
        view.message = msg

    # --- Text Message XP Listener with Random Drop Trigger ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if message.content.startswith("."):
            return

        now = time.time()
        last_time = self.msg_cooldowns.get(message.author.id, 0)
        if now - last_time < MSG_COOLDOWN_SECONDS:
            return

        self.msg_cooldowns[message.author.id] = now
        base_xp = random.randint(MSG_XP_MIN, MSG_XP_MAX)

        await self.bot.db.execute("""
            INSERT INTO levels (user_id, user_name, xp, level, messages_count, voice_minutes)
            VALUES (?, ?, 0, 1, 1, 0)
            ON CONFLICT(user_id) DO UPDATE SET messages_count = messages_count + 1
        """, (message.author.id, message.author.display_name))
        await self.bot.db.commit()

        await self.add_xp(message.author, base_xp, reason="Message XP", channel=message.channel)

        if random.random() < DROP_CHANCE:
            asyncio.create_task(self.trigger_xp_drop(message.channel))

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
        """Iterates over voice channels, awarding XP with Booster bonuses."""
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
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

        booster_badge = "🚀 **Active (1.5x XP)**" if is_server_booster(target) else "None (`1.0x`)"

        embed = discord.Embed(
            title=f"📊 Rank Card — {target.display_name}",
            color=discord.Color.magenta() if is_server_booster(target) else discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🏆 Server Rank", value=f"`#{rank_pos}` of `{len(all_users)}`", inline=True)
        embed.add_field(name="⭐ Level", value=f"`{data['level']} / {MAX_LEVEL}`", inline=True)
        embed.add_field(name="🚀 Booster Multiplier", value=booster_badge, inline=True)
        embed.add_field(name="✨ Current XP", value=f"`{data['xp']} / {needed} XP`", inline=True)
        embed.add_field(name="💬 Messages", value=f"`{data['messages_count']}`", inline=True)
        embed.add_field(name="🎙️ Voice Time", value=f"`{data['voice_minutes']} mins`", inline=True)
        embed.add_field(name="📈 Progress to Level Up", value=f"`{bar}`", inline=False)

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

        booster_badge = "🚀 **Active (1.5x XP)**" if is_server_booster(target) else "None (`1.0x`)"

        embed = discord.Embed(
            title=f"📊 Rank Card — {target.display_name}",
            color=discord.Color.magenta() if is_server_booster(target) else discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🏆 Server Rank", value=f"`#{rank_pos}` of `{len(all_users)}`", inline=True)
        embed.add_field(name="⭐ Level", value=f"`{data['level']} / {MAX_LEVEL}`", inline=True)
        embed.add_field(name="🚀 Booster Multiplier", value=booster_badge, inline=True)
        embed.add_field(name="✨ Current XP", value=f"`{data['xp']} / {needed} XP`", inline=True)
        embed.add_field(name="💬 Messages", value=f"`{data['messages_count']}`", inline=True)
        embed.add_field(name="🎙️ Voice Time", value=f"`{data['voice_minutes']} mins`", inline=True)
        embed.add_field(name="📈 Progress to Level Up", value=f"`{bar}`", inline=False)

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

    # --- Manual Drop Command ---
    @commands.command(name="dropxp")
    @commands.has_permissions(administrator=True)
    async def manual_drop_cmd(self, ctx: commands.Context, amount: int = None):
        if amount and not (10 <= amount <= 1000):
            await ctx.send("❌ XP Drop amount must be between 10 and 1,000 XP.")
            return

        try:
            await ctx.message.delete()
        except Exception:
            pass

        await self.trigger_xp_drop(ctx.channel, amount)

    @app_commands.command(name="dropxp", description="Spawn an interactive XP Drop in the current channel.")
    @app_commands.describe(amount="Custom XP amount (10-1000). Leave blank for random.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_manual_drop(self, interaction: discord.Interaction, amount: int = None):
        if amount and not (10 <= amount <= 1000):
            await interaction.response.send_message("❌ XP Drop amount must be between 10 and 1,000 XP.", ephemeral=True)
            return

        await interaction.response.send_message("🎁 Spawning supply drop...", ephemeral=True)
        await self.trigger_xp_drop(interaction.channel, amount)

    # --- Set Level Command (Testing) ---
    @commands.command(name="setlevel")
    @commands.has_permissions(administrator=True)
    async def set_level_cmd(self, ctx: commands.Context, member: discord.Member, target_level: int):
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

        awarded = await self._assign_milestone_roles(member, target_level)
        msg = f"✅ Set {member.mention}'s rank to **Level {target_level}**."
        if awarded:
            msg += f" Assigned roles: {', '.join(awarded)}"
        await ctx.send(msg)

        # Trigger celebratory announcement directly into the announcement channel
        announcement_id = int(getattr(config, "ANNOUNCEMENT_CHANNEL_ID", 0))
        target_chan = self.bot.get_channel(announcement_id) if announcement_id else ctx.channel
        if target_chan:
            embed = discord.Embed(
                title="🎉 LEVEL ADVANCEMENT!",
                description=f"Congratulations {member.mention}! You advanced to **Level {target_level} / {MAX_LEVEL}**!",
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc)
            )
            if awarded:
                embed.add_field(name="🎖️ Milestone Reward Unlocked!", value=", ".join([f"**{r}**" for r in awarded]), inline=False)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="Project-Edith • Server Leveling Hub")
            try:
                await target_chan.send(content=f"📢 Attention {member.mention}!", embed=embed)
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(Leveling(bot))

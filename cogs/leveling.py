import os
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
BOOSTER_XP_MULTIPLIER = 1.5

DROP_CHANCE = 0.03
DROP_XP_MIN = 10
DROP_XP_MAX = 1000
DROP_TIMEOUT_SECONDS = 90
CLAIM_AUTODELETE_SECONDS = 10

MILESTONE_ROLES = {
    5: ("Bronze Operative", 0xCD7F32),
    10: ("Silver Operative", 0xC0C0C0),
    25: ("Gold Operative", 0xFFD700),
    50: ("Mythic Pinnacle", 0x9B59B6)
}


def calculate_xp_for_level(level: int) -> int:
    if level <= 0:
        return 0
    if level >= MAX_LEVEL:
        return 5 * (MAX_LEVEL ** 2) + (50 * MAX_LEVEL) + 100
    return 5 * (level ** 2) + (50 * level) + 100


def get_progress_bar(current: int, target: int, length: int = 10) -> str:
    if target <= 0:
        return "▰" * length
    ratio = min(max(current / target, 0.0), 1.0)
    filled = int(ratio * length)
    empty = length - filled
    return f"{'▰' * filled}{'▱' * empty} {int(ratio * 100)}%"


def is_server_booster(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    return member.premium_since is not None


class XpDropClaimView(discord.ui.View):
    def __init__(self, cog, xp_amount: int):
        super().__init__(timeout=DROP_TIMEOUT_SECONDS)
        self.cog = cog
        self.xp_amount = xp_amount
        self.claimed = False
        self.message = None

    async def _auto_delete_after_delay(self, msg: discord.Message, delay: int = CLAIM_AUTODELETE_SECONDS):
        await asyncio.sleep(delay)
        try:
            if msg:
                await msg.delete()
        except Exception:
            pass

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
                + (f"🚀 *Booster 1.5x Multiplier applied: Received **+{awarded_xp} XP**!*\n" if is_server_booster(user) else "")
                + f"\n⏳ *This message will automatically disappear in {CLAIM_AUTODELETE_SECONDS} seconds.*"
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"Project-Edith • Purging in {CLAIM_AUTODELETE_SECONDS}s")

        await interaction.response.edit_message(embed=embed, view=self)

        target_msg = self.message or interaction.message
        asyncio.create_task(self._auto_delete_after_delay(target_msg, delay=CLAIM_AUTODELETE_SECONDS))

    async def on_timeout(self):
        if not self.claimed and self.message:
            for child in self.children:
                child.disabled = True
                child.label = "Expired"
            embed = discord.Embed(
                title="📦 XP Drop Expired",
                description="Nobody claimed this supply drop in time.\n\n⏳ *Disappearing in 5 seconds.*",
                color=discord.Color.dark_grey()
            )
            try:
                await self.message.edit(embed=embed, view=self)
                asyncio.create_task(self._auto_delete_after_delay(self.message, delay=5))
            except Exception:
                pass


class Leveling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.msg_cooldowns = {}
        self.voice_tracking = {}
        self.voice_xp_loop.start()
        self.weekly_report_loop.start()

    def cog_unload(self):
        self.voice_xp_loop.cancel()
        self.weekly_report_loop.cancel()

    async def _play_voice_chime(self, member: discord.Member):
        if not member.voice or not member.voice.channel:
            return

        sound_path = getattr(config, "LEVEL_UP_SOUND_FILE", "assets/levelup.mp3")
        if not os.path.exists(sound_path):
            return

        vc_channel = member.voice.channel
        guild = member.guild
        voice_client = guild.voice_client

        try:
            if voice_client is None:
                voice_client = await vc_channel.connect(timeout=10.0, reconnect=False)
            elif voice_client.channel != vc_channel:
                await voice_client.move_to(vc_channel)

            if not voice_client.is_playing():
                source = discord.FFmpegPCMAudio(sound_path)
                voice_client.play(source)

                timeout_counter = 0
                while voice_client.is_playing() and timeout_counter < 16:
                    await asyncio.sleep(0.5)
                    timeout_counter += 1

                await voice_client.disconnect(force=True)
        except Exception as e:
            print(f"[VOICE CHIME NOTICE] Could not play level up audio: {e}")
            if voice_client and voice_client.is_connected():
                try:
                    await voice_client.disconnect(force=True)
                except Exception:
                    pass

    async def _assign_milestone_roles(self, member: discord.Member, current_level: int) -> list[str]:
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
                        continue

                if role not in member.roles:
                    try:
                        await member.add_roles(role, reason=f"Level {milestone_lvl} milestone reached")
                        awarded_role_names.append(role.name)
                    except discord.Forbidden:
                        pass

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

            target_chan = None
            announcement_id = int(getattr(config, "ANNOUNCEMENT_CHANNEL_ID", 0))
            if announcement_id and announcement_id != 0:
                target_chan = self.bot.get_channel(announcement_id)
                if not target_chan:
                    try:
                        target_chan = await self.bot.fetch_channel(announcement_id)
                    except Exception:
                        target_chan = None

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
                        value="You have achieved the ultimate rank of Level 50!",
                        inline=False
                    )

                embed.set_thumbnail(url=user.display_avatar.url)
                embed.set_footer(text="Project-Edith • Server Leveling Hub")

                ping_content = f"📢 Attention {user.mention}!"
                alert_role_name = getattr(config, "LEVEL_UP_PING_ROLE_NAME", None)
                if alert_role_name:
                    alert_role = discord.utils.get(user.guild.roles, name=alert_role_name)
                    if alert_role:
                        ping_content += f" | {alert_role.mention}"

                try:
                    await target_chan.send(
                        content=ping_content,
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(roles=True, users=True)
                    )
                except Exception as e:
                    print(f"[LEVEL UP DISPATCH ERROR] {e}")

            asyncio.create_task(self._play_voice_chime(user))

    async def trigger_xp_drop(self, channel: discord.TextChannel, amount: int = None):
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

    async def generate_weekly_report_embed(self, guild: discord.Guild) -> discord.Embed:
        async with self.bot.db.execute("""
            SELECT user_id, user_name, level, xp, messages_count, voice_minutes
            FROM levels
            ORDER BY level DESC, xp DESC
        """) as cursor:
            all_records = await cursor.fetchall()

        total_members = len(all_records)
        total_messages = sum(r[4] for r in all_records)
        total_voice_mins = sum(r[5] for r in all_records)

        top_typer = max(all_records, key=lambda r: r[4]) if all_records else None
        top_voice = max(all_records, key=lambda r: r[5]) if all_records else None

        embed = discord.Embed(
            title="📈 Project-Edith // Weekly Community Level Report",
            description=(
                f"Performance digest and activity ranking for **{guild.name}** over the past 7 days.\n"
                f"Tracked Operatives: `{total_members}` | Total Messages: `{total_messages}` | Total Voice: `{total_voice_mins} mins`\n"
            ),
            color=discord.Color.from_rgb(46, 204, 113),
            timestamp=datetime.now(timezone.utc)
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        if top_typer and top_typer[4] > 0:
            embed.add_field(name="💬 Chat Champion", value=f"<@{top_typer[0]}> (`{top_typer[4]}` msgs)", inline=True)
        if top_voice and top_voice[5] > 0:
            embed.add_field(name="🎙️ Voice Titan", value=f"<@{top_voice[0]}> (`{top_voice[5]}` mins)", inline=True)

        lines = []
        medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
        for idx, (uid, name, lvl, xp, msgs, voice) in enumerate(all_records[:5]):
            medal = medals[idx]
            lines.append(f"{medal} **{name or 'Operative'}** (<@{uid}>)\n   ↳ Level **{lvl} / {MAX_LEVEL}** • `{xp} XP` • `{msgs} msgs` • `{voice}m VC`")

        if lines:
            embed.add_field(name="🏆 Top 5 Operative Roster", value="\n\n".join(lines), inline=False)
        else:
            embed.add_field(name="🏆 Top 5 Operative Roster", value="*No activity logged this week.*", inline=False)

        embed.add_field(
            name="🚀 Earn XP This Week",
            value="Participate in discussions, join voice lounges, run `.bump` every 2 hours, and catch random XP drops!",
            inline=False
        )
        embed.set_footer(text="Project-Edith • Weekly Automated Digest")
        return embed

    @tasks.loop(hours=1)
    async def weekly_report_loop(self):
        now = datetime.now(timezone.utc)
        if now.weekday() == 6 and now.hour == 0:
            announcement_id = int(getattr(config, "ANNOUNCEMENT_CHANNEL_ID", 0))
            if not announcement_id:
                return

            for guild in self.bot.guilds:
                channel = guild.get_channel(announcement_id)
                if channel:
                    embed = await self.generate_weekly_report_embed(guild)
                    try:
                        await channel.send(content="📊 **The Weekly Server Activity & Level Report is live!**", embed=embed)
                    except Exception as e:
                        print(f"[WEEKLY REPORT ERROR] Could not send report: {e}")

    @weekly_report_loop.before_loop
    async def before_weekly_report(self):
        await self.bot.wait_until_ready()

    @commands.command(name="weeklyreport", aliases=["levelreport", "weekly_report"])
    @commands.has_permissions(administrator=True)
    async def prefix_weekly_report(self, ctx: commands.Context):
        embed = await self.generate_weekly_report_embed(ctx.guild)
        await ctx.send(embed=embed)

    @app_commands.command(name="weekly_report", description="Generate the Weekly Server Level & Activity Report.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_weekly_report(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed = await self.generate_weekly_report_embed(interaction.guild)
        await interaction.followup.send(embed=embed)

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

    @commands.command(name="rank")
    async def rank_cmd(self, ctx: commands.Context, member: discord.Member = None):
        target = member or ctx.author
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

        upcoming = [lvl for lvl in sorted(MILESTONE_ROLES.keys()) if lvl > data["level"]]
        if upcoming:
            embed.add_field(name="🎯 Next Role Unlock", value=f"**{MILESTONE_ROLES[upcoming[0]][0]}** at Level `{upcoming[0]}`", inline=False)
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

        upcoming = [lvl for lvl in sorted(MILESTONE_ROLES.keys()) if lvl > data["level"]]
        if upcoming:
            embed.add_field(name="🎯 Next Role Unlock", value=f"**{MILESTONE_ROLES[upcoming[0]][0]}** at Level `{upcoming[0]}`", inline=False)
        else:
            embed.add_field(name="👑 Rank Status", value="All milestone roles unlocked!", inline=False)

        embed.set_footer(text="Project-Edith • Level Progression")
        await interaction.response.send_message(embed=embed)

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


async def setup(bot):
    await bot.add_cog(Leveling(bot))

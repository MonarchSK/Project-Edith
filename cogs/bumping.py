import time
import asyncio
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone
import config

BUMP_COOLDOWN_SECONDS = 7200      # 2 Hours
PRE_REMINDER_SECONDS = 6300       # 1 Hour 45 Minutes (15 minutes before ready)
BUMP_XP_REWARD = 200              # 200 XP per bump
BUMP_PING_ROLE_NAME = getattr(config, "BUMP_PING_ROLE_NAME", "Bump Pings")


async def get_or_create_bump_role(guild: discord.Guild) -> discord.Role | None:
    role = discord.utils.get(guild.roles, name=BUMP_PING_ROLE_NAME)
    if not role:
        try:
            role = await guild.create_role(
                name=BUMP_PING_ROLE_NAME,
                color=discord.Color.from_rgb(0, 168, 252),
                mentionable=True,
                reason="Automated Bump Notification Role"
            )
        except discord.Forbidden:
            print(f"[BUMP ERROR] Lacking permissions to create role '{BUMP_PING_ROLE_NAME}'.")
            return None
    return role


class BumpRoleToggleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Toggle Bump Pings",
        style=discord.ButtonStyle.secondary,
        emoji="🔔",
        custom_id="btn_toggle_bump_ping_role"
    )
    async def toggle_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        except Exception:
            pass

        guild = interaction.guild
        member = interaction.user

        role = await get_or_create_bump_role(guild)
        if not role:
            msg = "⚠️ Could not manage the Bump Pings role due to missing permissions."
            try:
                await interaction.followup.send(msg, ephemeral=True)
            except Exception:
                await interaction.channel.send(f"{member.mention} {msg}", delete_after=5)
            return

        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Self-service bump ping opt-out")
                await interaction.followup.send("🔕 You have removed the **Bump Pings** role. You will no longer be alerted.", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ Discord rejected role removal. Edith's role must be above the Bump Pings role.", ephemeral=True)
            except Exception:
                pass
        else:
            try:
                await member.add_roles(role, reason="Self-service bump ping opt-in")
                await interaction.followup.send("🔔 **Bump Pings role assigned!** You will be notified 15 minutes before and when the server is ready to bump.", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ Discord rejected role assignment. Edith's role must be above the Bump Pings role.", ephemeral=True)
            except Exception:
                pass


class Bumping(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_bump_reminder.start()

    async def cog_load(self):
        self.bot.add_view(BumpRoleToggleView())

        # Ensure pre_reminder_sent column exists
        try:
            await self.bot.db.execute("ALTER TABLE bump_status ADD COLUMN pre_reminder_sent INTEGER DEFAULT 1")
            await self.bot.db.commit()
        except sqlite3.OperationalError:
            pass

    def cog_unload(self):
        self.check_bump_reminder.cancel()

    async def get_bump_status(self, guild_id: int):
        async with self.bot.db.execute(
            "SELECT last_bump_timestamp, last_bumper_id, reminder_sent, last_channel_id, pre_reminder_sent FROM bump_status WHERE guild_id = ?",
            (guild_id,)
        ) as cursor:
            return await cursor.fetchone()

    def trigger_instant_backup(self, bumper_name: str):
        """Immediately syncs the new bump timestamp into Discord persistent cloud backup."""
        backup_cog = self.bot.get_cog("DiscordMemoryBackup")
        if backup_cog and hasattr(backup_cog, "upload_backup_and_prune"):
            asyncio.create_task(
                backup_cog.upload_backup_and_prune(reason=f"Bump Timestamp Synced ({bumper_name})")
            )

    async def execute_bump(self, guild: discord.Guild, user: discord.Member, channel: discord.TextChannel) -> tuple[bool, str, int]:
        now = int(time.time())
        row = await self.get_bump_status(guild.id)

        if row:
            last_bump = row[0]
            diff = now - last_bump
            if diff < BUMP_COOLDOWN_SECONDS:
                remaining_secs = BUMP_COOLDOWN_SECONDS - diff
                minutes, seconds = divmod(remaining_secs, 60)
                hours, minutes = divmod(minutes, 60)
                time_str = f"{hours}h {minutes}m {seconds}s" if hours > 0 else f"{minutes}m {seconds}s"
                return False, time_str, remaining_secs

        # Persist bump timestamp and reset flags
        await self.bot.db.execute("""
            INSERT INTO bump_status (guild_id, last_bump_timestamp, last_bumper_id, reminder_sent, last_channel_id, pre_reminder_sent)
            VALUES (?, ?, ?, 0, ?, 0)
            ON CONFLICT(guild_id) DO UPDATE SET
                last_bump_timestamp = excluded.last_bump_timestamp,
                last_bumper_id = excluded.last_bumper_id,
                reminder_sent = 0,
                last_channel_id = excluded.last_channel_id,
                pre_reminder_sent = 0
        """, (guild.id, now, user.id, channel.id))
        await self.bot.db.commit()

        # Award bumper XP
        leveling_cog = self.bot.get_cog("Leveling")
        if leveling_cog:
            await leveling_cog.add_xp(user, BUMP_XP_REWARD, reason="Server Bump Reward", channel=channel)

        # Force immediate cloud backup to lock in timestamp
        self.trigger_instant_backup(user.name)

        return True, "Success", 0

    @commands.command(name="bump")
    async def prefix_bump(self, ctx: commands.Context):
        """Bump the server to earn 200 XP and boost server ranking."""
        success, time_str, remaining = await self.execute_bump(ctx.guild, ctx.author, ctx.channel)
        if not success:
            embed = discord.Embed(
                title="⏳ Bump Cooldown Active",
                description=f"This server was already bumped recently!\nYou can bump again in **{time_str}** (<t:{int(time.time()) + remaining}:R>).",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, view=BumpRoleToggleView())
            return

        next_bump_epoch = int(time.time()) + BUMP_COOLDOWN_SECONDS
        embed = discord.Embed(
            title="🚀 SERVER BUMPED SUCCESSFULLY!",
            description=(
                f"**{ctx.guild.name}** has been boosted to the top of the server list!\n\n"
                f"• **Bumper:** {ctx.author.mention}\n"
                f"• **XP Awarded:** `+{BUMP_XP_REWARD} XP` *(Boosters get 300 XP)*\n"
                f"• **Timestamp Cloud-Synced:** 💾 Stored\n"
                f"• **Next Bump:** <t:{next_bump_epoch}:t> (<t:{next_bump_epoch}:R>)\n"
                f"• **15-Min Warning:** <t:{next_bump_epoch - 900}:R>"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        embed.set_footer(text="Project-Edith • Persistent Bump Engine")

        await ctx.send(embed=embed, view=BumpRoleToggleView())

    @app_commands.command(name="bump", description="Bump the server to earn 200 XP and boost ranking.")
    async def slash_bump(self, interaction: discord.Interaction):
        # 1. Attempt non-blocking defer with fallback handling
        deferred = False
        try:
            await interaction.response.defer()
            deferred = True
        except discord.NotFound:
            # Token expired before execution reached Render
            deferred = False
        except discord.HTTPException:
            deferred = interaction.response.is_done()

        success, time_str, remaining = await self.execute_bump(interaction.guild, interaction.user, interaction.channel)

        if not success:
            embed = discord.Embed(
                title="⏳ Bump Cooldown Active",
                description=f"This server was already bumped recently!\nYou can bump again in **{time_str}** (<t:{int(time.time()) + remaining}:R>).",
                color=discord.Color.red()
            )
            if deferred:
                try:
                    await interaction.followup.send(embed=embed, view=BumpRoleToggleView())
                    return
                except Exception:
                    pass
            await interaction.channel.send(embed=embed, view=BumpRoleToggleView())
            return

        next_bump_epoch = int(time.time()) + BUMP_COOLDOWN_SECONDS
        embed = discord.Embed(
            title="🚀 SERVER BUMPED SUCCESSFULLY!",
            description=(
                f"**{interaction.guild.name}** has been boosted to the top of the server list!\n\n"
                f"• **Bumper:** {interaction.user.mention}\n"
                f"• **XP Awarded:** `+{BUMP_XP_REWARD} XP` *(Boosters get 300 XP)*\n"
                f"• **Timestamp Cloud-Synced:** 💾 Stored\n"
                f"• **Next Bump:** <t:{next_bump_epoch}:t> (<t:{next_bump_epoch}:R>)\n"
                f"• **15-Min Warning:** <t:{next_bump_epoch - 900}:R>"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.set_footer(text="Project-Edith • Persistent Bump Engine")

        # 2. Try followup dispatch; if token is dead, cleanly dispatch straight to channel
        if deferred:
            try:
                await interaction.followup.send(embed=embed, view=BumpRoleToggleView())
                return
            except Exception:
                pass

        await interaction.channel.send(embed=embed, view=BumpRoleToggleView())

    # --- Background Loop: Handles Exact Timestamp Alignment Even Across Restarts ---
    @tasks.loop(seconds=15)
    async def check_bump_reminder(self):
        now = int(time.time())
        try:
            async with self.bot.db.execute("""
                SELECT guild_id, last_bump_timestamp, last_bumper_id, last_channel_id, reminder_sent, pre_reminder_sent
                FROM bump_status
            """) as cursor:
                rows = await cursor.fetchall()
        except Exception:
            return

        for gid, last_bump, bumper_id, chan_id, reminder_sent, pre_reminder_sent in rows:
            if last_bump == 0:
                continue

            guild = self.bot.get_guild(gid)
            if not guild:
                continue

            channel = guild.get_channel(chan_id)
            if not channel:
                continue

            diff = now - last_bump
            bump_role = await get_or_create_bump_role(guild)
            role_ping = bump_role.mention if bump_role else ""

            # 1. 15-Minute Advance Reminder (Between 1h45m and 2h)
            if diff >= PRE_REMINDER_SECONDS and diff < BUMP_COOLDOWN_SECONDS and pre_reminder_sent == 0:
                await self.bot.db.execute("UPDATE bump_status SET pre_reminder_sent = 1 WHERE guild_id = ?", (gid,))
                await self.bot.db.commit()

                ready_epoch = last_bump + BUMP_COOLDOWN_SECONDS
                embed = discord.Embed(
                    title="⏳ 15-Minute Bump Warning!",
                    description=(
                        f"The server bump cooldown will expire in **15 minutes** (<t:{ready_epoch}:R>)!\n\n"
                        f"Get ready to bump with `.bump` or `/bump` to claim **+{BUMP_XP_REWARD} XP**."
                    ),
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.set_footer(text="Project-Edith • Bump Alert")

                try:
                    await channel.send(
                        content=f"⏰ {role_ping}",
                        embed=embed,
                        view=BumpRoleToggleView(),
                        allowed_mentions=discord.AllowedMentions(roles=True, users=True)
                    )
                except Exception as e:
                    print(f"[BUMP PRE-REMINDER ERROR] {e}")

            # 2. 2-Hour Cooldown Ready Notification
            elif diff >= BUMP_COOLDOWN_SECONDS and reminder_sent == 0:
                await self.bot.db.execute("UPDATE bump_status SET reminder_sent = 1, pre_reminder_sent = 1 WHERE guild_id = ?", (gid,))
                await self.bot.db.commit()

                embed = discord.Embed(
                    title="🔔 Server Ready to Bump!",
                    description=(
                        "The 2-hour cooldown has expired!\n\n"
                        f"Use `.bump` or `/bump` now to claim **+{BUMP_XP_REWARD} XP** and keep **{guild.name}** at the top!"
                    ),
                    color=discord.Color.from_rgb(0, 168, 252),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.set_footer(text="Click 'Toggle Bump Pings' below to manage your alerts")

                bumper = guild.get_member(bumper_id)
                ping_content = f"📢 {role_ping}"
                if bumper and bump_role and (bump_role not in bumper.roles):
                    ping_content += f" | {bumper.mention}"

                try:
                    await channel.send(
                        content=ping_content,
                        embed=embed,
                        view=BumpRoleToggleView(),
                        allowed_mentions=discord.AllowedMentions(roles=True, users=True)
                    )
                except Exception as e:
                    print(f"[BUMP READY REMINDER ERROR] {e}")

    @check_bump_reminder.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Bumping(bot))

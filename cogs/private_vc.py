import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import config

PRIVATE_VC_CATEGORY_NAME = "🔊 PRIVATE VOICE LOUNGES"
PRIVATE_VC_PANEL_CHANNEL_NAME = "private-vc"
UNJOINED_CLEANUP_SECONDS = 60


def is_vc_authority(member: discord.Member) -> bool:
    """Checks if a member is an Administrator, Director, or Authority role holder."""
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    director_role = getattr(config, "DIRECTOR_ROLE_NAME", "Director").lower()
    allowed_roles = {director_role, "authority", "admin", "administrator"}
    return any(r.name.lower() in allowed_roles for r in member.roles)


async def get_or_create_vc_category(guild: discord.Guild) -> discord.CategoryChannel | None:
    category = discord.utils.get(guild.categories, name=PRIVATE_VC_CATEGORY_NAME)
    if not category:
        try:
            category = await guild.create_category(name=PRIVATE_VC_CATEGORY_NAME)
        except Exception:
            category = None
    return category


class InviteMemberSelect(discord.ui.UserSelect):
    def __init__(self, cog, voice_channel: discord.VoiceChannel):
        super().__init__(
            placeholder="Select members to invite to your Private VC...",
            min_values=1,
            max_values=5,
            custom_id="private_vc_invite_select"
        )
        self.cog = cog
        self.voice_channel = voice_channel

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self.voice_channel or self.voice_channel not in interaction.guild.voice_channels:
            await interaction.followup.send("❌ That Private VC no longer exists.", ephemeral=True)
            return

        invited_mentions = []
        for user in self.values:
            member = interaction.guild.get_member(user.id)
            if not member or member.bot:
                continue
            try:
                await self.voice_channel.set_permissions(
                    member,
                    view_channel=True,
                    connect=True,
                    speak=True,
                    stream=True,
                    use_voice_activation=True,
                    reason=f"Invited to Private VC by {interaction.user.name}"
                )
                invited_mentions.append(member.mention)
            except discord.Forbidden:
                pass

        if invited_mentions:
            await interaction.followup.send(
                f"✅ Granted access to {', '.join(invited_mentions)} for {self.voice_channel.mention}!",
                ephemeral=True
            )
        else:
            await interaction.followup.send("⚠️ Could not invite the selected user(s).", ephemeral=True)


class RevokeMemberSelect(discord.ui.UserSelect):
    def __init__(self, cog, voice_channel: discord.VoiceChannel, owner_id: int):
        super().__init__(
            placeholder="Select a member to remove/kick from your Private VC...",
            min_values=1,
            max_values=5,
            custom_id="private_vc_revoke_select"
        )
        self.cog = cog
        self.voice_channel = voice_channel
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self.voice_channel or self.voice_channel not in interaction.guild.voice_channels:
            await interaction.followup.send("❌ That Private VC no longer exists.", ephemeral=True)
            return

        removed_mentions = []
        blocked_staff = []

        for user in self.values:
            member = interaction.guild.get_member(user.id)
            if not member:
                continue

            # Prevent removing the room owner or high authority staff (unless actor is also authority)
            if member.id == self.owner_id and not is_vc_authority(interaction.user):
                continue
            if is_vc_authority(member) and not interaction.user.guild_permissions.administrator:
                blocked_staff.append(member.mention)
                continue

            try:
                await self.voice_channel.set_permissions(
                    member,
                    overwrite=None,
                    reason=f"Access revoked by {interaction.user.name}"
                )
                if member in self.voice_channel.members:
                    await member.move_to(None, reason="Removed from Private VC")
                removed_mentions.append(member.mention)
            except discord.Forbidden:
                pass

        msg_parts = []
        if removed_mentions:
            msg_parts.append(f"🚫 Removed {', '.join(removed_mentions)} from {self.voice_channel.mention}.")
        if blocked_staff:
            msg_parts.append(f"⛔ Cannot remove Authority/Director staff: {', '.join(blocked_staff)}.")
        if not msg_parts:
            msg_parts.append("⚠️ No changes were applied.")

        await interaction.followup.send("\n".join(msg_parts), ephemeral=True)


class CreatePrivateVCModal(discord.ui.Modal, title="🔒 Create Private Voice Channel"):
    room_name = discord.ui.TextInput(
        label="Voice Channel Name (Optional)",
        placeholder="Leave blank for default (e.g. Lock's Lounge)",
        max_length=32,
        required=False
    )
    user_limit = discord.ui.TextInput(
        label="User Limit (0-99, 0 = Unlimited)",
        placeholder="e.g. 5 (Leave blank for 0)",
        max_length=2,
        required=False
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user

        existing_vc = await self.cog.get_user_owned_vc(guild, member.id)
        if existing_vc:
            await interaction.followup.send(
                f"⚠️ You already have an active Private VC: {existing_vc.mention}.\n"
                f"Use the **Invite** or **Delete VC** buttons on the panel to manage it.",
                ephemeral=True
            )
            return

        limit = 0
        if self.user_limit.value.strip():
            try:
                limit = max(0, min(99, int(self.user_limit.value.strip())))
            except ValueError:
                limit = 0

        custom_title = self.room_name.value.strip()
        vc_name = f"🔒 {custom_title}" if custom_title else f"🔒 {member.display_name}'s VC"

        category = await get_or_create_vc_category(guild)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                connect=False,
                send_messages=False
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                manage_channels=True,
                manage_permissions=True,
                move_members=True
            ),
            member: discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                use_voice_activation=True
            )
        }

        # Grant join & management access to Director, Authority, and Admin roles
        director_name = getattr(config, "DIRECTOR_ROLE_NAME", "Director").lower()
        for role in guild.roles:
            if role.permissions.administrator or role.name.lower() in {director_name, "authority", "admin", "administrator"}:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                    speak=True,
                    stream=True,
                    move_members=True,
                    manage_channels=True
                )

        try:
            vc = await guild.create_voice_channel(
                name=vc_name[:32],
                category=category,
                user_limit=limit,
                overwrites=overwrites,
                reason=f"Private VC created by {member.name}"
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create Private VC: `{e}`", ephemeral=True)
            return

        await self.cog.register_vc(vc.id, guild.id, member.id)

        # If the user is already in another voice channel, move them in automatically
        if member.voice and member.voice.channel:
            try:
                await member.move_to(vc, reason="Moved creator into their new Private VC")
            except Exception:
                pass
        else:
            # Schedule cleanup if nobody joins within 60 seconds
            asyncio.create_task(self.cog.cleanup_if_unjoined(vc))

        view = discord.ui.View(timeout=120)
        view.add_item(InviteMemberSelect(self.cog, vc))

        await interaction.followup.send(
            f"✅ **Private VC Created:** {vc.mention}!\n"
            f"• **Access:** Locked to you, your invitees, and Server Authority.\n"
            f"• **Auto-Delete:** Channel deletes automatically once everyone leaves (or in `{UNJOINED_CLEANUP_SECONDS}s` if nobody connects).\n\n"
            f"👇 **Select members below to invite them right now:**",
            view=view,
            ephemeral=True
        )


class StaffTargetVCSelect(discord.ui.ChannelSelect):
    def __init__(self, cog, mode: str):
        super().__init__(
            placeholder=f"Authority Override: Select a Private VC to {mode}...",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1
        )
        self.cog = cog
        self.mode = mode

    async def callback(self, interaction: discord.Interaction):
        target_chan = self.values[0]
        vc = interaction.guild.get_channel(target_chan.id)
        if not vc or not isinstance(vc, discord.VoiceChannel):
            await interaction.response.send_message("❌ Voice channel not found.", ephemeral=True)
            return

        owner_id = await self.cog.get_vc_owner(vc.id)
        if not owner_id:
            await interaction.response.send_message("❌ That channel is not a tracked Private VC.", ephemeral=True)
            return

        if self.mode == "delete":
            await interaction.response.defer(ephemeral=True)
            await self.cog.delete_private_vc(vc, reason=f"Removed by Authority ({interaction.user.name})")
            await interaction.followup.send(f"🗑️ Private VC **`{vc.name}`** has been terminated.", ephemeral=True)
        elif self.mode == "invite":
            view = discord.ui.View(timeout=120)
            view.add_item(InviteMemberSelect(self.cog, vc))
            await interaction.response.edit_message(content=f"Select members to invite to {vc.mention}:", view=view)
        elif self.mode == "remove_user":
            view = discord.ui.View(timeout=120)
            view.add_item(RevokeMemberSelect(self.cog, vc, owner_id))
            await interaction.response.edit_message(content=f"Select members to kick/revoke from {vc.mention}:", view=view)


class PrivateVCPanelView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Create Private VC",
        style=discord.ButtonStyle.success,
        emoji="➕",
        custom_id="btn_pvc_create",
        row=0
    )
    async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreatePrivateVCModal(self.cog))

    @discord.ui.button(
        label="Invite Member",
        style=discord.ButtonStyle.primary,
        emoji="📩",
        custom_id="btn_pvc_invite",
        row=0
    )
    async def invite_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = await self.cog.get_user_owned_vc(interaction.guild, interaction.user.id)
        if vc:
            view = discord.ui.View(timeout=120)
            view.add_item(InviteMemberSelect(self.cog, vc))
            await interaction.response.send_message(
                f"📩 Select members to grant access to {vc.mention}:",
                view=view,
                ephemeral=True
            )
            return

        if is_vc_authority(interaction.user):
            view = discord.ui.View(timeout=120)
            view.add_item(StaffTargetVCSelect(self.cog, mode="invite"))
            await interaction.response.send_message(
                "🛡️ **Authority Override:** You don't own an active VC. Choose a Private VC to invite members into:",
                view=view,
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "❌ You do not currently own an active Private VC. Click **Create Private VC** first!",
            ephemeral=True
        )

    @discord.ui.button(
        label="Remove Member",
        style=discord.ButtonStyle.secondary,
        emoji="🚫",
        custom_id="btn_pvc_remove_user",
        row=0
    )
    async def remove_user_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = await self.cog.get_user_owned_vc(interaction.guild, interaction.user.id)
        if vc:
            view = discord.ui.View(timeout=120)
            view.add_item(RevokeMemberSelect(self.cog, vc, interaction.user.id))
            await interaction.response.send_message(
                f"🚫 Select members to kick and revoke from {vc.mention}:",
                view=view,
                ephemeral=True
            )
            return

        if is_vc_authority(interaction.user):
            view = discord.ui.View(timeout=120)
            view.add_item(StaffTargetVCSelect(self.cog, mode="remove_user"))
            await interaction.response.send_message(
                "🛡️ **Authority Override:** Choose a Private VC to remove a member from:",
                view=view,
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "❌ You do not currently own an active Private VC.",
            ephemeral=True
        )

    @discord.ui.button(
        label="Delete VC",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        custom_id="btn_pvc_delete",
        row=0
    )
    async def delete_vc_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1. If the user is an Authority/Director/Admin currently sitting inside a tracked Private VC, delete that VC
        if is_vc_authority(interaction.user) and interaction.user.voice and interaction.user.voice.channel:
            current_vc = interaction.user.voice.channel
            if await self.cog.get_vc_owner(current_vc.id):
                await interaction.response.defer(ephemeral=True)
                await self.cog.delete_private_vc(current_vc, reason=f"Deleted by {interaction.user.name}")
                await interaction.followup.send(f"🗑️ Private VC **`{current_vc.name}`** deleted.", ephemeral=True)
                return

        # 2. Check if the user owns an active Private VC
        vc = await self.cog.get_user_owned_vc(interaction.guild, interaction.user.id)
        if vc:
            await interaction.response.defer(ephemeral=True)
            await self.cog.delete_private_vc(vc, reason=f"Owner {interaction.user.name} deleted VC")
            await interaction.followup.send(f"🗑️ Your Private VC **`{vc.name}`** has been deleted.", ephemeral=True)
            return

        # 3. Allow Authority/Directors/Admins to pick any active Private VC to delete
        if is_vc_authority(interaction.user):
            view = discord.ui.View(timeout=120)
            view.add_item(StaffTargetVCSelect(self.cog, mode="delete"))
            await interaction.response.send_message(
                "🛡️ **Authority Override:** Select a Private VC to force-delete:",
                view=view,
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "❌ You do not have an active Private VC to delete.",
            ephemeral=True
        )


class PrivateVC(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_view = PrivateVCPanelView(self)

    async def cog_load(self):
        self.bot.add_view(self.active_view)
        if hasattr(self.bot, "db") and self.bot.db:
            await self.init_db()
        asyncio.create_task(self.reconcile_empty_vcs_on_boot())

    async def init_db(self):
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS private_vcs (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        await self.bot.db.commit()

    async def register_vc(self, channel_id: int, guild_id: int, owner_id: int):
        await self.init_db()
        now = int(datetime.now(timezone.utc).timestamp())
        await self.bot.db.execute("""
            INSERT OR REPLACE INTO private_vcs (channel_id, guild_id, owner_id, created_at)
            VALUES (?, ?, ?, ?)
        """, (channel_id, guild_id, owner_id, now))
        await self.bot.db.commit()

    async def get_vc_owner(self, channel_id: int) -> int | None:
        if not hasattr(self.bot, "db") or not self.bot.db:
            return None
        await self.init_db()
        async with self.bot.db.execute("SELECT owner_id FROM private_vcs WHERE channel_id = ?", (channel_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

    async def get_user_owned_vc(self, guild: discord.Guild, user_id: int) -> discord.VoiceChannel | None:
        if not hasattr(self.bot, "db") or not self.bot.db:
            return None
        await self.init_db()
        async with self.bot.db.execute(
            "SELECT channel_id FROM private_vcs WHERE guild_id = ? AND owner_id = ?",
            (guild.id, user_id)
        ) as cur:
            rows = await cur.fetchall()

        for (cid,) in rows:
            vc = guild.get_channel(cid)
            if vc and isinstance(vc, discord.VoiceChannel):
                return vc
            else:
                await self.bot.db.execute("DELETE FROM private_vcs WHERE channel_id = ?", (cid,))
        await self.bot.db.commit()
        return None

    async def delete_private_vc(self, vc: discord.VoiceChannel, reason: str = "Private VC empty"):
        if hasattr(self.bot, "db") and self.bot.db:
            await self.bot.db.execute("DELETE FROM private_vcs WHERE channel_id = ?", (vc.id,))
            await self.bot.db.commit()
        try:
            await vc.delete(reason=reason)
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"[PRIVATE VC DELETE ERROR] {e}")

    async def cleanup_if_unjoined(self, vc: discord.VoiceChannel):
        await asyncio.sleep(UNJOINED_CLEANUP_SECONDS)
        if vc and vc in vc.guild.voice_channels:
            human_members = [m for m in vc.members if not m.bot]
            if not human_members:
                await self.delete_private_vc(vc, reason="Creator did not connect within 60 seconds")

    async def reconcile_empty_vcs_on_boot(self):
        """Cleans up any orphaned or empty private VCs after a container restart."""
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)
        if not hasattr(self.bot, "db") or not self.bot.db:
            return
        await self.init_db()

        async with self.bot.db.execute("SELECT channel_id, guild_id FROM private_vcs") as cur:
            rows = await cur.fetchall()

        for cid, gid in rows:
            guild = self.bot.get_guild(gid)
            if not guild:
                continue
            vc = guild.get_channel(cid)
            if not vc or not isinstance(vc, discord.VoiceChannel):
                await self.bot.db.execute("DELETE FROM private_vcs WHERE channel_id = ?", (cid,))
                await self.bot.db.commit()
            else:
                human_members = [m for m in vc.members if not m.bot]
                if not human_members:
                    await self.delete_private_vc(vc, reason="Auto-cleanup on startup: VC empty")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # Trigger when a user leaves or moves out of a voice channel
        if before.channel and before.channel != after.channel:
            owner_id = await self.get_vc_owner(before.channel.id)
            if owner_id is not None:
                human_members = [m for m in before.channel.members if not m.bot]
                if len(human_members) == 0:
                    await self.delete_private_vc(before.channel, reason="All users left Private VC")

    def create_panel_embed(self, guild: discord.Guild) -> discord.Embed:
        director_name = getattr(config, "DIRECTOR_ROLE_NAME", "Director")
        embed = discord.Embed(
            title="🔒 Private Voice Lounge Terminal",
            description=(
                "Create your own locked, invite-only voice channel on demand.\n\n"
                "**Security & Access Rules:**\n"
                "• **Invite-Only:** Only **you** and **members you explicitly invite** can connect.\n"
                f"• **Staff Clearance:** **Administrators**, **@{director_name}**, and **Authority** roles have full clearance to join or remove any Private VC.\n"
                "• **Auto-Cleanup:** Your voice channel is automatically deleted the moment the last member leaves."
            ),
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=datetime.now(timezone.utc)
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if getattr(config, "TICKET_BANNER", None):
            embed.set_image(url=config.TICKET_BANNER)

        embed.add_field(
            name="🎛️ Terminal Controls",
            value=(
                "• **➕ Create Private VC:** Launch your locked voice room.\n"
                "• **📩 Invite Member:** Grant specific members permission to join.\n"
                "• **🚫 Remove Member:** Kick a user from your VC and revoke their invite.\n"
                "• **🗑️ Delete VC:** Immediately close your VC (or Authority override)."
            ),
            inline=False
        )
        embed.set_footer(text="Project-Edith • Private Voice Matrix")
        return embed

    async def provision_private_vc_hub(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Creates the #private-vc text channel (read-only for members, interactive buttons enabled) and posts the panel."""
        category = await get_or_create_vc_category(guild)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=False,
                read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
                embed_links=True,
                manage_messages=True,
                manage_channels=True
            )
        }

        channel = discord.utils.get(guild.text_channels, name=PRIVATE_VC_PANEL_CHANNEL_NAME)
        if not channel:
            try:
                channel = await guild.create_text_channel(
                    name=PRIVATE_VC_PANEL_CHANNEL_NAME,
                    category=category,
                    overwrites=overwrites,
                    topic="Private Voice Channel Terminal. Use the panel below to create and manage your locked VC."
                )
            except Exception as e:
                print(f"[PRIVATE VC HUB ERROR] {e}")
                return None
        else:
            try:
                await channel.edit(category=category, overwrites=overwrites)
                await channel.purge(limit=20, check=lambda m: m.author == self.bot.user)
            except Exception:
                pass

        embed = self.create_panel_embed(guild)
        await channel.send(embed=embed, view=self.active_view)
        return channel

    @commands.command(name="private_vc_panel", aliases=["pvc_panel", "setup_pvc"])
    @commands.has_permissions(administrator=True)
    async def prefix_pvc_panel(self, ctx: commands.Context):
        """Creates the #private-vc channel and deploys the Private VC control panel."""
        try:
            await ctx.message.delete()
        except Exception:
            pass
        channel = await self.provision_private_vc_hub(ctx.guild)
        if channel:
            await ctx.send(f"✅ Private VC hub provisioned in {channel.mention}!", delete_after=6)
        else:
            await ctx.send("❌ Failed to create `#private-vc`. Check Edith's `Manage Channels` permission.", delete_after=6)

    @app_commands.command(name="private_vc_panel", description="Create the #private-vc channel and deploy the Private VC panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_pvc_panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel = await self.provision_private_vc_hub(interaction.guild)
        if channel:
            await interaction.followup.send(f"✅ Private VC hub deployed in {channel.mention}!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to provision `#private-vc`. Check bot permissions.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateVC(bot))

import re
import discord
from discord import app_commands
from discord.ext import commands
import config


class HexColorModal(discord.ui.Modal, title="🎨 Custom Color Plugin"):
    hex_code = discord.ui.TextInput(
        label="Hex Color Code",
        placeholder="#RRGGBB (e.g. #FF5733 or #3498DB)",
        min_length=3,
        max_length=7,
        required=True
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        raw_hex = self.hex_code.value.strip().lstrip("#")

        # Support 3-digit shorthand (e.g. F00 -> FF0000)
        if len(raw_hex) == 3:
            raw_hex = "".join([c * 2 for c in raw_hex])

        if not re.fullmatch(r"^[0-9a-fA-F]{6}$", raw_hex):
            await interaction.followup.send("❌ Invalid hex format. Please use standard 6-digit hex codes like `#3498DB` or `#FF5733`.", ephemeral=True)
            return

        try:
            color_int = int(raw_hex, 16)
        except ValueError:
            await interaction.followup.send("❌ Could not parse color code.", ephemeral=True)
            return

        # Discord interprets exact 0 (#000000) as transparent default color
        if color_int == 0:
            color_int = 0x000001

        role_name = f"Custom #{raw_hex.upper()}"
        member = interaction.user

        # Strip previous colors and clean up unused custom roles
        await self.cog.strip_existing_color_roles(member, purge_empty_custom=True)

        guild = interaction.guild
        custom_role = discord.utils.get(guild.roles, name=role_name)

        if not custom_role:
            try:
                custom_role = await guild.create_role(
                    name=role_name,
                    color=discord.Color(color_int),
                    reason=f"Custom color for {member.name}"
                )
                # Position color role beneath the bot's highest role so it shows above standard member roles
                bot_top_role = guild.me.top_role
                if bot_top_role and bot_top_role.position > 1:
                    target_pos = max(1, bot_top_role.position - 1)
                    await custom_role.edit(position=target_pos)
            except discord.Forbidden:
                await interaction.followup.send("⚠️ Bot lacks permission to create or position roles.", ephemeral=True)
                return
            except Exception as e:
                print(f"[HEX ROLE CREATION ERROR] {e}")

        try:
            await member.add_roles(custom_role, reason="User applied custom hex color")
            await interaction.followup.send(f"✨ Custom color **#{raw_hex.upper()}** assigned successfully!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("⚠️ Failed assigning role. Ensure Edith's role is positioned above the color roles.", ephemeral=True)


class ColorDropdown(discord.ui.Select):
    def __init__(self, cog):
        self.cog = cog
        options = []
        palette = getattr(config, "COLOR_PALETTE", {})
        for name, data in palette.items():
            emoji = data[1] if isinstance(data, (list, tuple)) and len(data) > 1 else "🎨"
            options.append(
                discord.SelectOption(
                    label=name,
                    value=name,
                    emoji=emoji,
                    description=f"Change username to {name}"
                )
            )

        options.append(
            discord.SelectOption(
                label="Clear Color",
                value="REMOVE",
                emoji="🧹",
                description="Remove color role and restore default"
            )
        )

        super().__init__(
            placeholder="Select a preset color...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="color_palette_select"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        choice = self.values[0]
        member = interaction.user

        await self.cog.strip_existing_color_roles(member, purge_empty_custom=True)

        if choice == "REMOVE":
            await interaction.followup.send("🧹 Color role removed. Restored server default.", ephemeral=True)
            return

        guild = interaction.guild
        target_role = discord.utils.get(guild.roles, name=choice)
        palette = getattr(config, "COLOR_PALETTE", {})

        if not target_role and choice in palette:
            hex_val, _ = palette[choice]
            try:
                target_role = await guild.create_role(
                    name=choice,
                    color=discord.Color(hex_val),
                    reason="Auto-created preset color role"
                )
                bot_top_role = guild.me.top_role
                if bot_top_role and bot_top_role.position > 1:
                    target_pos = max(1, bot_top_role.position - 1)
                    await target_role.edit(position=target_pos)
            except discord.Forbidden:
                await interaction.followup.send("⚠️ Missing `Manage Roles` permission to create preset color.", ephemeral=True)
                return

        if not target_role:
            await interaction.followup.send("❌ Target color role could not be resolved.", ephemeral=True)
            return

        try:
            await member.add_roles(target_role, reason="User selected preset color")
            emoji = palette.get(choice, (None, "✨"))[1]
            await interaction.followup.send(f"{emoji} Equipped **{choice}** role!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("⚠️ Hierarchy error: Move Edith's bot role higher than color roles.", ephemeral=True)


class ColorPluginPanel(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(ColorDropdown(cog))

    @discord.ui.button(label="Custom Hex Pop-up", style=discord.ButtonStyle.secondary, emoji="🎛️", custom_id="custom_hex_btn")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HexColorModal(self.cog))

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, emoji="❌", custom_id="reset_color_btn")
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.cog.strip_existing_color_roles(interaction.user, purge_empty_custom=True)
        await interaction.followup.send("Color reset to default.", ephemeral=True)


class ColorRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_view = ColorPluginPanel(self)

    async def cog_load(self):
        """Self-registers persistent view once to ensure buttons and select menus stay alive after reboot."""
        self.bot.add_view(self.active_view)

    async def strip_existing_color_roles(self, member: discord.Member, purge_empty_custom: bool = False):
        palette = getattr(config, "COLOR_PALETTE", {})
        to_strip = [r for r in member.roles if r.name in palette or r.name.startswith("Custom #")]

        if to_strip:
            try:
                await member.remove_roles(*to_strip, reason="Clearing color roles")
            except discord.Forbidden:
                pass

        # Clean up empty custom roles to prevent hitting Discord's 250 role limit
        if purge_empty_custom and member.guild.me.guild_permissions.manage_roles:
            for role in to_strip:
                if role.name.startswith("Custom #"):
                    # Check if any remaining member holds this custom role
                    remaining_members = [m for m in role.members if m.id != member.id]
                    if not remaining_members:
                        try:
                            await role.delete(reason="Purging unused custom hex role")
                        except Exception:
                            pass

    def create_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎨 Color Studio Plugin",
            description="Select a preset color or open the custom modal dialog for exact hex codes.",
            color=discord.Color.from_rgb(46, 204, 113)
        )
        if getattr(config, "DEFAULT_ICON", None):
            embed.set_thumbnail(url=config.DEFAULT_ICON)
        if getattr(config, "COLOR_BANNER", None):
            embed.set_image(url=config.COLOR_BANNER)

        palette = getattr(config, "COLOR_PALETTE", {})
        palette_list = "\n".join([f"{emoji} **{name}**" for name, (_, emoji) in palette.items()])
        embed.add_field(name="Available Presets", value=palette_list or "*None configured*", inline=True)
        embed.add_field(
            name="Features",
            value="• Modal pop-up window\n• Auto-creates & cleans roles\n• Custom Hex supported",
            inline=True
        )
        embed.set_footer(text="Color Studio Plugin • Persistent UI")
        return embed

    @commands.command(name="color_panel")
    @commands.has_permissions(manage_roles=True)
    async def prefix_panel(self, ctx: commands.Context):
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await ctx.channel.send(embed=self.create_panel_embed(), view=self.active_view)

    @app_commands.command(name="color_panel", description="Deploy the Color Studio Plugin.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def slash_panel(self, interaction: discord.Interaction):
        await interaction.channel.send(embed=self.create_panel_embed(), view=self.active_view)
        await interaction.response.send_message("Color panel posted.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ColorRoles(bot))

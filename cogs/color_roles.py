import discord
from discord import app_commands
from discord.ext import commands
import config

class HexColorModal(discord.ui.Modal, title="🎨 Custom Color Plugin"):
    hex_code = discord.ui.TextInput(
        label="Hex Color Code", placeholder="#RRGGBB (e.g. #FF5733)", min_length=6, max_length=7, required=True
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        raw_hex = self.hex_code.value.strip().lstrip("#")
        try:
            color_int = int(raw_hex, 16)
        except ValueError:
            await interaction.response.send_message("❌ Invalid hex format. Example: `#3498DB`", ephemeral=True)
            return

        role_name = f"Custom #{raw_hex.upper()}"
        member = interaction.user
        await self.cog.strip_existing_color_roles(member)

        custom_role = discord.utils.get(interaction.guild.roles, name=role_name)
        if not custom_role:
            try:
                custom_role = await interaction.guild.create_role(
                    name=role_name, color=discord.Color(color_int), reason=f"Custom color for {member.name}"
                )
            except discord.Forbidden:
                await interaction.response.send_message("⚠️ Bot lacks permission to create roles.", ephemeral=True)
                return

        try:
            await member.add_roles(custom_role)
            await interaction.response.send_message(f"✨ Custom color **#{raw_hex.upper()}** assigned!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("⚠️ Failed assigning role due to hierarchy.", ephemeral=True)

class ColorDropdown(discord.ui.Select):
    def __init__(self, cog):
        self.cog = cog
        options = [
            discord.SelectOption(label=name, value=name, emoji=emoji, description=f"Change username to {name}")
            for name, (_, emoji) in config.COLOR_PALETTE.items()
        ]
        options.append(discord.SelectOption(label="Clear Color", value="REMOVE", emoji="🧹", description="Remove color role"))
        super().__init__(placeholder="Select a preset color...", min_values=1, max_values=1, options=options, custom_id="color_palette_select")

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        member = interaction.user
        await self.cog.strip_existing_color_roles(member)

        if choice == "REMOVE":
            await interaction.response.send_message("🧹 Color role removed.", ephemeral=True)
            return

        target_role = discord.utils.get(interaction.guild.roles, name=choice)
        if not target_role:
            hex_val, _ = config.COLOR_PALETTE[choice]
            try:
                target_role = await interaction.guild.create_role(name=choice, color=discord.Color(hex_val), reason="Auto-created color role")
            except discord.Forbidden:
                await interaction.response.send_message("⚠️ Missing `Manage Roles` permission.", ephemeral=True)
                return

        try:
            await member.add_roles(target_role)
            emoji = config.COLOR_PALETTE[choice][1]
            await interaction.response.send_message(f"{emoji} Equipped **{choice}** role!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("⚠️ Hierarchy error: Move bot role higher.", ephemeral=True)

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
        await self.cog.strip_existing_color_roles(interaction.user)
        await interaction.response.send_message("Color reset to default.", ephemeral=True)

class ColorRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def strip_existing_color_roles(self, member: discord.Member):
        to_strip = [r for r in member.roles if r.name in config.COLOR_PALETTE or r.name.startswith("Custom #")]
        if to_strip:
            await member.remove_roles(*to_strip)

    def create_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎨 Color Studio Plugin",
            description="Select a preset color or open the custom modal dialog for exact hex codes.",
            color=discord.Color.from_rgb(46, 204, 113)
        )
        embed.set_thumbnail(url=config.DEFAULT_ICON)
        embed.set_image(url=config.COLOR_BANNER)
        palette_list = "\n".join([f"{emoji} **{name}**" for name, (_, emoji) in config.COLOR_PALETTE.items()])
        embed.add_field(name="Available Presets", value=palette_list, inline=True)
        embed.add_field(name="Features", value="• Modal pop-up window\n• Auto-creates roles\n• Cleans previous colors", inline=True)
        embed.set_footer(text="Color Studio Plugin • Persistent UI")
        return embed

    @commands.command(name="color_panel")
    @commands.has_permissions(manage_roles=True)
    async def prefix_panel(self, ctx: commands.Context):
        await ctx.channel.send(embed=self.create_panel_embed(), view=ColorPluginPanel(self))
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @app_commands.command(name="color_panel", description="Deploy the Color Studio Plugin.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def slash_panel(self, interaction: discord.Interaction):
        await interaction.channel.send(embed=self.create_panel_embed(), view=ColorPluginPanel(self))
        await interaction.response.send_message("Color panel posted.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ColorRoles(bot))

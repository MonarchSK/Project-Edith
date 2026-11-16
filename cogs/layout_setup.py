import os
import discord
from discord import app_commands
from discord.ext import commands
import config
from main import update_command_channel_panel

CATEGORY_NAME = "🛠️ BACKUPS & CONTROL"
MEMORY_CHANNEL_NAME = "bot-memory"
ERROR_CHANNEL_NAME = "bot-errors"
COMMANDS_CHANNEL_NAME = "bot-commands"


class LayoutSetup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_or_create_director_role(self, guild: discord.Guild) -> discord.Role | None:
        role_name = getattr(config, "DIRECTOR_ROLE_NAME", "Director")
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            try:
                role = await guild.create_role(
                    name=role_name,
                    color=discord.Color.dark_gold(),
                    reason="Automated Director control role provisioning"
                )
            except discord.Forbidden:
                pass
        return role

    async def setup_environment(self, guild: discord.Guild) -> dict:
        director_role = await self._get_or_create_director_role(guild)

        private_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, read_messages=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
                attach_files=True,
                manage_channels=True,
                manage_messages=True,
                embed_links=True
            )
        }

        if director_role:
            private_overwrites[director_role] = discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
                attach_files=True
            )

        public_commands_overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
                read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
                embed_links=True,
                manage_messages=True
            )
        }

        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if not category:
            category = await guild.create_category(name=CATEGORY_NAME, overwrites=private_overwrites)
        else:
            try:
                await category.edit(overwrites=private_overwrites)
            except discord.Forbidden:
                pass

        async def get_or_create_channel(name: str, topic: str, chan_overwrites: dict) -> discord.TextChannel:
            chan = discord.utils.get(category.text_channels, name=name) or discord.utils.get(guild.text_channels, name=name)
            if not chan:
                chan = await guild.create_text_channel(
                    name=name,
                    category=category,
                    topic=topic,
                    overwrites=chan_overwrites
                )
            else:
                try:
                    await chan.edit(category=category, overwrites=chan_overwrites)
                except discord.Forbidden:
                    pass
            return chan

        memory_chan = await get_or_create_channel(
            MEMORY_CHANNEL_NAME,
            "Automated SQLite backups and restore records.",
            private_overwrites
        )
        error_chan = await get_or_create_channel(
            ERROR_CHANNEL_NAME,
            "Real-time exception logs & tracebacks.",
            private_overwrites
        )
        commands_chan = await get_or_create_channel(
            COMMANDS_CHANNEL_NAME,
            "Official bot commands hub. Dynamic command matrix updates here automatically.",
            public_commands_overwrites
        )

        # Synchronize configuration across both module namespace and environment
        config.BACKUP_CHANNEL_ID = memory_chan.id
        config.BOT_ERRORS_CHANNEL_ID = error_chan.id
        config.COMMANDS_CHANNEL_ID = commands_chan.id
        os.environ["BACKUP_CHANNEL_ID"] = str(memory_chan.id)
        os.environ["BOT_ERRORS_CHANNEL_ID"] = str(error_chan.id)
        os.environ["COMMANDS_CHANNEL_ID"] = str(commands_chan.id)

        await update_command_channel_panel(self.bot, guild)
        return {
            "category": category,
            "memory": memory_chan,
            "errors": error_chan,
            "commands": commands_chan
        }

    @commands.command(name="setup_layout")
    @commands.has_permissions(administrator=True)
    async def manual_setup_cmd(self, ctx: commands.Context):
        """Provisions BACKUPS & CONTROL channels and updates documentation."""
        msg = await ctx.send("⚙️ Setting up `🛠️ BACKUPS & CONTROL` category and secure logging channels...")
        res = await self.setup_environment(ctx.guild)
        role_name = getattr(config, "DIRECTOR_ROLE_NAME", "Director")
        await msg.edit(content=f"✅ Layout operational in **{res['category'].name}** configured for role `{role_name}`.")

    @app_commands.command(name="setup_layout", description="Provision BACKUPS & CONTROL channels and update documentation.")
    @app_commands.checks.has_permissions(administrator=True)
    async def manual_setup_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        res = await self.setup_environment(interaction.guild)
        role_name = getattr(config, "DIRECTOR_ROLE_NAME", "Director")
        await interaction.followup.send(f"✅ Layout operational in **{res['category'].name}** configured for role `{role_name}`.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LayoutSetup(bot))

import os
import traceback
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import config

CATEGORY_NAME = "🛠️ BACKUPS & CONTROL"
MEMORY_CHANNEL_NAME = "bot-memory"
ERROR_CHANNEL_NAME = "bot-errors"
COMMANDS_CHANNEL_NAME = "bot-commands"

class LayoutRemapping(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.on_command_error = self.global_prefix_error_handler
        self.bot.tree.on_error = self.global_slash_error_handler

    async def _get_or_create_director_role(self, guild: discord.Guild) -> discord.Role:
        role = discord.utils.get(guild.roles, name=config.DIRECTOR_ROLE_NAME)
        if not role:
            role = await guild.create_role(
                name=config.DIRECTOR_ROLE_NAME,
                color=discord.Color.dark_gold(),
                reason="Director control role"
            )
        return role

    async def setup_environment(self, guild: discord.Guild) -> dict:
        director_role = await self._get_or_create_director_role(guild)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, read_messages=False),
            director_role: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, attach_files=True, manage_channels=True)
        }

        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if not category:
            category = await guild.create_category(name=CATEGORY_NAME, overwrites=overwrites)
        else:
            await category.edit(overwrites=overwrites)

        async def get_or_create_channel(name: str, topic: str):
            chan = discord.utils.get(category.text_channels, name=name)
            if not chan:
                chan = await guild.create_text_channel(name=name, category=category, topic=topic)
            return chan

        memory_chan = await get_or_create_channel(MEMORY_CHANNEL_NAME, "Automated SQLite backups and restore records.")
        error_chan = await get_or_create_channel(ERROR_CHANNEL_NAME, "Real-time exception logs & tracebacks.")
        commands_chan = await get_or_create_channel(COMMANDS_CHANNEL_NAME, "Live catalog of active bot commands.")

        config.BACKUP_CHANNEL_ID = memory_chan.id
        os.environ["ERROR_CHANNEL_ID"] = str(error_chan.id)

        await self._render_command_documentation(commands_chan)
        return {"category": category, "memory": memory_chan, "errors": error_chan, "commands": commands_chan}

    async def _render_command_documentation(self, channel: discord.TextChannel):
        try:
            await channel.purge(limit=15)
        except Exception:
            pass

        embed = discord.Embed(
            title="📜 Project-Edith Commands",
            description="Active operational directory. Default prefix: `.`",
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc)
        )
        prefix_cmds = [f"`.{c.name}` - {c.help or 'No description'}" for c in self.bot.commands if not c.hidden]
        slash_cmds = [f"`/{c.name}` - {c.description or 'No description'}" for c in self.bot.tree.get_commands()]

        embed.add_field(name="Prefix Commands (`.`)", value="\n".join(prefix_cmds) or "None", inline=False)
        embed.add_field(name="Slash Commands (`/`)", value="\n".join(slash_cmds) or "None", inline=False)
        embed.set_footer(text="Auto-synced on deployment")
        await channel.send(embed=embed)

    async def log_error_to_channel(self, ctx_or_interaction, error: Exception):
        err_id = int(os.getenv("ERROR_CHANNEL_ID", "0"))
        if not err_id:
            return
        channel = self.bot.get_channel(err_id)
        if not channel:
            return

        is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
        invoker = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
        cmd_name = ctx_or_interaction.command.name if ctx_or_interaction.command else "Unknown"

        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        if len(tb) > 1750:
            tb = tb[-1750:]

        embed = discord.Embed(title="🚨 System Error Caught", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Command", value=f"`{cmd_name}`", inline=True)
        embed.add_field(name="Invoker", value=f"{invoker.mention} (`{invoker.id}`)", inline=True)
        embed.add_field(name="Traceback", value=f"```py\n{tb}\n```", inline=False)
        await channel.send(embed=embed)

    async def global_prefix_error_handler(self, ctx: commands.Context, error: commands.CommandError):
        orig = getattr(error, "original", error)
        if isinstance(orig, commands.CommandNotFound):
            return
        await self.log_error_to_channel(ctx, orig)
        await ctx.send(f"⚠️ An error occurred while executing `.{ctx.invoked_with}`. Logged to the Director panel.")

    async def global_slash_error_handler(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        orig = getattr(error, "original", error)
        await self.log_error_to_channel(interaction, orig)
        msg = "⚠️ Execution error encountered. Logged to `#bot-errors`."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @commands.command(name="setup_layout")
    @commands.is_owner()
    async def manual_setup_cmd(self, ctx: commands.Context):
        res = await self.setup_environment(ctx.guild)
        await ctx.send(f"✅ Layout operational in **{res['category'].name}** for `{config.DIRECTOR_ROLE_NAME}`.")

async def setup(bot: commands.Bot):
    await bot.add_cog(LayoutRemapping(bot))

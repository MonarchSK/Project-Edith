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


class LayoutSetup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register tree error handler cleanly
        self.bot.tree.on_error = self.global_slash_error_handler

    async def _get_or_create_director_role(self, guild: discord.Guild) -> discord.Role:
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

        overwrites = {
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
            overwrites[director_role] = discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
                attach_files=True
            )

        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if not category:
            category = await guild.create_category(name=CATEGORY_NAME, overwrites=overwrites)
        else:
            try:
                await category.edit(overwrites=overwrites)
            except discord.Forbidden:
                pass

        async def get_or_create_channel(name: str, topic: str) -> discord.TextChannel:
            chan = discord.utils.get(category.text_channels, name=name)
            if not chan:
                chan = await guild.create_text_channel(
                    name=name,
                    category=category,
                    topic=topic,
                    overwrites=overwrites
                )
            return chan

        memory_chan = await get_or_create_channel(
            MEMORY_CHANNEL_NAME,
            "Automated SQLite backups and restore records."
        )
        error_chan = await get_or_create_channel(
            ERROR_CHANNEL_NAME,
            "Real-time exception logs & tracebacks."
        )
        commands_chan = await get_or_create_channel(
            COMMANDS_CHANNEL_NAME,
            "Live catalog of active bot commands."
        )

        # Synchronize configuration across both module namespace and environment
        config.BACKUP_CHANNEL_ID = memory_chan.id
        os.environ["BACKUP_CHANNEL_ID"] = str(memory_chan.id)
        os.environ["ERROR_CHANNEL_ID"] = str(error_chan.id)

        await self._render_command_documentation(commands_chan)
        return {
            "category": category,
            "memory": memory_chan,
            "errors": error_chan,
            "commands": commands_chan
        }

    async def _render_command_documentation(self, channel: discord.TextChannel):
        try:
            await channel.purge(limit=25)
        except Exception:
            pass

        embed = discord.Embed(
            title="📜 Project-Edith Control Catalog",
            description="Active operational directory. Default prefix: `.`",
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc)
        )

        prefix_cmds = []
        for c in sorted(self.bot.commands, key=lambda x: x.name):
            if not c.hidden:
                desc = c.help or "No description provided."
                prefix_cmds.append(f"`.{c.name}` — {desc}")

        slash_cmds = []
        for c in sorted(self.bot.tree.get_commands(), key=lambda x: x.name):
            desc = c.description or "No description provided."
            slash_cmds.append(f"`/{c.name}` — {desc}")

        def chunk_list(items: list[str], max_len: int = 1000) -> list[str]:
            chunks = []
            curr = ""
            for item in items:
                if len(curr) + len(item) + 1 > max_len:
                    chunks.append(curr)
                    curr = item + "\n"
                else:
                    curr += item + "\n"
            if curr:
                chunks.append(curr)
            return chunks

        prefix_chunks = chunk_list(prefix_cmds)
        for idx, chunk in enumerate(prefix_chunks):
            title = "Prefix Commands (`.`)" if idx == 0 else f"Prefix Commands (Cont. {idx+1})"
            embed.add_field(name=title, value=chunk or "*None loaded*", inline=False)

        slash_chunks = chunk_list(slash_cmds)
        for idx, chunk in enumerate(slash_chunks):
            title = "Slash Commands (`/`)" if idx == 0 else f"Slash Commands (Cont. {idx+1})"
            embed.add_field(name=title, value=chunk or "*None loaded*", inline=False)

        embed.set_footer(text="Auto-synced on deployment • Project-Edith Control Engine")
        await channel.send(embed=embed)

    async def log_error_to_channel(self, ctx_or_interaction, error: Exception):
        err_id = int(os.getenv("ERROR_CHANNEL_ID", "0"))
        if not err_id:
            return
        channel = self.bot.get_channel(err_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(err_id)
            except Exception:
                return

        is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
        invoker = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
        cmd_name = "Unknown"
        if is_interaction:
            cmd_name = ctx_or_interaction.command.name if ctx_or_interaction.command else "Slash Command"
        else:
            cmd_name = ctx_or_interaction.command.name if ctx_or_interaction.command else (ctx_or_interaction.invoked_with or "Prefix Command")

        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        if len(tb) > 1750:
            tb = tb[-1750:]

        embed = discord.Embed(
            title="🚨 System Error Caught",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Command", value=f"`{cmd_name}`", inline=True)
        embed.add_field(name="Invoker", value=f"{invoker.mention} (`{invoker.id}`)", inline=True)
        if hasattr(ctx_or_interaction, "channel") and ctx_or_interaction.channel:
            embed.add_field(name="Channel", value=f"#{ctx_or_interaction.channel.name}", inline=True)
        embed.add_field(name="Traceback", value=f"```py\n{tb}\n```", inline=False)

        try:
            await channel.send(embed=embed)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        orig = getattr(error, "original", error)
        if isinstance(orig, commands.CommandNotFound):
            return

        if isinstance(orig, commands.MissingPermissions):
            await ctx.send("⛔ You do not have permission to execute this directive.", delete_after=5)
            return

        await self.log_error_to_channel(ctx, orig)
        try:
            await ctx.send(f"⚠️ An error occurred while executing `.{ctx.invoked_with}`. Logged to `#bot-errors`.", delete_after=8)
        except Exception:
            pass

    async def global_slash_error_handler(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        orig = getattr(error, "original", error)

        if isinstance(orig, app_commands.MissingPermissions):
            msg = "⛔ You lack sufficient administrative permissions to use this slash command."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return

        await self.log_error_to_channel(interaction, orig)
        msg = "⚠️ Execution error encountered. Full traceback logged to `#bot-errors`."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

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

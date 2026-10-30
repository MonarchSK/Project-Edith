import discord
from discord import app_commands
from discord.ext import commands

class Nicknames(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _update_nick(self, member: discord.Member, new_nick: str = None) -> str:
        if member.id == member.guild.owner_id:
            return "Discord prohibits bots from editing the Server Owner's nickname."
        if new_nick:
            if len(new_nick) > 32:
                return "Nicknames cannot exceed 32 characters."
            if "@everyone" in new_nick or "@here" in new_nick:
                return "Mentions are not allowed in nicknames."

        try:
            await member.edit(nick=new_nick)
            return f"Nickname updated to **{new_nick}**." if new_nick else "Nickname reset."
        except discord.Forbidden:
            return "Permission error: Make sure my bot role is higher than yours."

    @commands.command(name="nick")
    async def prefix_nick(self, ctx: commands.Context, *, new_name: str = None):
        msg = await self._update_nick(ctx.author, new_name)
        await ctx.send(msg)

    @app_commands.command(name="nick", description="Change or reset your server nickname.")
    @app_commands.describe(new_nickname="New name (leave blank to reset)")
    async def slash_nick(self, interaction: discord.Interaction, new_nickname: str = None):
        msg = await self._update_nick(interaction.user, new_nickname)
        await interaction.response.send_message(msg, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Nicknames(bot))

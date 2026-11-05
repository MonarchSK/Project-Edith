import os
import shutil
import asyncio
import collections
import random
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

# Verify voice support dependencies
VOICE_SUPPORT_AVAILABLE = discord.opus.is_loaded() or hasattr(discord, "VoiceClient")
FFMPEG_EXECUTABLE = shutil.which("ffmpeg") or "ffmpeg"

YTDL_FORMAT_OPTIONS = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -filter:a "volume=0.8"',
}

ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)


class Song:
    def __init__(self, data: dict, requester: discord.Member):
        self.data = data
        self.requester = requester
        self.title = data.get('title', 'Unknown Track')
        self.url = data.get('webpage_url', '')
        self.stream_url = data.get('url', '')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail', None)
        self.uploader = data.get('uploader', 'Unknown Artist')

    @classmethod
    async def from_query(cls, query: str, requester: discord.Member, loop=None):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        if 'entries' in data:
            if not data['entries']:
                raise ValueError("No matching audio found.")
            data = data['entries'][0]
        return cls(data, requester)

    def format_duration(self) -> str:
        if not self.duration:
            return "🔴 Live Stream"
        mins, secs = divmod(self.duration, 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            return f"{hours:d}:{mins:02d}:{secs:02d}"
        return f"{mins:d}:{secs:02d}"


class MusicPlayerControllerView(discord.ui.View):
    def __init__(self, guild_music_state):
        super().__init__(timeout=None)
        self.state = guild_music_state

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, custom_id="btn_music_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.state.history:
            await interaction.response.send_message("❌ No previously played track in history.", ephemeral=True)
            return

        prev_song = self.state.history.pop()
        if self.state.current_song:
            self.state.queue.appendleft(self.state.current_song)
        self.state.queue.appendleft(prev_song)
        if self.state.voice_client:
            self.state.voice_client.stop()
        await interaction.response.send_message("⏮️ Returning to previous track.", ephemeral=True)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary, custom_id="btn_music_pause_resume")
    async def btn_pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.state.voice_client
        if not vc:
            await interaction.response.send_message("❌ Bot is not connected.", ephemeral=True)
            return

        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ Playback paused.", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Playback resumed.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="btn_music_skip")
    async def btn_skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.state.voice_client or not self.state.voice_client.is_playing():
            await interaction.response.send_message("❌ Nothing to skip.", ephemeral=True)
            return

        self.state.voice_client.stop()
        await interaction.response.send_message("⏭️ Skipped current track.", ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, custom_id="btn_music_shuffle")
    async def btn_shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.state.queue) < 2:
            await interaction.response.send_message("❌ Need at least 2 tracks in queue to shuffle.", ephemeral=True)
            return

        queue_list = list(self.state.queue)
        random.shuffle(queue_list)
        self.state.queue = collections.deque(queue_list)
        await interaction.response.send_message("🔀 Queue shuffled randomly.", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="btn_music_stop")
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.state.queue.clear()
        if self.state.voice_client:
            await self.state.voice_client.disconnect()
        if self.state.current_message:
            try:
                await self.state.current_message.delete()
            except Exception:
                pass
        await interaction.response.send_message("⏹️ Playback stopped and disconnected.", ephemeral=True)


class GuildMusicState:
    def __init__(self, bot, guild: discord.Guild):
        self.bot = bot
        self.guild = guild
        self.queue = collections.deque()
        self.history = collections.deque(maxlen=10)
        self.current_song: Song = None
        self.voice_client: discord.VoiceClient = None
        self.current_message: discord.Message = None
        self.play_next_song = asyncio.Event()
        self.audio_loop_task = bot.loop.create_task(self.audio_player_loop())

    async def audio_player_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.play_next_song.clear()

            if not self.queue:
                try:
                    await asyncio.wait_for(self._wait_for_queue(), timeout=180.0)
                except asyncio.TimeoutError:
                    if self.voice_client and self.voice_client.is_connected():
                        await self.voice_client.disconnect()
                    if self.current_message:
                        try:
                            await self.current_message.delete()
                        except Exception:
                            pass
                    break

            if not self.queue:
                continue

            self.current_song = self.queue.popleft()
            try:
                source = discord.FFmpegPCMAudio(
                    self.current_song.stream_url,
                    executable=FFMPEG_EXECUTABLE,
                    **FFMPEG_OPTIONS
                )
            except Exception as e:
                print(f"[FFMPEG INITIALIZATION ERROR] {e}")
                continue

            def after_playback(err):
                if err:
                    print(f"[AUDIO RUNTIME ERROR] {err}")
                self.bot.loop.call_soon_threadsafe(self.play_next_song.set)

            if not self.voice_client or not self.voice_client.is_connected():
                break

            self.voice_client.play(source, after=after_playback)
            await self.update_controller_ui()

            await self.play_next_song.wait()
            if self.current_song:
                self.history.append(self.current_song)

    async def _wait_for_queue(self):
        while not self.queue:
            await asyncio.sleep(2)

    async def update_controller_ui(self):
        if not self.current_song:
            return

        embed = discord.Embed(
            title="🎵 AtomMusic // Active Playback",
            description=f"[{self.current_song.title}]({self.current_song.url})",
            color=discord.Color.from_rgb(0, 204, 255)
        )
        if self.current_song.thumbnail:
            embed.set_thumbnail(url=self.current_song.thumbnail)

        embed.add_field(name="Channel / Artist", value=f"`{self.current_song.uploader}`", inline=True)
        embed.add_field(name="Duration", value=f"`{self.current_song.format_duration()}`", inline=True)
        embed.add_field(name="Requested By", value=self.current_song.requester.mention, inline=True)

        if self.queue:
            next_up = list(self.queue)[:3]
            lines = [f"`{idx+1}.` [{s.title[:38]}]({s.url}) (`{s.format_duration()}`)" for idx, s in enumerate(next_up)]
            remaining = len(self.queue) - len(next_up)
            queue_str = "\n".join(lines)
            if remaining > 0:
                queue_str += f"\n*...and {remaining} more tracks*"
            embed.add_field(name="📑 Next In Queue", value=queue_str, inline=False)
        else:
            embed.add_field(name="📑 Next In Queue", value="*Queue empty — add more songs!*", inline=False)

        embed.set_footer(text="Project-Edith • AtomMusic Controller System")
        view = MusicPlayerControllerView(self)

        if self.current_message:
            try:
                await self.current_message.edit(embed=embed, view=view)
                return
            except Exception:
                pass

        text_channel = self.current_song.requester.guild.system_channel or self.current_song.requester.guild.text_channels[0]
        if text_channel:
            try:
                self.current_message = await text_channel.send(embed=embed, view=view)
            except Exception:
                pass


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.states: dict[int, GuildMusicState] = {}

    def get_music_state(self, guild: discord.Guild) -> GuildMusicState:
        if guild.id not in self.states or self.states[guild.id].audio_loop_task.done():
            self.states[guild.id] = GuildMusicState(self.bot, guild)
        return self.states[guild.id]

    async def ensure_voice_connection(self, ctx_or_interaction) -> discord.VoiceClient | None:
        user = ctx_or_interaction.user if isinstance(ctx_or_interaction, discord.Interaction) else ctx_or_interaction.author
        if not user.voice or not user.voice.channel:
            msg = "❌ You must be connected to a voice channel to use music directives."
            if isinstance(ctx_or_interaction, discord.Interaction):
                await ctx_or_interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx_or_interaction.send(msg, delete_after=5)
            return None

        guild = ctx_or_interaction.guild
        vc = guild.voice_client
        target_channel = user.voice.channel

        if vc is None:
            vc = await target_channel.connect(timeout=15.0)
        elif vc.channel != target_channel:
            await vc.move_to(target_channel)

        return vc

    @app_commands.command(name="play", description="Play a track or add it to the AtomMusic player queue.")
    @app_commands.describe(query="Song title, artist name, or YouTube/SoundCloud URL")
    async def slash_play(self, interaction: discord.Interaction, query: str):
        vc = await self.ensure_voice_connection(interaction)
        if not vc:
            return

        await interaction.response.defer(ephemeral=True)
        state = self.get_music_state(interaction.guild)
        state.voice_client = vc

        try:
            song = await Song.from_query(query, requester=interaction.user, loop=self.bot.loop)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to resolve track: `{e}`", ephemeral=True)
            return

        state.queue.append(song)

        if vc.is_playing() or vc.is_paused():
            embed = discord.Embed(
                title="📑 Added to Queue",
                description=f"[{song.title}]({song.url})\n\n• **Duration:** `{song.format_duration()}`\n• **Queue Position:** `#{len(state.queue)}`",
                color=discord.Color.green()
            )
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(f"▶️ Loaded **{song.title}** into the audio controller.", ephemeral=True)

    @commands.command(name="play", aliases=["p"])
    async def prefix_play(self, ctx: commands.Context, *, query: str):
        """Play a track or add to queue: .play Alan Walker Faded"""
        vc = await self.ensure_voice_connection(ctx)
        if not vc:
            return

        state = self.get_music_state(ctx.guild)
        state.voice_client = vc

        load_msg = await ctx.send("🔍 *Searching audio streams...*")
        try:
            song = await Song.from_query(query, requester=ctx.author, loop=self.bot.loop)
        except Exception as e:
            await load_msg.edit(content=f"❌ Failed to resolve audio: `{e}`")
            return

        state.queue.append(song)
        try:
            await load_msg.delete()
        except Exception:
            pass

        if vc.is_playing() or vc.is_paused():
            embed = discord.Embed(
                title="📑 Added to Queue",
                description=f"[{song.title}]({song.url})\n\n• **Duration:** `{song.format_duration()}`\n• **Queue Position:** `#{len(state.queue)}`",
                color=discord.Color.green()
            )
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
            await ctx.send(embed=embed, delete_after=10)

    @commands.command(name="queue", aliases=["q"])
    async def prefix_queue(self, ctx: commands.Context):
        """View the current playback queue."""
        state = self.get_music_state(ctx.guild)
        if not state.current_song and not state.queue:
            await ctx.send("❌ The music queue is currently empty.", delete_after=5)
            return

        embed = discord.Embed(
            title="🎼 Active Playback Queue",
            color=discord.Color.blue()
        )
        if state.current_song:
            embed.add_field(
                name="▶️ Currently Playing",
                value=f"[{state.current_song.title}]({state.current_song.url}) | `{state.current_song.format_duration()}` (Requested by {state.current_song.requester.mention})",
                inline=False
            )

        if state.queue:
            lines = [f"`{idx+1}.` [{s.title}]({s.url}) (`{s.format_duration()}`)" for idx, s in enumerate(list(state.queue)[:10])]
            embed.add_field(name="Upcoming Tracks", value="\n".join(lines), inline=False)

        await ctx.send(embed=embed, delete_after=15)


async def setup(bot):
    await bot.add_cog(Music(bot))

# MTM1OTg4MTE4ODUzMzIwNzA0MA.GVCuAs.jEicg5tR4zpp0e3schqZhkPBSdJ2cO8cxk6Iy4
import asyncio
import logging
import time
from typing import cast, Optional, List

import discord
from discord.ext import commands

import wavelink


class Bot(commands.Bot):
    def __init__(self) -> None:
        intents: discord.Intents = discord.Intents.default()
        intents.message_content = True

        discord.utils.setup_logging(level=logging.INFO)
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        # Use your provided Lavalink server credentials
        nodes = [
            wavelink.Node(
                uri="wss://lavalinkv4.serenetia.com:443", 
                password="https://dsc.gg/ajidevserver"
            )
        ]

        # Connect to the Lavalink nodes
        await wavelink.Pool.connect(nodes=nodes, client=self, cache_capacity=100)

    async def on_ready(self) -> None:
        logging.info("Logged in: %s | %s", self.user, self.user.id)
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.listening, 
            name="!help for commands"
        ))

    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        logging.info("Wavelink Node connected: %r | Resumed: %s", payload.node, payload.resumed)

    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player: wavelink.Player | None = payload.player
        if not player:
            # Handle edge cases...
            return

        original: wavelink.Playable | None = payload.original
        track: wavelink.Playable = payload.track

        embed: discord.Embed = discord.Embed(
            title="Now Playing", 
            color=discord.Color.blurple()
        )
        embed.description = f"**{track.title}** by `{track.author}`"

        if track.artwork:
            embed.set_image(url=track.artwork)

        if original and original.recommended:
            embed.description += f"\n\n`This track was recommended via {track.source}`"

        if track.album.name:
            embed.add_field(name="Album", value=track.album.name)
            
        # Format duration
        minutes, seconds = divmod(track.length // 1000, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            duration = f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            duration = f"{minutes}:{seconds:02d}"
        embed.add_field(name="Duration", value=duration)

        await player.home.send(embed=embed)
        
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player: wavelink.Player | None = payload.player
        if not player:
            return
            
        # If nothing left in queue and autoplay is disabled, show a message
        if player.queue.is_empty and player.autoplay == wavelink.AutoPlayMode.disabled:
            embed = discord.Embed(
                title="Queue Finished",
                description="No more tracks in queue. Use `!play` to add more songs!",
                color=discord.Color.orange()
            )
            await player.home.send(embed=embed)


bot: Bot = Bot()


@bot.command()
async def play(ctx: commands.Context, *, query: str) -> None:
    """Play a song with the given query."""
    if not ctx.guild:
        return

    player: wavelink.Player
    player = cast(wavelink.Player, ctx.voice_client)  # type: ignore

    if not player:
        try:
            player = await ctx.author.voice.channel.connect(cls=wavelink.Player)  # type: ignore
        except AttributeError:
            await ctx.send("Please join a voice channel first before using this command.")
            return
        except discord.ClientException:
            await ctx.send("I was unable to join this voice channel. Please try again.")
            return

    # Turn on AutoPlay to enabled mode.
    # enabled = AutoPlay will play songs for us and fetch recommendations...
    # partial = AutoPlay will play songs for us, but WILL NOT fetch recommendations...
    # disabled = AutoPlay will do nothing...
    player.autoplay = wavelink.AutoPlayMode.enabled

    # Lock the player to this channel...
    if not hasattr(player, "home"):
        player.home = ctx.channel
    elif player.home != ctx.channel:
        await ctx.send(f"You can only play songs in {player.home.mention}, as the player has already started there.")
        return

    # This will handle fetching Tracks and Playlists...
    # Seed the doc strings for more information on this method...
    # If spotify is enabled via LavaSrc, this will automatically fetch Spotify tracks if you pass a URL...
    # Defaults to YouTube for non URL based queries...
    try:
        tracks: wavelink.Search = await wavelink.Playable.search(query)
        if not tracks:
            await ctx.send(f"{ctx.author.mention} - Could not find any tracks with that query. Please try again.")
            return

        if isinstance(tracks, wavelink.Playlist):
            # tracks is a playlist...
            added: int = await player.queue.put_wait(tracks)
            await ctx.send(f"Added the playlist **`{tracks.name}`** ({added} songs) to the queue.")
        else:
            track: wavelink.Playable = tracks[0]
            await player.queue.put_wait(track)
            await ctx.send(f"Added **`{track.title}`** to the queue.")

        if not player.playing:
            # Play now since we aren't playing anything...
            await player.play(player.queue.get(), volume=30)
            
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")
        return

    # Optionally delete the invokers message...
    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass


@bot.command()
async def skip(ctx: commands.Context) -> None:
    """Skip the current song."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently playing anything.")
        return

    await player.skip(force=True)
    await ctx.message.add_reaction("⏭️")


@bot.command()
async def nightcore(ctx: commands.Context) -> None:
    """Set the filter to a nightcore style."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently in a voice channel.")
        return

    filters: wavelink.Filters = player.filters
    filters.timescale.set(pitch=1.2, speed=1.2, rate=1)
    await player.set_filters(filters)

    await ctx.send("Applied nightcore filter! 🎵")


@bot.command()
async def normal(ctx: commands.Context) -> None:
    """Reset audio filters to normal."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently in a voice channel.")
        return

    await player.set_filters(wavelink.Filters())
    await ctx.send("Reset audio filters to normal! 🎵")


@bot.command(name="toggle", aliases=["pause", "resume"])
async def pause_resume(ctx: commands.Context) -> None:
    """Pause or Resume the Player depending on its current state."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently playing anything.")
        return

    await player.pause(not player.paused)
    status = "Paused ⏸️" if player.paused else "Resumed ▶️"
    await ctx.send(f"{status} the player.")


@bot.command()
async def volume(ctx: commands.Context, value: int = None) -> None:
    """Change the volume of the player (0-100)."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently in a voice channel.")
        return
        
    if value is None:
        await ctx.send(f"Current volume: **{player.volume}%**")
        return
        
    if not 0 <= value <= 100:
        await ctx.send("Volume must be between 0 and 100.")
        return

    await player.set_volume(value)
    await ctx.send(f"Set volume to **{value}%** 🔊")


@bot.command(aliases=["dc"])
async def disconnect(ctx: commands.Context) -> None:
    """Disconnect the Player."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently in a voice channel.")
        return

    await player.disconnect()
    await ctx.send("Disconnected from the voice channel 👋")


@bot.command(aliases=["np"])
async def nowplaying(ctx: commands.Context) -> None:
    """Show information about the current track."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player or not player.current:
        await ctx.send("Nothing is playing right now.")
        return
        
    track = player.current
    
    # Create progress bar
    duration = track.length
    position = player.position
    
    if duration > 0:
        progress = int((position / duration) * 20)
        progress_bar = "▬" * progress + "🔘" + "▬" * (20 - progress)
        
        # Format timestamps
        pos_min, pos_sec = divmod(position // 1000, 60)
        dur_min, dur_sec = divmod(duration // 1000, 60)
        
        timestamp = f"{pos_min}:{pos_sec:02d}/{dur_min}:{dur_sec:02d}"
    else:
        progress_bar = "🔘▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
        timestamp = "LIVE"
    
    embed = discord.Embed(title="Now Playing", color=discord.Color.green())
    embed.description = f"**{track.title}**\nby `{track.author}`\n\n{progress_bar}\n{timestamp}"
    
    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    
    if track.album.name:
        embed.add_field(name="Album", value=track.album.name, inline=True)
    
    embed.add_field(name="Source", value=track.source.capitalize(), inline=True)
    
    if player.queue and not player.queue.is_empty:
        embed.set_footer(text=f"{len(player.queue)} songs in queue")
    
    await ctx.send(embed=embed)


@bot.command(aliases=["q"])
async def queue(ctx: commands.Context) -> None:
    """Display the current queue."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently in a voice channel.")
        return
    
    if player.queue.is_empty:
        if player.current:
            embed = discord.Embed(
                title="Queue",
                description="No songs in queue. Currently playing:\n" + 
                           f"**{player.current.title}** - `{player.current.author}`",
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send("The queue is empty and nothing is playing.")
        return
    
    queue_list = list(player.queue)
    
    # Create a nice embed for the queue
    embed = discord.Embed(title="Music Queue", color=discord.Color.blue())
    
    # Add currently playing song
    if player.current:
        embed.add_field(
            name="Currently Playing",
            value=f"**{player.current.title}** - `{player.current.author}`",
            inline=False
        )
    
    # Add queue items (up to 10 for readability)
    queue_text = "\n".join([f"`{i+1}.` **{track.title}** - `{track.author}`" for i, track in enumerate(queue_list[:10])])
    
    if len(queue_list) > 10:
        queue_text += f"\n\n... and {len(queue_list) - 10} more tracks"
    
    # Calculate total queue duration
    total_duration = sum(track.length for track in queue_list) // 1000
    minutes, seconds = divmod(total_duration, 60)
    hours, minutes = divmod(minutes, 60)
    
    if hours > 0:
        time_format = f"{hours}h {minutes}m {seconds}s"
    else:
        time_format = f"{minutes}m {seconds}s"
    
    embed.description = f"**{len(queue_list)} tracks** | Total length: `{time_format}`\n\n{queue_text}"
    
    await ctx.send(embed=embed)


@bot.command()
async def clear(ctx: commands.Context) -> None:
    """Clear the queue."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently in a voice channel.")
        return
    
    if player.queue.is_empty:
        await ctx.send("The queue is already empty.")
        return
    
    count = len(player.queue)
    player.queue.clear()
    await ctx.send(f"Cleared {count} tracks from the queue.")


@bot.command()
async def seek(ctx: commands.Context, position: int) -> None:
    """Seek to a position in the track (in seconds)."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player or not player.current:
        await ctx.send("Nothing is playing right now.")
        return
    
    # Convert to milliseconds
    position_ms = position * 1000
    
    if position_ms > player.current.length:
        await ctx.send("The position is beyond the track length.")
        return
    
    await player.seek(position_ms)
    await ctx.send(f"Seeked to `{position}` seconds.")


@bot.command(aliases=["loop"])
async def repeat(ctx: commands.Context, mode: str = None) -> None:
    """Set repeat mode: off, one, all"""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently in a voice channel.")
        return
    
    if mode is None:
        current_mode = "off"
        if player.queue.mode == wavelink.QueueMode.loop:
            current_mode = "all"
        elif player.queue.mode == wavelink.QueueMode.loop_all:
            current_mode = "one"
        
        await ctx.send(f"Current repeat mode: `{current_mode}`\nUse `!repeat [off/one/all]` to change.")
        return
    
    mode = mode.lower()
    if mode == "off":
        player.queue.mode = wavelink.QueueMode.normal
        await ctx.send("Repeat mode: `Off`")
    elif mode == "one":
        player.queue.mode = wavelink.QueueMode.loop
        await ctx.send("Repeat mode: `Repeat one track`")
    elif mode == "all":
        player.queue.mode = wavelink.QueueMode.loop_all
        await ctx.send("Repeat mode: `Repeat all tracks`")
    else:
        await ctx.send("Invalid mode. Use `off`, `one`, or `all`.")


@bot.command()
async def shuffle(ctx: commands.Context) -> None:
    """Shuffle the queue."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently in a voice channel.")
        return
    
    if player.queue.is_empty:
        await ctx.send("The queue is empty.")
        return
    
    player.queue.shuffle()
    await ctx.send("🔀 Shuffled the queue!")


@bot.command()
async def remove(ctx: commands.Context, index: int) -> None:
    """Remove a track from the queue by its position."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently in a voice channel.")
        return
    
    if player.queue.is_empty:
        await ctx.send("The queue is empty.")
        return
    
    if index < 1 or index > len(player.queue):
        await ctx.send(f"Invalid position. Please provide a number between 1 and {len(player.queue)}.")
        return
    
    queue_list = list(player.queue)
    track = queue_list[index-1]
    
    # Create a new queue without the removed track
    new_queue = wavelink.Queue()
    for i, t in enumerate(queue_list):
        if i != index-1:
            new_queue.put(t)
    
    player.queue = new_queue
    await ctx.send(f"Removed `{track.title}` from the queue.")


@bot.command(aliases=["bass"])
async def boost(ctx: commands.Context) -> None:
    """Apply a bass boost filter."""
    player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
    if not player:
        await ctx.send("I'm not currently in a voice channel.")
        return
    
    filters = player.filters
    filters.equalizer.set(bands=[(0, 0.3), (1, 0.3), (2, 0.2), (3, 0.1)])
    await player.set_filters(filters)
    await ctx.send("Applied bass boost filter! 🔊")


@bot.command()
async def lavalinkstats(ctx):
    """Displays Lavalink node stats."""
    node = wavelink.NodePool.get_node()  # This should be a Node object, not a string

    if hasattr(node, 'is_connected'):  # Optional: avoid crashing
        stats = f"Status: {'Online' if node.is_connected else 'Offline'}\n"
    else:
        stats = "Status: Unknown\n"

    stats += f"Players: {getattr(node, 'players', 'Unknown')}\n"
    stats += f"Playing Players: {getattr(node, 'playing_players', 'Unknown')}\n"
    stats += f"Uptime: {getattr(node, 'uptime', 'Unknown')}ms\n"

    mem = getattr(node, 'memory', None)
    if mem:
        stats += f"Memory - Used: {mem.get('used', 'Unknown')}MB / Allocated: {mem.get('allocated', 'Unknown')}MB / Free: {mem.get('free', 'Unknown')}MB / Reservable: {mem.get('reservable', 'Unknown')}MB\n"
    else:
        stats += "Memory: Unknown\n"

    cpu = getattr(node, 'cpu', None)
    if cpu:
        stats += f"CPU - Cores: {cpu.get('cores', 'Unknown')} | System Load: {cpu.get('systemLoad', 'Unknown')} | Lavalink Load: {cpu.get('lavalinkLoad', 'Unknown')}\n"
    else:
        stats += "CPU: Unknown\n"

    frame_stats = getattr(node, 'frame_stats', None)
    if frame_stats:
        stats += f"Frames - Sent: {frame_stats.get('sent', 'Unknown')} | Nulled: {frame_stats.get('nulled', 'Unknown')} | Deficit: {frame_stats.get('deficit', 'Unknown')}\n"
    else:
        stats += "Frame Stats: Unknown\n"

    await ctx.send(f"```{stats}```")



@bot.command(name="commands")
async def help(ctx: commands.Context, command: str = None) -> None:
    """ Show help for all commands or a specific command. """
    if command:
        cmd = bot.get_command(command)
        if not cmd:
            await ctx.send(f"Command `{command}` not found.")
            return
            
        embed = discord.Embed(
            title=f"Help: {cmd.name}",
            description=cmd.help or "No description available",
            color=discord.Color.blue()
        )
        
        if cmd.aliases:
            embed.add_field(name="Aliases", value=", ".join(cmd.aliases), inline=False)
            
        usage = f"!{cmd.name}"
        if cmd.signature:
            usage += f" {cmd.signature}"
        embed.add_field(name="Usage", value=f"`{usage}`", inline=False)
        
        await ctx.send(embed=embed)
        return
    
    # General help
    embed = discord.Embed(
        title="Music Bot Commands",
        description="Use `!help <command>` for more details about a specific command.",
        color=discord.Color.blue()
    )
    
    # Group commands by category
    playback = []
    queue_commands = []
    effects = []
    system = []
    
    for cmd in sorted(bot.commands, key=lambda x: x.name):
        if cmd.name in ["play", "pause", "resume", "toggle", "skip", "seek", "nowplaying", "np"]:
            playback.append(cmd.name)
        elif cmd.name in ["queue", "q", "clear", "shuffle", "remove", "repeat", "loop"]:
            queue_commands.append(cmd.name)
        elif cmd.name in ["nightcore", "normal", "volume", "boost", "bass"]:
            effects.append(cmd.name)
        else:
            system.append(cmd.name)
    
    embed.add_field(name="🎵 Playback", value=", ".join(f"`{cmd}`" for cmd in playback), inline=False)
    embed.add_field(name="📋 Queue", value=", ".join(f"`{cmd}`" for cmd in queue_commands), inline=False)
    embed.add_field(name="🔊 Sound Effects", value=", ".join(f"`{cmd}`" for cmd in effects), inline=False)
    embed.add_field(name="⚙️ System", value=", ".join(f"`{cmd}`" for cmd in system), inline=False)
    
    await ctx.send(embed=embed)


# Add an uptime attribute to track bot's starting time
bot.uptime = time.time()

async def main() -> None:
    async with bot:
        await bot.start("MTM1OTg4MTE4ODUzMzIwNzA0MA.GVCuAs.jEicg5tR4zpp0e3schqZhkPBSdJ2cO8cxk6Iy4")


asyncio.run(main())
import discord
import wavelink
import asyncio
from discord.ext import commands

# Bot configuration
TOKEN = "MTM1OTg4MTE4ODUzMzIwNzA0MA.GVCuAs.jEicg5tR4zpp0e3schqZhkPBSdJ2cO8cxk6Iy4"  # Replace with your actual bot token
PREFIX = "$"

# Create bot instance
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Lavalink server credentials
LAVALINK_SERVER = {
    "host": "lavalink.nextgencoders.xyz",
    "port": 443,
    "password": "nextgencoders",
    "secure": True
}

class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=PREFIX, intents=intents)
        
    async def setup_hook(self) -> None:
        # Initialize wavelink and connect to lavalink server
        node = wavelink.Node(
            uri=f"{'wss' if LAVALINK_SERVER['secure'] else 'ws'}://{LAVALINK_SERVER['host']}:{LAVALINK_SERVER['port']}/v4/websocket",
            password=LAVALINK_SERVER['password'],
            secure=LAVALINK_SERVER['secure']
        )
        await wavelink.NodePool.connect(client=bot, nodes=[node])
        print(f"Connected to Lavalink node: {LAVALINK_SERVER['host']}")


# Replace the bot instance with our custom class
bot = MusicBot()

@bot.event
async def on_ready():
    print(f"Bot is ready! Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Using wavelink v{wavelink.__version__}")

@bot.event
async def on_wavelink_node_ready(node: wavelink.Node):
    print(f"Node {node.identifier} is ready!")

@bot.event
async def on_wavelink_track_end(player: wavelink.Player, track: wavelink.Track, reason):
    if not player.queue.is_empty and not player.auto_queue:
        next_song = await player.queue.get_wait()
        await player.play(next_song)
    elif player.auto_queue:
        # Auto queue already handles this
        pass

@bot.command()
async def join(ctx):
    """Join the voice channel"""
    if not ctx.author.voice:
        return await ctx.send("You must be in a voice channel to use this command.")
    
    if not ctx.voice_client:
        try:
            await ctx.author.voice.channel.connect(cls=wavelink.Player)
            await ctx.send(f"Joined {ctx.author.voice.channel.mention}")
        except Exception as e:
            await ctx.send(f"Error joining voice channel: {e}")
    else:
        await ctx.send("I'm already in a voice channel!")

@bot.command()
async def leave(ctx):
    """Leave the voice channel"""
    if not ctx.voice_client:
        return await ctx.send("I'm not in a voice channel.")
    
    await ctx.voice_client.disconnect()
    await ctx.send("Left the voice channel.")

@bot.command()
async def play(ctx, *, query: str):
    """Play a song with the given query"""
    if not ctx.voice_client:
        # Join the voice channel if the bot is not already in one
        if not ctx.author.voice:
            return await ctx.send("You must be in a voice channel to use this command.")
        await ctx.author.voice.channel.connect(cls=wavelink.Player)
    
    # Get the player
    player = ctx.voice_client
    
    if not player:
        return await ctx.send("I couldn't connect to your voice channel.")
    
    # Search for the song
    try:
        # Search for tracks
        search_result = await wavelink.Playable.search(query)
        
        if not search_result:
            return await ctx.send(f"No results found for: {query}")
        
        # Handle different search result types
        if isinstance(search_result, wavelink.Playlist):
            # Add playlist tracks to queue
            first_track = search_result.tracks[0]
            await player.queue.put_wait(first_track)
            
            # Add the rest to the queue
            for track in search_result.tracks[1:]:
                await player.queue.put_wait(track)
            
            await ctx.send(f"Added playlist {search_result.name} with {len(search_result.tracks)} tracks to the queue.")
            
            # Play if not already playing
            if not player.is_playing():
                track = await player.queue.get_wait()
                await player.play(track)
        else:
            # Single track
            track = search_result[0]
            
            # If player is already playing, add to queue
            if player.is_playing():
                await player.queue.put_wait(track)
                await ctx.send(f"Added to queue: **{track.title}**")
            else:
                # Otherwise play immediately
                await player.play(track)
                await ctx.send(f"Now playing: **{track.title}**")
    
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
async def pause(ctx):
    """Pause the current track"""
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        return await ctx.send("Nothing is playing right now.")
    
    player = ctx.voice_client
    
    if player.is_paused():
        return await ctx.send("The player is already paused.")
    
    await player.pause()
    await ctx.send("Paused the player.")

@bot.command()
async def resume(ctx):
    """Resume the current track"""
    if not ctx.voice_client:
        return await ctx.send("I'm not in a voice channel.")
    
    player = ctx.voice_client
    
    if not player.is_paused():
        return await ctx.send("The player is not paused.")
    
    await player.resume()
    await ctx.send("Resumed the player.")

@bot.command()
async def skip(ctx):
    """Skip the current track"""
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        return await ctx.send("Nothing to skip.")
    
    player = ctx.voice_client
    
    await player.stop()
    await ctx.send("Skipped the current track.")

@bot.command()
async def queue(ctx):
    """Display the current queue"""
    if not ctx.voice_client:
        return await ctx.send("I'm not in a voice channel.")
    
    player = ctx.voice_client
    
    if player.queue.is_empty:
        return await ctx.send("The queue is empty.")
    
    queue_list = list(player.queue)
    
    # Create a nice embed for the queue
    embed = discord.Embed(title="Music Queue", color=discord.Color.blurple())
    
    # Add currently playing song
    if player.current:
        embed.add_field(
            name="Currently Playing",
            value=f"**{player.current.title}**",
            inline=False
        )
    
    # Add queue items (up to 10 for readability)
    queue_text = "\n".join([f"{i+1}. **{track.title}**" for i, track in enumerate(queue_list[:10])])
    
    if len(queue_list) > 10:
        queue_text += f"\n... and {len(queue_list) - 10} more"
    
    embed.add_field(name="Up Next", value=queue_text or "No songs in queue", inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
async def clear(ctx):
    """Clear the queue"""
    if not ctx.voice_client:
        return await ctx.send("I'm not in a voice channel.")
    
    player = ctx.voice_client
    
    player.queue.clear()
    await ctx.send("Cleared the queue.")

@bot.command()
async def volume(ctx, volume: int = None):
    """Change the player volume"""
    if not ctx.voice_client:
        return await ctx.send("I'm not in a voice channel.")
    
    player = ctx.voice_client
    
    if volume is None:
        return await ctx.send(f"Current volume: {player.volume}%")
    
    if not 0 <= volume <= 100:
        return await ctx.send("Volume must be between 0 and 100.")
    
    await player.set_volume(volume)
    await ctx.send(f"Set volume to {volume}%")

@bot.command()
async def nowplaying(ctx):
    """Show information about the current track"""
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        return await ctx.send("Nothing is playing right now.")
    
    player = ctx.voice_client
    track = player.current
    
    if not track:
        return await ctx.send("Nothing is playing right now.")
    
    embed = discord.Embed(title="Now Playing", color=discord.Color.green())
    embed.add_field(name="Track", value=track.title, inline=False)
    embed.add_field(name="Artist", value=track.author, inline=True)
    
    # Format duration
    minutes, seconds = divmod(track.length // 1000, 60)
    duration = f"{minutes}:{seconds:02d}"
    embed.add_field(name="Duration", value=duration, inline=True)
    
    # Add thumbnail if available
    if hasattr(track, 'thumbnail') and track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    
    await ctx.send(embed=embed)

@bot.command()
async def seek(ctx, position: int):
    """Seek to a position in the track (in seconds)"""
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        return await ctx.send("Nothing is playing right now.")
    
    player = ctx.voice_client
    
    # Convert to milliseconds
    position_ms = position * 1000
    
    if position_ms > player.current.length:
        return await ctx.send("The position is beyond the track length.")
    
    await player.seek(position_ms)
    await ctx.send(f"Seeked to {position} seconds.")

@bot.command()
async def loop(ctx):
    """Toggle loop mode for the current track"""
    if not ctx.voice_client:
        return await ctx.send("I'm not in a voice channel.")
    
    player = ctx.voice_client
    
    # Toggle loop mode
    player.queue.loop = not player.queue.loop
    
    status = "enabled" if player.queue.loop else "disabled"
    await ctx.send(f"Loop mode {status}.")

@bot.command()
async def shuffle(ctx):
    """Shuffle the queue"""
    if not ctx.voice_client:
        return await ctx.send("I'm not in a voice channel.")
    
    player = ctx.voice_client
    
    if player.queue.is_empty:
        return await ctx.send("The queue is empty.")
    
    player.queue.shuffle()
    await ctx.send("Shuffled the queue.")

# Run the bot
if __name__ == "__main__":
    bot.run(TOKEN)
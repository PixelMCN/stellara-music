import asyncio
import logging
import time
import random
import re
from typing import cast, Optional, Dict, List, Union
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks
import wavelink
from dotenv import load_dotenv
import os
import json

# Load environment variables from .env file
load_dotenv()

LAVALINK_URI = os.getenv("LAVALINK_URI")
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD")
DJ_ROLE_NAME = "DJ"  # Role name for DJ permissions
INACTIVITY_TIMEOUT = 300  # 5 minutes in seconds

# Define regex patterns for streaming service URLs
SPOTIFY_REGEX = re.compile(r"https?://open.spotify.com/(?P<type>track|playlist|album)/(?P<id>[a-zA-Z0-9]+)")
YOUTUBE_PLAYLIST_REGEX = re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/playlist\?list=(?P<id>[a-zA-Z0-9_-]+)")


class VolumeManager:
    """Class to manage volume settings per guild"""
    def __init__(self, file_path="volume_settings.json"):
        self.file_path = file_path
        self.volumes = self._load()

    def _load(self) -> Dict[int, int]:
        try:
            with open(self.file_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save(self):
        with open(self.file_path, "w") as f:
            # Convert int keys to strings for JSON
            serializable_volumes = {str(k): v for k, v in self.volumes.items()}
            json.dump(serializable_volumes, f)

    def get_volume(self, guild_id: int) -> int:
        return self.volumes.get(str(guild_id), 30)  # Default volume is 30%

    def set_volume(self, guild_id: int, volume: int):
        self.volumes[str(guild_id)] = volume
        self.save()


class MusicPlayer(wavelink.Player):
    """Extended Player class with additional functionality"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.home = None  # Channel where the player was invoked
        self.loop = False  # Loop the current track
        self.loop_queue = False  # Loop the entire queue
        self.last_interaction = datetime.now()  # Track when the player was last used
        self.dj_role_required = True  # Whether DJ role is required for certain commands
        self.dj_members = set()  # Set of user IDs with DJ permissions
        self.current_track = None  # Currently playing track
        self.progress_message = None  # Message showing track progress

    async def update_last_interaction(self):
        """Update the timestamp of the last interaction with the player"""
        self.last_interaction = datetime.now()

    def is_inactive(self) -> bool:
        """Check if the player has been inactive for too long"""
        return (datetime.now() - self.last_interaction).total_seconds() > INACTIVITY_TIMEOUT

    def format_duration(self, milliseconds: int) -> str:
        """Format milliseconds into mm:ss format"""
        seconds = milliseconds // 1000
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes}:{seconds:02d}"

    def create_progress_bar(self, current_ms: int, total_ms: int, length: int = 15) -> str:
        """Create a text-based progress bar"""
        if total_ms <= 0:
            return "▬" * length
        
        position = min(length, int(length * current_ms / total_ms))
        bar = "▬" * position + "🔘" + "▬" * (length - position - 1)
        return bar


class Bot(commands.Bot):
    def __init__(self) -> None:
        intents: discord.Intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True  # Need member intent for role checks

        discord.utils.setup_logging(level=logging.INFO)
        super().__init__(command_prefix="!", intents=intents)
        
        # Initialize volume manager
        self.volume_manager = VolumeManager()
        
        # Dictionary to track search results for users
        self.search_results = {}

    async def setup_hook(self) -> None:
        # Use your provided Lavalink server credentials
        nodes = [
            wavelink.Node(
                uri=LAVALINK_URI,
                password=LAVALINK_PASSWORD
            )
        ]

        # Connect to the Lavalink nodes
        await wavelink.Pool.connect(nodes=nodes, client=self, cache_capacity=100)

        # Start inactive player check task
        self.check_inactive_players.start()

        # Sync slash commands
        await self.tree.sync()

    async def on_ready(self) -> None:
        logging.info("Logged in: %s | %s", self.user, self.user.id)
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="/help for commands"
        ))
        
        logging.info(f"Connected to {len(self.guilds)} guilds!")

    @tasks.loop(seconds=30)
    async def check_inactive_players(self):
        """Task to check and disconnect inactive players"""
        for guild in self.guilds:
            player = cast(MusicPlayer, guild.voice_client)
            if not player:
                continue
                
            # Check if player is inactive and not playing anything
            if player.is_inactive() and not player.playing:
                await player.disconnect()
                if player.home:
                    try:
                        await player.home.send("🔌 Disconnected due to inactivity.")
                    except discord.HTTPException:
                        pass
    
    @check_inactive_players.before_loop
    async def before_check_inactive(self):
        await self.wait_until_ready()

    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player: MusicPlayer = payload.player
        track: wavelink.Playable = payload.track
        
        # Set the current track
        player.current_track = track
        await player.update_last_interaction()

        # Create embed for now playing
        embed = discord.Embed(
            title="Now Playing 🎶",
            description=f"**{track.title}**\nby `{track.author}`",
            color=discord.Color.blurple()
        )
        
        if track.artwork:
            embed.set_image(url=track.artwork)
        
        embed.add_field(
            name="Duration", 
            value=player.format_duration(track.length), 
            inline=True
        )
        
        # Add progress bar
        progress_bar = player.create_progress_bar(0, track.length)
        time_display = f"0:00 {progress_bar} {player.format_duration(track.length)}"
        embed.add_field(name="Progress", value=time_display, inline=False)
        
        # Add source info
        source_icon = "🎵"
        if "youtube" in track.source:
            source_icon = "🔴"
        elif "spotify" in track.source:
            source_icon = "💚"
            
        embed.add_field(name="Source", value=f"{source_icon} {track.source}", inline=True)
        
        if player.loop:
            embed.add_field(name="Loop", value="🔂 Track loop enabled", inline=True)
        elif player.loop_queue:
            embed.add_field(name="Loop", value="🔁 Queue loop enabled", inline=True)
            
        # Send now playing message
        if hasattr(player, "home") and player.home:
            player.progress_message = await player.home.send(embed=embed)

    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player: MusicPlayer = payload.player
        
        # Clean up progress message if it exists
        if player.progress_message:
            try:
                await player.progress_message.delete()
            except discord.HTTPException:
                pass
            player.progress_message = None
        
        # Handle track loops
        if player.loop and payload.reason == 'finished':
            # If track loop is enabled, play the same track again
            await player.play(payload.track)
            return
        
        # Handle queue loops
        if player.loop_queue and payload.reason == 'finished' and not player.queue.is_empty:
            # If we reached the end of a track and queue loop is enabled,
            # add the current track to the end of the queue
            await player.queue.put_wait(payload.track)
    
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        logging.info(f"Wavelink Node {payload.node.identifier} is ready!")
    
    async def on_wavelink_websocket_closed(self, payload: wavelink.WebsocketClosedEventPayload) -> None:
        logging.warning(f"Wavelink WebSocket closed with code {payload.code} for guild {payload.guild_id}")


bot = Bot()


# Helper function to check if user has DJ permissions
async def has_dj_permissions(interaction: discord.Interaction) -> bool:
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    if not player or not player.dj_role_required:
        return True
    
    # The bot owner and server owner always have DJ permissions
    if interaction.user.id == interaction.guild.owner_id or await bot.is_owner(interaction.user):
        return True
    
    # Check if user has the DJ role
    dj_role = discord.utils.get(interaction.guild.roles, name=DJ_ROLE_NAME)
    if dj_role and dj_role in interaction.user.roles:
        return True
        
    # Check if user is in the DJ members set
    if interaction.user.id in player.dj_members:
        return True
    
    # If no DJ permissions found
    return False


# Search track helper function
async def search_tracks(query: str) -> wavelink.Search:
    # Check if it's a Spotify link
    spotify_match = SPOTIFY_REGEX.match(query)
    if spotify_match:
        spotify_type = spotify_match.group("type")
        spotify_id = spotify_match.group("id")
        
        # Create proper Spotify URL format for wavelink
        if spotify_type == "track":
            query = f"spsearch:{query}"  # Search Spotify track
        else:
            # It's a playlist or album - use direct Spotify URL
            return await wavelink.Playable.search(query)
    
    # YouTube playlist handling
    youtube_playlist_match = YOUTUBE_PLAYLIST_REGEX.match(query)
    if youtube_playlist_match:
        return await wavelink.Playable.search(query)
    
    # Regular search
    return await wavelink.Playable.search(query)


@bot.tree.command(name="play", description="Play a song with the given query.")
@app_commands.describe(query="The song name or URL to play")
async def play(interaction: discord.Interaction, query: str) -> None:
    """Play a song with the given query."""
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    await interaction.response.defer()
    
    # Only update last_interaction if we have a voice client
    if interaction.guild.voice_client:
        await MusicPlayer.update_last_interaction(interaction.guild.voice_client)

    # Check if user is in a voice channel
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.followup.send("You need to be in a voice channel to use this command.", ephemeral=True)
        return

    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)

    if not player:
        try:
            player = await interaction.user.voice.channel.connect(cls=MusicPlayer)
            # Set the volume from saved settings
            volume = bot.volume_manager.get_volume(interaction.guild.id)
            await player.set_volume(volume)
        except AttributeError:
            await interaction.followup.send("Please join a voice channel first before using this command.", ephemeral=True)
            return
        except discord.ClientException:
            await interaction.followup.send("I was unable to join this voice channel. Please try again.", ephemeral=True)
            return

    if not hasattr(player, "home"):
        player.home = interaction.channel
    elif player.home != interaction.channel:
        await interaction.followup.send(f"You can only play songs in {player.home.mention}, as the player has already started there.", ephemeral=True)
        return

    try:
        # Use the improved search function
        tracks: wavelink.Search = await search_tracks(query)
        
        if not tracks:
            await interaction.followup.send(f"Could not find any tracks with that query. Please try again.", ephemeral=True)
            return

        if isinstance(tracks, wavelink.Playlist):
            added: int = await player.queue.put_wait(tracks)
            
            # Create an embed with playlist information
            embed = discord.Embed(
                title="Added Playlist to Queue 📑",
                description=f"**{tracks.name}**",
                color=discord.Color.green()
            )
            
            embed.add_field(name="Tracks Added", value=f"{added} songs", inline=True)
            embed.add_field(name="Total Duration", value=f"{player.format_duration(sum(track.length for track in tracks))}", inline=True)
            
            if tracks.artwork:
                embed.set_thumbnail(url=tracks.artwork)
                
            await interaction.followup.send(embed=embed)
        elif len(tracks) > 1:
            # Store the search results for this user
            bot.search_results[interaction.user.id] = tracks[:5]  # Store up to 5 results
            
            # Create selection embed
            embed = discord.Embed(
                title="Search Results 🔍",
                description="Please select a track to play by using the `/select` command with the track number.",
                color=discord.Color.blue()
            )
            
            # Add tracks to embed
            for i, track in enumerate(tracks[:5], 1):
                duration = player.format_duration(track.length)
                embed.add_field(
                    name=f"{i}. {track.title}",
                    value=f"by `{track.author}` • Duration: `{duration}`",
                    inline=False
                )
                
            embed.set_footer(text=f"Use '/select <number>' to choose a track • Results will expire in 60 seconds")
            
            await interaction.followup.send(embed=embed)
            
            # Set a timer to clear these results after 60 seconds
            await asyncio.sleep(60)
            if interaction.user.id in bot.search_results:
                del bot.search_results[interaction.user.id]
        else:
            # Single track found
            track: wavelink.Playable = tracks[0]
            await player.queue.put_wait(track)
            
            # Create an embed with track information
            embed = discord.Embed(
                title="Added to Queue 🎵",
                description=f"**{track.title}**\nby `{track.author}`",
                color=discord.Color.green()
            )
            
            embed.add_field(name="Duration", value=player.format_duration(track.length), inline=True)
            embed.add_field(name="Position", value=f"#{player.queue.count}" if player.playing else "Next", inline=True)
            
            if track.artwork:
                embed.set_thumbnail(url=track.artwork)
                
            await interaction.followup.send(embed=embed)

        if not player.playing:
            await player.play(player.queue.get(), volume=bot.volume_manager.get_volume(interaction.guild.id))
            
    except Exception as e:
        logging.error(f"Error in play command: {e}", exc_info=True)
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@bot.tree.command(name="select", description="Select a track from your search results.")
@app_commands.describe(number="The track number to select (1-5)")
async def select(interaction: discord.Interaction, number: int) -> None:
    """Select a track from search results."""
    await interaction.response.defer()
    await MusicPlayer.update_last_interaction(interaction.guild.voice_client if interaction.guild.voice_client else None)
    
    if interaction.user.id not in bot.search_results:
        await interaction.followup.send("You don't have any active search results. Use `/play` to search for songs first.", ephemeral=True)
        return
        
    if not 1 <= number <= len(bot.search_results[interaction.user.id]):
        await interaction.followup.send(f"Please select a valid number between 1 and {len(bot.search_results[interaction.user.id])}.", ephemeral=True)
        return
        
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    if not player:
        await interaction.followup.send("The bot is not currently in a voice channel.", ephemeral=True)
        return
    
    # Get the selected track and add it to the queue
    track = bot.search_results[interaction.user.id][number-1]
    await player.queue.put_wait(track)
    
    # Create an embed with track information
    embed = discord.Embed(
        title="Added to Queue 🎵",
        description=f"**{track.title}**\nby `{track.author}`",
        color=discord.Color.green()
    )
    
    embed.add_field(name="Duration", value=player.format_duration(track.length), inline=True)
    embed.add_field(name="Position", value=f"#{player.queue.count}" if player.playing else "Next", inline=True)
    
    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    
    # Clean up search results
    del bot.search_results[interaction.user.id]
    
    await interaction.followup.send(embed=embed)
    
    if not player.playing:
        await player.play(player.queue.get(), volume=bot.volume_manager.get_volume(interaction.guild.id))


@bot.tree.command(name="skip", description="Skip the current song.")
async def skip(interaction: discord.Interaction) -> None:
    """Skip the current song."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player or not player.playing:
        await interaction.response.send_message("No song is currently playing.", ephemeral=True)
        return
    
    # Check DJ permissions if required for destructive actions
    if player.dj_role_required and not await has_dj_permissions(interaction):
        await interaction.response.send_message("You need the DJ role to use this command.", ephemeral=True)
        return

    # Temporarily disable loop for this skip
    was_looping = player.loop
    player.loop = False
    
    # Skip the current track
    current_track = player.current_track
    await player.stop()
    
    # Restore loop state
    player.loop = was_looping
    
    # Create embed for skip confirmation
    embed = discord.Embed(
        title="⏭️ Skipped Track",
        description=f"**{current_track.title}**\nby `{current_track.author}`",
        color=discord.Color.blue()
    )
    
    if player.queue.is_empty:
        embed.add_field(name="Queue Status", value="No more tracks in queue", inline=True)
    else:
        next_track = player.queue.peek()
        embed.add_field(name="Up Next", value=f"**{next_track.title}**\nby `{next_track.author}`", inline=True)
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="pause", description="Pause the currently playing song.")
async def pause(interaction: discord.Interaction) -> None:
    """Pause the currently playing song."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player or not player.playing:
        await interaction.response.send_message("No song is currently playing.", ephemeral=True)
        return
        
    if player.paused:
        await interaction.response.send_message("The player is already paused.", ephemeral=True)
        return

    await player.pause(True)  # Explicitly pass True to pause the player
    await interaction.response.send_message("⏸️ Paused the current song.")


@bot.tree.command(name="resume", description="Resume the paused song.")
async def resume(interaction: discord.Interaction) -> None:
    """Resume the paused song."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return
        
    if not player.paused:
        await interaction.response.send_message("The player is not currently paused.", ephemeral=True)
        return

    await player.pause(False)  # Explicitly pass False to resume the player
    await interaction.response.send_message("▶️ Resumed the song.")


@bot.tree.command(name="stop", description="Stop the music and clear the queue.")
async def stop(interaction: discord.Interaction) -> None:
    """Stop the music and clear the queue."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return
    
    # Check DJ permissions
    if player.dj_role_required and not await has_dj_permissions(interaction):
        await interaction.response.send_message("You need the DJ role to use this command.", ephemeral=True)
        return

    await player.stop()
    player.queue.clear()
    player.loop = False
    player.loop_queue = False
    
    await interaction.response.send_message("⏹️ Stopped the music and cleared the queue.")


@bot.tree.command(name="queue", description="Show the current music queue.")
async def queue(interaction: discord.Interaction) -> None:
    """Show the current music queue."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return

    if player.queue.is_empty and not player.current_track:
        await interaction.response.send_message("The queue is empty.", ephemeral=True)
        return

    queue_list = list(player.queue)
    embed = discord.Embed(title="🎶 Music Queue", color=discord.Color.blue())
    
    # Add queue status info
    status_icons = []
    if player.loop:
        status_icons.append("🔂 Track loop")
    if player.loop_queue:
        status_icons.append("🔁 Queue loop")
    
    if status_icons:
        embed.add_field(name="Queue Status", value=" • ".join(status_icons), inline=False)

    # Show currently playing track
    if player.current_track:
        current_track = player.current_track
        current_pos = int(player.position)
        duration = current_track.length
        
        # Create progress bar
        progress_bar = player.create_progress_bar(current_pos, duration)
        time_info = f"{player.format_duration(current_pos)} {progress_bar} {player.format_duration(duration)}"
        
        embed.add_field(
            name="Currently Playing",
            value=f"**{current_track.title}**\nby `{current_track.author}`\n{time_info}",
            inline=False
        )
        
        if current_track.artwork:
            embed.set_thumbnail(url=current_track.artwork)

    # Calculate total duration of queue
    total_duration = sum(track.length for track in queue_list)
    total_tracks = len(queue_list)
    
    # Show queue tracks
    if queue_list:
        queue_text = ""
        for i, track in enumerate(queue_list[:10], 1):
            duration = player.format_duration(track.length)
            queue_text += f"`{i}.` **{track.title}** - `{track.author}` • `{duration}`\n"
            
        if len(queue_list) > 10:
            queue_text += f"\n... and {len(queue_list) - 10} more tracks"
            
        embed.add_field(name="Up Next", value=queue_text, inline=False)
        
        # Add queue summary
        embed.add_field(
            name="Queue Summary", 
            value=f"**{total_tracks}** tracks • Total duration: **{player.format_duration(total_duration)}**",
            inline=False
        )
    else:
        embed.add_field(name="Up Next", value="No more tracks in queue", inline=False)
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="nowplaying", description="Show information about the currently playing song.")
async def nowplaying(interaction: discord.Interaction) -> None:
    """Show information about the currently playing track."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player or not player.current_track:
        await interaction.response.send_message("No song is currently playing.", ephemeral=True)
        return
    
    track = player.current_track
    position = int(player.position)
    
    # Create embed with track details
    embed = discord.Embed(
        title="Now Playing 🎶",
        description=f"**{track.title}**\nby `{track.author}`",
        color=discord.Color.blurple()
    )
    
    if track.artwork:
        embed.set_image(url=track.artwork)
    
    # Add progress bar
    progress_bar = player.create_progress_bar(position, track.length)
    time_info = f"{player.format_duration(position)} {progress_bar} {player.format_duration(track.length)}"
    embed.add_field(name="Progress", value=time_info, inline=False)
    
    # Add source information
    source_icon = "🎵"
    if "youtube" in track.source:
        source_icon = "🔴"
    elif "spotify" in track.source:
        source_icon = "💚"
        
    embed.add_field(name="Source", value=f"{source_icon} {track.source}", inline=True)
    
    # Add loop status
    if player.loop:
        embed.add_field(name="Loop", value="🔂 Track loop enabled", inline=True)
    elif player.loop_queue:
        embed.add_field(name="Loop", value="🔁 Queue loop enabled", inline=True)
    
    # Add volume info
    embed.add_field(name="Volume", value=f"🔊 {player.volume}%", inline=True)
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="disconnect", description="Disconnect the bot from the voice channel.")
async def disconnect(interaction: discord.Interaction) -> None:
    """Disconnect the bot from the voice channel."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return
    
    # Check DJ permissions
    if player.dj_role_required and not await has_dj_permissions(interaction):
        await interaction.response.send_message("You need DJ permissions to disconnect the bot.", ephemeral=True)
        return
    
    await player.disconnect()
    
    # Create embed for disconnect
    embed = discord.Embed(
        title="Disconnected 👋",
        description="Successfully disconnected from the voice channel.",
        color=discord.Color.red()
    )
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="volume", description="Change the volume of the player (0-100).")
@app_commands.describe(value="The volume level to set (0-100).")
async def volume(interaction: discord.Interaction, value: int) -> None:
    """Change the volume of the player (0-100)."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return

    if not 0 <= value <= 100:
        await interaction.response.send_message("Volume must be between 0 and 100.", ephemeral=True)
        return

    # Set the volume and save the preference
    await player.set_volume(value)
    bot.volume_manager.set_volume(interaction.guild.id, value)
    
    # Create volume embed with visual indicator
    volume_bar = player.create_progress_bar(value, 100, length=10)
    
    embed = discord.Embed(
        title="Volume Changed 🔊",
        description=f"Set volume to **{value}%**\n{volume_bar}",
        color=discord.Color.blue()
    )
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="loop", description="Toggle looping for the current track or entire queue.")
@app_commands.describe(mode="Loop mode: 'track', 'queue', or 'off'")
@app_commands.choices(mode=[
    app_commands.Choice(name="Track Loop 🔂", value="track"),
    app_commands.Choice(name="Queue Loop 🔁", value="queue"),
    app_commands.Choice(name="Loop Off ⏹️", value="off"),
])
async def loop(interaction: discord.Interaction, mode: str) -> None:
    """Toggle looping for the current track or entire queue."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return
    
    # Check DJ permissions
    if player.dj_role_required and not await has_dj_permissions(interaction):
        await interaction.response.send_message("You need DJ permissions to change loop settings.", ephemeral=True)
        return
    
    # Set the loop mode
    if mode == "track":
        player.loop = True
        player.loop_queue = False
        message = "🔂 Enabled track loop mode! The current song will repeat."
    elif mode == "queue":
        player.loop = False
        player.loop_queue = True
        message = "🔁 Enabled queue loop mode! The entire queue will repeat."
    else:  # off
        player.loop = False
        player.loop_queue = False
        message = "⏹️ Disabled all loop modes."
    
    await interaction.response.send_message(message)


@bot.tree.command(name="shuffle", description="Shuffle the current queue.")
async def shuffle(interaction: discord.Interaction) -> None:
    """Shuffle the current queue."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return
    
    if player.queue.is_empty:
        await interaction.response.send_message("The queue is empty.", ephemeral=True)
        return
    
    # Check DJ permissions
    if player.dj_role_required and not await has_dj_permissions(interaction):
        await interaction.response.send_message("You need DJ permissions to shuffle the queue.", ephemeral=True)
        return
    
    # Get all tracks from queue
    queue_tracks = list(player.queue)
    
    # Clear the queue and shuffle tracks
    player.queue.clear()
    random.shuffle(queue_tracks)
    
    # Add shuffled tracks back to queue
    for track in queue_tracks:
        await player.queue.put_wait(track)
        
    embed = discord.Embed(
        title="Queue Shuffled 🔀",
        description=f"Shuffled {len(queue_tracks)} tracks in the queue.",
        color=discord.Color.green()
    )
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="remove", description="Remove a specific track from the queue.")
@app_commands.describe(position="The position of the track to remove (1, 2, 3, etc.)")
async def remove(interaction: discord.Interaction, position: int) -> None:
    """Remove a specific track from the queue."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return
    
    if player.queue.is_empty:
        await interaction.response.send_message("The queue is empty.", ephemeral=True)
        return
    
    # Check DJ permissions
    if player.dj_role_required and not await has_dj_permissions(interaction):
        await interaction.response.send_message("You need DJ permissions to remove tracks from the queue.", ephemeral=True)
        return
    
    # Validate position
    if position < 1 or position > player.queue.count:
        await interaction.response.send_message(f"Invalid position. Please choose a number between 1 and {player.queue.count}.", ephemeral=True)
        return
    
    # Get all tracks from queue
    queue_tracks = list(player.queue)
    
    # Get track to remove
    removed_track = queue_tracks[position - 1]
    
    # Clear queue and add back all tracks except the removed one
    player.queue.clear()
    for i, track in enumerate(queue_tracks):
        if i != position - 1:
            await player.queue.put_wait(track)
    
    embed = discord.Embed(
        title="Track Removed ❌",
        description=f"Removed **{removed_track.title}**\nby `{removed_track.author}` from the queue.",
        color=discord.Color.red()
    )
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="clear", description="Clear the entire queue.")
async def clear(interaction: discord.Interaction) -> None:
    """Clear the entire queue."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return
    
    if player.queue.is_empty:
        await interaction.response.send_message("The queue is already empty.", ephemeral=True)
        return
    
    # Check DJ permissions
    if player.dj_role_required and not await has_dj_permissions(interaction):
        await interaction.response.send_message("You need DJ permissions to clear the queue.", ephemeral=True)
        return
    
    queue_size = player.queue.count
    player.queue.clear()
    
    embed = discord.Embed(
        title="Queue Cleared 🧹",
        description=f"Cleared {queue_size} tracks from the queue.",
        color=discord.Color.red()
    )
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="seek", description="Seek to a specific position in the current track.")
@app_commands.describe(position="Position in seconds (e.g., 30 for 0:30, 120 for 2:00)")
async def seek(interaction: discord.Interaction, position: int) -> None:
    """Seek to a specific position in the current track."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player or not player.current_track:
        await interaction.response.send_message("No song is currently playing.", ephemeral=True)
        return
    
    # Convert seconds to milliseconds
    position_ms = position * 1000
    
    # Ensure position is within track bounds
    if position_ms < 0 or position_ms > player.current_track.length:
        await interaction.response.send_message(
            f"Position must be between 0 and {player.current_track.length // 1000} seconds.", 
            ephemeral=True
        )
        return
    
    # Seek to position
    await player.seek(position_ms)
    
    # Create embed for seek confirmation
    embed = discord.Embed(
        title="Position Changed ⏩",
        description=f"Seeking to position `{player.format_duration(position_ms)}` in the current track.",
        color=discord.Color.blue()
    )
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="dj", description="Toggle DJ mode or add/remove a user as DJ.")
@app_commands.describe(
    action="Enable/disable DJ mode or add/remove a DJ",
    user="The user to add or remove as DJ (only needed for add/remove actions)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="Enable DJ Mode", value="enable"),
    app_commands.Choice(name="Disable DJ Mode", value="disable"),
    app_commands.Choice(name="Add DJ", value="add"),
    app_commands.Choice(name="Remove DJ", value="remove"),
])
async def dj(
    interaction: discord.Interaction, 
    action: str, 
    user: discord.Member = None
) -> None:
    """Toggle DJ mode or add/remove a user as DJ."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    
    # Only guild owner or admin can change DJ settings
    if not interaction.user.guild_permissions.administrator and interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("You need administrator permissions to manage DJ settings.", ephemeral=True)
        return
    
    if not player and interaction.guild.voice_client:
        player = cast(MusicPlayer, interaction.guild.voice_client)
    
    if not player:
        # Create a temporary player object if none exists
        player = MusicPlayer(client=bot)
    
    if action == "enable":
        player.dj_role_required = True
        await interaction.response.send_message("🎧 Enabled DJ mode. Only users with the DJ role can use certain commands.")
    
    elif action == "disable":
        player.dj_role_required = False
        await interaction.response.send_message("🎧 Disabled DJ mode. All users can use all commands.")
    
    elif action in ["add", "remove"]:
        if not user:
            await interaction.response.send_message("Please specify a user to add or remove as DJ.", ephemeral=True)
            return
        
        if action == "add":
            player.dj_members.add(user.id)
            await interaction.response.send_message(f"🎧 Added {user.mention} as a DJ.")
        else:  # remove
            if user.id in player.dj_members:
                player.dj_members.remove(user.id)
                await interaction.response.send_message(f"🎧 Removed {user.mention} from DJs.")
            else:
                await interaction.response.send_message(f"{user.mention} is not a DJ.", ephemeral=True)
    
    # Update last interaction
    await MusicPlayer.update_last_interaction(player)


@bot.tree.command(name="boost", description="Apply a sound filter to the player.")
@app_commands.describe(filter_type="The type of audio filter to apply")
@app_commands.choices(filter_type=[
    app_commands.Choice(name="Bass Boost", value="bassboost"),
    app_commands.Choice(name="Nightcore", value="nightcore"),
    app_commands.Choice(name="8D Audio", value="8d"),
    app_commands.Choice(name="Clear Filters", value="clear"),
])
async def boost(interaction: discord.Interaction, filter_type: str) -> None:
    """Apply a sound filter to the player."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player or not player.playing:
        await interaction.response.send_message("No song is currently playing.", ephemeral=True)
        return
    
    filters = player.filters
    filter_name = filter_type.capitalize()
    
    await interaction.response.defer()
    
    try:
        if filter_type == "bassboost":
            # Apply bass boost EQ
            bands = [
                {"band": 0, "gain": 0.6},  # 60Hz
                {"band": 1, "gain": 0.5},  # 150Hz
                {"band": 2, "gain": 0.3},  # 400Hz
                {"band": 3, "gain": 0.1},  # 1kHz
            ]
            filters.equalizer.set(bands=bands)
            filter_name = "Bass Boost"
            
        elif filter_type == "nightcore":
            # Increase speed and pitch
            filters.timescale.set(speed=1.15, pitch=1.2, rate=1.0)
            filter_name = "Nightcore"
            
        elif filter_type == "8d":
            # Apply rotating 8D audio effect
            filters.rotation.set(rotation_hz=0.2)  # Rotate 0.2 times per second
            filter_name = "8D Audio"
            
        elif filter_type == "clear":
            # Reset all filters
            await player.set_filters()
            await interaction.followup.send("🔄 Cleared all audio filters.")
            return
        
        # Apply the selected filter
        await player.set_filters(filters)
        
        embed = discord.Embed(
            title="Filter Applied 🎛️",
            description=f"Applied the **{filter_name}** filter to the player.",
            color=discord.Color.purple()
        )
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logging.error(f"Error applying filter: {e}", exc_info=True)
        await interaction.followup.send(f"An error occurred while applying the filter: {e}", ephemeral=True)


@bot.tree.command(name="lyrics", description="Try to find lyrics for the current song.")
async def lyrics(interaction: discord.Interaction) -> None:
    """Try to find lyrics for the current song."""
    player: MusicPlayer = cast(MusicPlayer, interaction.guild.voice_client)
    await MusicPlayer.update_last_interaction(player)
    
    if not player or not player.current_track:
        await interaction.response.send_message("No song is currently playing.", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    track = player.current_track
    query = f"{track.title} {track.author} lyrics"
    
    embed = discord.Embed(
        title=f"Lyrics for {track.title}",
        description="This feature would connect to a lyrics API to fetch lyrics.\n\n"
                   "Since we don't have an actual lyrics API in this example, this is a placeholder. "
                   "In a real implementation, you would:\n"
                   "1. Use a service like Genius, Musixmatch, or others\n"
                   "2. Make an API request using the song title and artist\n"
                   "3. Display the formatted lyrics here\n\n"
                   f"Search query would be: `{query}`",
        color=discord.Color.gold()
    )
    
    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="help", description="Show a list of all available commands.")
async def help_command(interaction: discord.Interaction) -> None:
    """Show a list of all available commands."""
    embed = discord.Embed(
        title="🎵 Stellara Music Bot Commands",
        description="Here are all the available commands for the music bot:",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🎶 Playback Commands",
        value=(
            "`/play <query>` - Play a song with the given query (supports YouTube/Spotify links)\n"
            "`/pause` - Pause the currently playing song\n"
            "`/resume` - Resume the paused song\n"
            "`/stop` - Stop the music and clear the queue\n"
            "`/skip` - Skip the current song\n"
            "`/seek <position>` - Jump to a specific position in the current track"
        ),
        inline=False
    )

    embed.add_field(
        name="📋 Queue Commands",
        value=(
            "`/queue` - Show the current music queue\n"
            "`/nowplaying` - Show details about the currently playing track\n"
            "`/clear` - Clear the entire queue\n"
            "`/remove <position>` - Remove a specific track from the queue\n"
            "`/shuffle` - Shuffle the tracks in the queue\n"
            "`/loop <mode>` - Set loop mode (track, queue, or off)"
        ),
        inline=False
    )

    embed.add_field(
        name="🔍 Search Commands",
        value=(
            "`/select <number>` - Select a track from search results"
        ),
        inline=False
    )

    embed.add_field(
        name="🔊 Audio Controls",
        value=(
            "`/volume <value>` - Change the volume of the player (0-100)\n"
            "`/boost <filter>` - Apply audio filters (bassboost, nightcore, 8d, clear)"
        ),
        inline=False
    )

    embed.add_field(
        name="🛠️ Other Commands",
        value=(
            "`/disconnect` - Disconnect the bot from the voice channel\n"
            "`/lyrics` - Try to find lyrics for the current song\n"
            "`/dj <action>` - Manage DJ mode and permissions"
        ),
        inline=False
    )

    embed.set_footer(text="Made with ❤️ | Bot automatically disconnects after 5 minutes of inactivity")
    embed.set_thumbnail(url="https://i.imgur.com/8eQj6wQ.png")

    await interaction.response.send_message(embed=embed)


async def main() -> None:
    # Retrieve the bot token from the .env file
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise ValueError("Bot token not found in .env file.")

    async with bot:
        await bot.start(bot_token)


asyncio.run(main())
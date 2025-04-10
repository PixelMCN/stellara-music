import asyncio
import logging
import time
from typing import cast, Optional

import discord
from discord import app_commands
from discord.ext import commands
import wavelink
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

GUILD_ID = discord.Object(id=1358804712018804916)  # Replace with your guild ID

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

        # Sync slash commands
        await self.tree.sync()

    async def on_ready(self) -> None:
        logging.info("Logged in: %s | %s", self.user, self.user.id)
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="/help for commands"
        ))

    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player: wavelink.Player = payload.player
        track: wavelink.Playable = payload.track

        embed = discord.Embed(
            title="Now Playing 🎶",
            description=f"**{track.title}** by `{track.author}`",
            color=discord.Color.blurple()
        )
        if track.artwork:
            embed.set_image(url=track.artwork)

        duration = f"{track.length // 60000}:{(track.length // 1000) % 60:02}"
        embed.add_field(name="Duration", value=duration, inline=True)

        if hasattr(player, "home") and player.home:
            await player.home.send(embed=embed)


bot = Bot()


@bot.tree.command(name="play", description="Play a song with the given query.")
async def play(interaction: discord.Interaction, query: str) -> None:
    """Play a song with the given query."""
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    await interaction.response.defer()

    player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)

    if not player:
        try:
            player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
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
        tracks: wavelink.Search = await wavelink.Playable.search(query)
        if not tracks:
            await interaction.followup.send(f"Could not find any tracks with that query. Please try again.", ephemeral=True)
            return

        if isinstance(tracks, wavelink.Playlist):
            added: int = await player.queue.put_wait(tracks)
            await interaction.followup.send(f"Added the playlist **`{tracks.name}`** ({added} songs) to the queue.")
        else:
            track: wavelink.Playable = tracks[0]
            await player.queue.put_wait(track)
            await interaction.followup.send(f"Added **`{track.title}`** to the queue.")

        if not player.playing:
            await player.play(player.queue.get(), volume=30)
    except Exception as e:
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@bot.tree.command(name="skip", description="Skip the current song.")
async def skip(interaction: discord.Interaction) -> None:
    """Skip the current song."""
    player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
    if not player or not player.playing:
        await interaction.response.send_message("No song is currently playing.", ephemeral=True)
        return

    await player.stop()
    await interaction.response.send_message("⏭️ Skipped the current song.")


@bot.tree.command(name="pause", description="Pause the currently playing song.")
async def pause(interaction: discord.Interaction) -> None:
    """Pause the currently playing song."""
    player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
    if not player or not player.playing:
        await interaction.response.send_message("No song is currently playing.", ephemeral=True)
        return

    await player.pause(True)  # Explicitly pass True to pause the player
    await interaction.response.send_message("⏸️ Paused the current song.")


@bot.tree.command(name="resume", description="Resume the paused song.")
async def resume(interaction: discord.Interaction) -> None:
    """Resume the paused song."""
    player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
    if not player or not player.paused:
        await interaction.response.send_message("No song is currently paused.", ephemeral=True)
        return

    await player.pause(False)  # Explicitly pass False to resume the player
    await interaction.response.send_message("▶️ Resumed the song.")


@bot.tree.command(name="stop", description="Stop the music and clear the queue.")
async def stop(interaction: discord.Interaction) -> None:
    """Stop the music and clear the queue."""
    player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return

    await player.stop()
    player.queue.clear()
    await interaction.response.send_message("⏹️ Stopped the music and cleared the queue.")


@bot.tree.command(name="queue", description="Show the current music queue.")
async def queue(interaction: discord.Interaction) -> None:
    """Show the current music queue."""
    player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return

    if player.queue.is_empty:
        await interaction.response.send_message("The queue is empty.", ephemeral=True)
        return

    queue_list = list(player.queue)
    embed = discord.Embed(title="Music Queue", color=discord.Color.blue())

    if player.current:
        embed.add_field(
            name="Currently Playing",
            value=f"**{player.current.title}** - `{player.current.author}`",
            inline=False
        )

    queue_text = "\n".join([f"`{i+1}.` **{track.title}** - `{track.author}`" for i, track in enumerate(queue_list[:10])])

    if len(queue_list) > 10:
        queue_text += f"\n\n... and {len(queue_list) - 10} more tracks"

    embed.description = queue_text
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="disconnect", description="Disconnect the bot from the voice channel.")
async def disconnect(interaction: discord.Interaction) -> None:
    """Disconnect the bot from the voice channel."""
    player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return

    await player.disconnect()
    await interaction.response.send_message("👋 Disconnected from the voice channel.")


@bot.tree.command(name="volume", description="Change the volume of the player (0-100).")
@app_commands.describe(value="The volume level to set (0-100).")
async def volume(interaction: discord.Interaction, value: int) -> None:
    """Change the volume of the player (0-100)."""
    player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return

    if not 0 <= value <= 100:
        await interaction.response.send_message("Volume must be between 0 and 100.", ephemeral=True)
        return

    await player.set_volume(value)
    await interaction.response.send_message(f"Set volume to **{value}%** 🔊")


@bot.tree.command(name="boost", description="Apply a bass boost filter.")
async def boost(interaction: discord.Interaction) -> None:
    """Apply a bass boost filter."""
    player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
    if not player:
        await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return

    # Correct format for bands: list of dictionaries with "band" and "gain"
    bands = [
        {"band": 0, "gain": 0.3},
        {"band": 1, "gain": 0.3},
        {"band": 2, "gain": 0.2},
        {"band": 3, "gain": 0.1},
    ]

    filters = player.filters
    filters.equalizer.set(bands=bands)
    await player.set_filters(filters)
    await interaction.response.send_message("Applied bass boost filter! 🔊")


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
            "`/play <query>` - Play a song with the given query.\n"
            "`/pause` - Pause the currently playing song.\n"
            "`/resume` - Resume the paused song.\n"
            "`/stop` - Stop the music and clear the queue.\n"
            "`/skip` - Skip the current song."
        ),
        inline=False
    )

    embed.add_field(
        name="📋 Queue Commands",
        value=(
            "`/queue` - Show the current music queue.\n"
            "`/disconnect` - Disconnect the bot from the voice channel."
        ),
        inline=False
    )

    embed.add_field(
        name="🔊 Audio Controls",
        value=(
            "`/volume <value>` - Change the volume of the player (0-100).\n"
            "`/boost` - Apply a bass boost filter."
        ),
        inline=False
    )

    embed.set_footer(text="Use these commands to control the music bot!")
    embed.set_thumbnail(url="https://i.imgur.com/8eQj6wQ.png")  # Add a thumbnail for aesthetics

    await interaction.response.send_message(embed=embed)


async def main() -> None:
    # Retrieve the bot token from the .env file
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise ValueError("Bot token not found in .env file.")

    async with bot:
        await bot.start(bot_token)


asyncio.run(main())
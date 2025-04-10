import asyncio
import logging
from typing import cast

import discord
from discord.ext import commands
import wavelink
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

class Bot(commands.Bot):  # Inherit from commands.Bot
    def __init__(self) -> None:
        intents: discord.Intents = discord.Intents.default()
        intents.message_content = True

        discord.utils.setup_logging(level=logging.INFO)
        super().__init__(command_prefix="!", intents=intents)  # Correctly call the parent class initializer

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


bot: Bot = Bot()


@bot.tree.command(name="play", description="Play a song with the given query.")
async def play(interaction: discord.Interaction, query: str) -> None:
    """Play a song with the given query."""
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    player: wavelink.Player
    player = cast(wavelink.Player, interaction.guild.voice_client)  # type: ignore

    if not player:
        try:
            player = await interaction.user.voice.channel.connect(cls=wavelink.Player)  # type: ignore
        except AttributeError:
            await interaction.response.send_message("Please join a voice channel first before using this command.", ephemeral=True)
            return
        except discord.ClientException:
            await interaction.response.send_message("I was unable to join this voice channel. Please try again.", ephemeral=True)
            return

    player.autoplay = wavelink.AutoPlayMode.enabled

    if not hasattr(player, "home"):
        player.home = interaction.channel
    elif player.home != interaction.channel:
        await interaction.response.send_message(f"You can only play songs in {player.home.mention}, as the player has already started there.", ephemeral=True)
        return

    try:
        tracks: wavelink.Search = await wavelink.Playable.search(query)
        if not tracks:
            await interaction.response.send_message(f"Could not find any tracks with that query. Please try again.", ephemeral=True)
            return

        if isinstance(tracks, wavelink.Playlist):
            added: int = await player.queue.put_wait(tracks)
            await interaction.response.send_message(f"Added the playlist **`{tracks.name}`** ({added} songs) to the queue.")
        else:
            track: wavelink.Playable = tracks[0]
            await player.queue.put_wait(track)
            await interaction.response.send_message(f"Added **`{track.title}`** to the queue.")

        if not player.playing:
            await player.play(player.queue.get(), volume=30)
    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
        return


async def main() -> None:
    # Retrieve the bot token from the .env file
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise ValueError("Bot token not found in .env file.")

    async with bot:
        await bot.start(bot_token)


asyncio.run(main())
import asyncio
import logging
import os
import time
from datetime import datetime

import discord
import yaml
from dotenv import load_dotenv

from src.bot.helper import BotHelper
from src.config.cliargs import CLIArgs
from src.utils.commandline import CommandLine
from src.utils.pdf_generator import pdf_generator

load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
USER_MAP_FILE_PATH = os.getenv("USER_MAP_FILE_PATH")

logger = logging.getLogger()  # root logger


def configure_logging():
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('faster_whisper').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

    # Ensure the directory exists
    log_directory = '.logs/transcripts'
    pdf_directory = '.logs/pdfs'
    os.makedirs(log_directory, exist_ok=True) 
    os.makedirs(pdf_directory, exist_ok=True)  

    # Get the current date for the log file name
    current_date = datetime.now().strftime('%Y-%m-%d')
    log_filename = os.path.join(log_directory, f"{current_date}-transcription.log")

    # Custom logging format (date with milliseconds, message)
    log_format = '%(asctime)s %(name)s: %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S.%f'[:-3]  # Trim to milliseconds

    if CLIArgs.verbose:
        logger.setLevel(logging.DEBUG)
        logging.basicConfig(level=logging.DEBUG,
                            format=log_format,
                            datefmt=date_format)
    else:
        logger.setLevel(logging.INFO)
        logging.basicConfig(level=logging.INFO,
                            format=log_format,
                            datefmt=date_format)
    
    # Set up the transcription logger
    transcription_logger = logging.getLogger('transcription')
    transcription_logger.setLevel(logging.INFO)

    # File handler for transcription logs (append mode)
    file_handler = logging.FileHandler(log_filename, mode='a')
    file_handler.setLevel(logging.INFO)
    
    # Custom formatter WITHOUT the automatic timestamp
    file_handler.setFormatter(logging.Formatter(
        '%(message)s'  # Only log the custom message, no automatic timestamp
    ))

    # Add the handler to the transcription logger
    transcription_logger.addHandler(file_handler)

if __name__ == "__main__":
    args = CommandLine.read_command_line()
    CLIArgs.update_from_args(args)

    configure_logging()
    
    from src.bot.volo_bot import VoloBot  
    
    bot = VoloBot()

    @bot.event
    async def on_voice_state_update(member, before, after):
        if member.id == bot.user.id:
            # If the bot left the "before" channel
            if after.channel is None:
                guild_id = before.channel.guild.id
                helper = bot.guild_to_helper.get(guild_id, None)
                if helper:
                    helper.set_vc(None)
                    bot.guild_to_helper.pop(guild_id, None)

                bot._close_and_clean_sink_for_guild(guild_id)

    @bot.tree.command(name="connect", description="Add the bot to your voice channel.")
    async def connect(interaction: discord.Interaction):
        if bot._is_ready is False:
            await interaction.response.send_message("Bot is not ready. Please try again shortly.", ephemeral=True)
            return
        author_vc = interaction.user.voice
        if not author_vc:
            await interaction.response.send_message("You are not in a voice channel.", ephemeral=True)
            return
        if bot.guild_to_helper.get(interaction.guild_id, None):
            await interaction.response.send_message("The bot is already in a voice channel on this server.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            guild_id = interaction.guild_id
            vc = await author_vc.channel.connect()
            helper = bot.guild_to_helper.get(guild_id, BotHelper(bot))
            helper.guild_id = guild_id
            helper.set_vc(vc)
            bot.guild_to_helper[guild_id] = helper
            await interaction.followup.send(f"Successfully connected to the voice channel!", ephemeral=False)
            await interaction.guild.change_voice_state(channel=author_vc.channel, self_mute=True)
        except Exception as e:
            await interaction.followup.send(f"{e}", ephemeral=True)

    @bot.tree.command(name="record", description="Start recording the conference.")
    async def ink(interaction: discord.Interaction):
        await interaction.response.defer()
        connect_text = "`/connect`"
        if not bot.guild_to_helper.get(interaction.guild_id, None):
            await interaction.followup.send(f"The bot is not connected to a voice channel. Please use {connect_text} to connect.", ephemeral=True)
            return
        if bot.guild_is_recording.get(interaction.guild_id, False):
            await interaction.followup.send("Recording is already in progress.", ephemeral=True)
            return
        bot.start_recording(interaction)
        await interaction.followup.send("Recording started!", ephemeral=False)

    @bot.tree.command(name="stop", description="Stop the recording.")
    async def stop(interaction: discord.Interaction):
        guild_id = interaction.guild_id
        helper = bot.guild_to_helper.get(guild_id, None)
        if not helper:
            await interaction.response.send_message("The bot is not in a voice channel.", ephemeral=True)
            return

        bot_vc = helper.vc
        
        if not bot_vc:
            await interaction.response.send_message("The bot is not in a voice channel.", ephemeral=True)
            return

        if not bot.guild_is_recording.get(guild_id, False):
            await interaction.response.send_message("No recording is currently in progress.", ephemeral=True)
            return

        await interaction.response.defer()
        
        if bot.guild_is_recording.get(guild_id, False):
            await bot.get_transcription(interaction)
            bot.stop_recording(interaction)
            bot.guild_is_recording[guild_id] = False
            await interaction.followup.send("Recording stopped.", ephemeral=False)
            bot.cleanup_sink(interaction)
        
    @bot.tree.command(name="disconnect", description="Disconnect the bot from the voice channel.")
    async def disconnect(interaction: discord.Interaction):
        guild_id = interaction.guild_id
        id_exists = bot.guild_to_helper.get(guild_id, None)
        if not id_exists:
            await interaction.response.send_message("The bot is not in a voice channel.", ephemeral=True)
            return
        
        helper = bot.guild_to_helper[guild_id]    
        bot_vc = helper.vc
        
        if not bot_vc:
            await interaction.response.send_message("The bot is not in a voice channel.", ephemeral=True)
            return
        
        await interaction.response.defer()
        await bot_vc.disconnect()
        helper.guild_id = None
        helper.set_vc(None)
        bot.guild_to_helper.pop(guild_id, None)

        await interaction.response.send_message("Successfully disconnected from the voice channel.", ephemeral=False)

    @bot.tree.command(name="generate_pdf", description="Generate a PDF of the transcriptions.")
    async def generate_pdf(interaction: discord.Interaction):
        guild_id = interaction.guild_id
        helper = bot.guild_to_helper.get(guild_id, None)
        if not helper:
            await interaction.response.send_message("The bot is not in a voice channel.", ephemeral=True)
            return
        transcription = await bot.get_transcription(interaction)
        if not transcription:
            await interaction.response.send_message("There are no transcriptions to generate a PDF from.", ephemeral=True)
            return
        pdf_file_path = await pdf_generator(transcription)
        if os.path.exists(pdf_file_path):
            try:
                with open(pdf_file_path, "rb") as f:
                    discord_file = discord.File(f, filename=f"conference_transcription.pdf")
                    await interaction.response.send_message("Here is the transcription from this session:", file=discord_file)
            finally:
                os.remove(pdf_file_path)
        else:
            await interaction.response.send_message("No transcription file could be generated.", ephemeral=True)


    @bot.tree.command(name="update_user_map", description="Updates the user_map. Writes to file if USER_MAP_FILE_PATH is set.")
    async def update_user_map(interaction: discord.Interaction):
        if bot.guild_is_recording.get(interaction.guild_id, False):
            await interaction.response.send_message("Cannot update the user map while recording is in progress.", ephemeral=True)
            return
        try:
            await bot.update_user_map(interaction)
            await interaction.response.send_message("User map has been updated.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Unable to update user_map.yml.:\n{e}", ephemeral=True)
            raise e


    @bot.tree.command(name="help", description="Show the help message.")
    async def help(interaction: discord.Interaction):
        embed_fields = [
            discord.EmbedField(
                name="/connect", value="Connect the bot to your voice channel.", inline=True),
            discord.EmbedField(
                name="/disconnect", value="Disconnect the bot from your voice channel.", inline=True),
            discord.EmbedField(
                name="/record", value="Start transcribing the voice channel.", inline=True),
            discord.EmbedField(
                name="/stop", value="Stop the transcription.", inline=True),
            discord.EmbedField(
                name="/generate_pdf", value="Generate a PDF of the transcriptions.", inline=True),
            discord.EmbedField(
                name="/update_user_map", value="Update the user map.", inline=True),
            discord.EmbedField(
                name="/help", value="Show this help message.", inline=True),
        ]

        embed = discord.Embed(title="Bot Help",
                              description="Commands for the transcription bot.",
                              color=discord.Color.blue(),
                              fields=embed_fields)

        await interaction.response.send_message(embed=embed, ephemeral=True)





    try:
        bot.run(DISCORD_BOT_TOKEN)
    except KeyboardInterrupt:
        logger.info("^C received, shutting down...")
        asyncio.run(bot.stop_and_cleanup())
    finally:
        asyncio.run(bot.close())
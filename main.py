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
from src.utils.txt_generator import txt_generator

load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
USER_MAP_FILE_PATH = os.getenv("USER_MAP_FILE_PATH")

logger = logging.getLogger() 


def configure_logging():
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('faster_whisper').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

   
    log_directory = '.logs/transcripts'
    pdf_directory = '.logs/pdfs'
    os.makedirs(log_directory, exist_ok=True) 
    os.makedirs(pdf_directory, exist_ok=True)  

   
    current_date = datetime.now().strftime('%Y-%m-%d')
    log_filename = os.path.join(log_directory, f"{current_date}-transcription.log")

   
    log_format = '%(asctime)s %(name)s: %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S.%f'[:-3] 

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
    
   
    transcription_logger = logging.getLogger('transcription')
    transcription_logger.setLevel(logging.INFO)

   
    file_handler = logging.FileHandler(log_filename, mode='a')
    file_handler.setLevel(logging.INFO)
    
   
    file_handler.setFormatter(logging.Formatter(
        '%(message)s' 
    ))

   
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
           
            if after.channel is None:
                guild_id = before.channel.guild.id
                helper = bot.guild_to_helper.get(guild_id, None)
                if helper:
                    helper.set_vc(None)
                    bot.guild_to_helper.pop(guild_id, None)

                bot._close_and_clean_sink_for_guild(guild_id)

    @bot.slash_command(name="connect", description="Add the bot to your voice channel.")
    async def connect(ctx: discord.ApplicationContext):
        if bot._is_ready is False:
            await ctx.respond("Bot is not ready. Please try again shortly.", ephemeral=True)
            return
        author_vc = ctx.author.voice
        if not author_vc:
            await ctx.respond("You are not in a voice channel.", ephemeral=True)
            return
        if bot.guild_to_helper.get(ctx.guild.id, None):
            await ctx.respond("The bot is already in a voice channel on this server.", ephemeral=True)
            return
        await ctx.defer()
        try:
            guild_id = ctx.guild.id
            vc = await author_vc.channel.connect()
            helper = bot.guild_to_helper.get(guild_id, BotHelper(bot))
            helper.guild_id = guild_id
            helper.set_vc(vc)
            bot.guild_to_helper[guild_id] = helper
            await ctx.followup.send(f"Successfully connected to the voice channel!", ephemeral=False)
            await ctx.guild.change_voice_state(channel=author_vc.channel, self_mute=True)
        except Exception as e:
            await ctx.followup.send(f"{e}", ephemeral=True)

    @bot.slash_command(name="record", description="Start recording the conference.")
    async def ink(ctx: discord.ApplicationContext):
        await ctx.defer()
        connect_text = "`/connect`"
        if not bot.guild_to_helper.get(ctx.guild.id, None):
            await ctx.followup.send(f"The bot is not connected to a voice channel. Please use {connect_text} to connect.", ephemeral=True)
            return
        if bot.guild_is_recording.get(ctx.guild.id, False):
            await ctx.followup.send("Recording is already in progress.", ephemeral=True)
            return
        bot.start_recording(ctx)
        await ctx.followup.send("Recording started!", ephemeral=False)

    @bot.slash_command(name="stop", description="Stop the recording.")
    async def stop(ctx: discord.ApplicationContext):
        guild_id = ctx.guild.id
        helper = bot.guild_to_helper.get(guild_id, None)
        if not helper:
            await ctx.respond("The bot is not in a voice channel.", ephemeral=True)
            return

        bot_vc = helper.vc
        
        if not bot_vc:
            await ctx.respond("The bot is not in a voice channel.", ephemeral=True)
            return

        if not bot.guild_is_recording.get(guild_id, False):
            await ctx.respond("No recording is currently in progress.", ephemeral=True)
            return

        await ctx.defer()
        
        if bot.guild_is_recording.get(guild_id, False):
            await bot.get_transcription(ctx)
            bot.stop_recording(ctx)
            bot.guild_is_recording[guild_id] = False
            await ctx.followup.send("Recording stopped.", ephemeral=False)
            bot.cleanup_sink(ctx)
        
    @bot.slash_command(name="disconnect", description="Disconnect the bot from the voice channel.")
    async def disconnect(ctx: discord.ApplicationContext):
        guild_id = ctx.guild.id
        id_exists = bot.guild_to_helper.get(guild_id, None)
        if not id_exists:
            await ctx.respond("The bot is not in a voice channel.", ephemeral=True)
            return
        
        helper = bot.guild_to_helper[guild_id]    
        bot_vc = helper.vc
        
        if not bot_vc:
            await ctx.respond("The bot is not in a voice channel.", ephemeral=True)
            return
        
        await ctx.defer()
        await bot_vc.disconnect()
        helper.guild_id = None
        helper.set_vc(None)
        bot.guild_to_helper.pop(guild_id, None)

        await ctx.respond("Successfully disconnected from the voice channel.", ephemeral=False)

    @bot.slash_command(name="generate_txt", description="Generate a TXT of the transcriptions.")
    async def generate_txt(ctx: discord.ApplicationContext):
        guild_id = ctx.guild.id
        helper = bot.guild_to_helper.get(guild_id, None)
        if not helper:
            await ctx.respond("The bot is not in a voice channel.", ephemeral=True)
            return
        transcription = await bot.get_transcription(ctx)
        if not transcription:
            await ctx.respond("There are no transcriptions to generate a TXT from.", ephemeral=True)
            return
        txt_file_path = await txt_generator(transcription)
        if os.path.exists(txt_file_path):
            try:
                with open(txt_file_path, "rb") as f:
                    discord_file = discord.File(f, filename=f"conference_transcription.txt")
                    await ctx.respond("Here is the transcription from this session:", file=discord_file)
            finally:
                os.remove(txt_file_path)
        else:
            await ctx.respond("No transcription file could be generated.", ephemeral=True)


    @bot.slash_command(name="update_user_map", description="Updates the user_map. Writes to file if USER_MAP_FILE_PATH is set.")
    async def update_user_map(ctx: discord.ApplicationContext):
        if bot.guild_is_recording.get(ctx.guild.id, False):
            await ctx.respond("Cannot update the user map while recording is in progress.", ephemeral=True)
            return
        try:
            await bot.update_user_map(ctx)
            await ctx.respond("User map has been updated.", ephemeral=True)
        except Exception as e:
            await ctx.respond(f"Unable to update user_map.yml.:\n{e}", ephemeral=True)
            raise e


    @bot.slash_command(name="help", description="Show the help message.")
    async def help(ctx: discord.ApplicationContext):
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
                name="/generate_txt", value="Generate a TXT of the transcriptions.", inline=True),
            discord.EmbedField(
                name="/update_user_map", value="Update the user map.", inline=True),
            discord.EmbedField(
                name="/help", value="Show this help message.", inline=True),
        ]

        embed = discord.Embed(title="Bot Help",
                              description="Commands for the transcription bot.",
                              color=discord.Color.blue(),
                              fields=embed_fields)

        await ctx.respond(embed=embed, ephemeral=True)





    try:
        bot.run(DISCORD_BOT_TOKEN)
    except KeyboardInterrupt:
        logger.info("^C received, shutting down...")
        asyncio.run(bot.stop_and_cleanup())
    finally:
        asyncio.run(bot.close())
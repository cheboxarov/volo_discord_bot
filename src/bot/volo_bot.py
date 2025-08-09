import asyncio
import json
import logging
import os
from collections import defaultdict

import discord
import yaml

from src.sinks.whisper_sink import WhisperSink

DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
TRANSCRIPTION_METHOD = os.getenv("TRANSCRIPTION_METHOD")
USER_MAP_FILE_PATH = os.getenv("USER_MAP_FILE_PATH")


logger = logging.getLogger(__name__)

class VoloBot(discord.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.voice_states = True

        super().__init__(
            activity=discord.CustomActivity(name='Transcribing Audio to Text'),
            intents=intents
        )
        self.guild_to_helper = {}
        self.guild_is_recording = {}
        self.guild_whisper_sinks = {}
        self.guild_whisper_message_tasks = {}
        self.user_map = {}
        self._is_ready = False
        if TRANSCRIPTION_METHOD == "openai":
            self.transcriber_type = "openai"
        else:
            self.transcriber_type = "local"
        if USER_MAP_FILE_PATH:
            with open(USER_MAP_FILE_PATH, "r", encoding="utf-8") as file:
                self.user_map = yaml.safe_load(file)

    

    async def on_ready(self):
        try:
            await self.sync_commands()
        except Exception as e:
            logger.warning(f"Could not sync slash commands: {e}")
        logger.info(f"Logged in as {self.user} to Discord.")
        self._is_ready = True


    

    def _close_and_clean_sink_for_guild(self, guild_id: int):
        whisper_sink: WhisperSink | None = self.guild_whisper_sinks.get(
            guild_id, None)

        if whisper_sink:
            logger.debug(f"Stopping whisper sink, requested by {guild_id}.")
            whisper_sink.stop_voice_thread()
            del self.guild_whisper_sinks[guild_id]
            whisper_sink.close()

    
    def start_recording(self, ctx):
        """
        Start recording audio from the voice channel. Create a whisper sink
        and start sending transcripts to the queue.

        Since this is a critical function, this is where we should handle
        subscription checks and limits.
        """
        try:
            self.start_whisper_sink(ctx)
            self.guild_is_recording[ctx.guild.id] = True
        except Exception as e:
            logger.error(f"Error starting whisper sink: {e}")

    async def retry_recording(self, ctx):
        await asyncio.sleep(5)
        self.start_recording(ctx)

    def start_whisper_sink(self, ctx):
        guild_voice_sink = self.guild_whisper_sinks.get(ctx.guild.id, None)
        if guild_voice_sink:
            logger.debug(
                f"Sink is already active for guild {ctx.guild.id}."
            )
            return

        def after_recording(sink: WhisperSink, *args):
            try:
                logger.debug(f"Sink for guild {sink.vc.guild.id} stopped cleanly.")
            except Exception:
                pass
            self._close_and_clean_sink_for_guild(sink.vc.guild.id)

        transcript_queue = asyncio.Queue()

        whisper_sink = WhisperSink(
            transcript_queue,
            transcriber_type=self.transcriber_type,
            user_map=self.user_map,
        )

        self.guild_to_helper[ctx.guild.id].vc.start_recording(whisper_sink, after_recording)

        def on_thread_exception(e):
            logger.warning(
                f"Whisper sink thread exception for guild {ctx.guild.id}. Retry in 5 seconds...\n{e}")
            self._close_and_clean_sink_for_guild(ctx.guild.id)
            asyncio.run_coroutine_threadsafe(self.retry_recording(ctx), self.loop)

        whisper_sink.start_voice_thread(on_exception=on_thread_exception)
        self.guild_whisper_sinks[ctx.guild.id] = whisper_sink

    def stop_recording(self, ctx):
        vc = ctx.guild.voice_client
        if vc and vc.is_recording():
            self.guild_is_recording[ctx.guild.id] = False
            vc.stop_recording()
        guild_id = ctx.guild.id
        whisper_message_task = self.guild_whisper_message_tasks.get(
            guild_id, None)
        if whisper_message_task:
            logger.debug("Cancelling whisper message task.")
            whisper_message_task.cancel()
            del self.guild_whisper_message_tasks[guild_id]

    def cleanup_sink(self, ctx):
        guild_id = ctx.guild.id
        self._close_and_clean_sink_for_guild(guild_id)

    async def get_transcription(self, ctx):
       
        if not (self.guild_whisper_sinks.get(ctx.guild.id)):
            return
        whisper_sink = self.guild_whisper_sinks[ctx.guild.id]
        transcriptions = []
        if whisper_sink is None:
            return
    
        transcriptions_queue = whisper_sink.transcription_output_queue
        while not transcriptions_queue.empty():
            transcriptions.append(await transcriptions_queue.get())
        return transcriptions

    async def update_user_map(self, ctx):
        user_map = {}
        for member in ctx.guild.members:
            user_map[member.id] = {
                "userName": member.name,
                "displayName": member.display_name
            }
        logger.info(f"{str(user_map)}")
        self.user_map.update(user_map)
        if USER_MAP_FILE_PATH:
            with open(USER_MAP_FILE_PATH, "w", encoding="utf-8") as file:
                yaml.dump(self.user_map, file, default_flow_style=False, allow_unicode=True)

    async def stop_and_cleanup(self):
        try:
            for sink in self.guild_whisper_sinks.values():
                sink.close()
                sink.stop_voice_thread()
                logger.debug(
                    f"Stopped whisper sink for guild {sink.vc.channel.guild.id} in cleanup.")
            self.guild_whisper_sinks.clear()
        except Exception as e:
            logger.error(f"Error stopping whisper sinks: {e}")
        finally:
            logger.info("Cleanup completed.")
    
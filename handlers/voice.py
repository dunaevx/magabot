import asyncio
import os
import tempfile
import subprocess
import warnings
import shutil  # <-- важно

# Скрываем предупреждения pydub
warnings.filterwarnings('ignore', category=RuntimeWarning, module='pydub.utils')

# Автоматическое определение ffmpeg / ffprobe
# Windows / Linux / Mac — работают одинаково.
ffmpeg_path = shutil.which("ffmpeg")
ffprobe_path = shutil.which("ffprobe")

print(f"FFmpeg found: {ffmpeg_path}")
print(f"FFprobe found: {ffprobe_path}")

if not ffmpeg_path:
    raise RuntimeError(
        "FFmpeg не найден. Установите:\n"
        "Linux: sudo apt install ffmpeg\n"
        "Windows: choco install ffmpeg"
    )

# Импортируем pydub после того, как пути определены
from pydub import AudioSegment
from pydub.effects import normalize

# Устанавливаем пути для pydub, если они определены
AudioSegment.ffmpeg = ffmpeg_path
AudioSegment.ffprobe = ffprobe_path
AudioSegment.converter = ffmpeg_path

from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

router = Router()


class VoiceStates(StatesGroup):
    waiting_for_file = State()


@router.message(F.text == "/send_voice")
@router.message(F.text == "Голосовуха")
async def cmd_send_voice(message: Message, state: FSMContext):
    await message.reply(
        "Отправьте voice/mp3/wav или видео. Я конвертирую в voice OGG."
    )
    await state.set_state(VoiceStates.waiting_for_file)


@router.message(VoiceStates.waiting_for_file, F.voice)
async def process_voice(message: Message, state: FSMContext):
    await _convert_and_send(message, state, is_video=False)


@router.message(VoiceStates.waiting_for_file, F.audio)
async def process_audio(message: Message, state: FSMContext):
    await _convert_and_send(message, state, is_video=False)


@router.message(VoiceStates.waiting_for_file, F.video)
async def process_video(message: Message, state: FSMContext):
    await _convert_and_send(message, state, is_video=True)


@router.message(VoiceStates.waiting_for_file)
async def invalid_file(message: Message, state: FSMContext):
    await message.reply("Это не voice/audio/video. Отправьте нужный файл.")


async def _convert_and_send(message: Message, state: FSMContext, is_video: bool):
    try:
        file_id = None
        audio_format = None
        temp_dir = tempfile.gettempdir()

        if message.voice:
            file_id = message.voice.file_id
            ext = "ogg"
            audio_format = "ogg"
            file_name = f"{file_id[:8]}.{ext}"

        elif message.audio:
            file_id = message.audio.file_id
            mime = message.audio.mime_type or "audio/mpeg"
            ext = mime.split("/")[-1]
            audio_format = ext
            file_name = f"{file_id[:8]}.{ext}"

        elif message.video:
            file_id = message.video.file_id
            ext = "mp4"
            audio_format = "mp4"
            file_name = f"{file_id[:8]}.{ext}"

        else:
            await message.reply("Отправьте voice/audio/video.")
            return

        file_path = os.path.join(temp_dir, file_name)
        file = await message.bot.get_file(file_id)
        await message.bot.download_file(file.file_path, file_path)

        # Проверяем скачивание
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            raise RuntimeError("Ошибка скачивания файла.")

        # Конвертация через pydub (основной путь)
        try:
            if is_video:
                audio = AudioSegment.from_file(file_path, format="mp4")
            else:
                audio = AudioSegment.from_file(file_path, format=audio_format)

            audio = normalize(audio)

            if len(audio) > 60000:
                audio = audio[:60000]

            ogg_name = file_name.rsplit(".", 1)[0] + ".ogg"
            ogg_path = os.path.join(temp_dir, ogg_name)

            audio.export(ogg_path, format="ogg", codec="libopus", bitrate="64k")
            duration = len(audio) // 1000

        except Exception as pydub_error:
            print(f"Pydub ошибка: {pydub_error}. Используем ffmpeg напрямую.")

            ogg_name = file_name.rsplit(".", 1)[0] + ".ogg"
            ogg_path = os.path.join(temp_dir, ogg_name)

            cmd = [
                ffmpeg_path,
                "-i", file_path,
                "-c:a", "libopus",
                "-b:a", "64k",
                "-t", "60",
                ogg_path,
                "-y"
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg error: {result.stderr}")

            duration = 60  # fallback

        # Проверяем итог
        if not os.path.exists(ogg_path):
            raise RuntimeError("Конвертация провалилась.")

        await message.reply_voice(
            voice=FSInputFile(ogg_path),
            duration=duration
        )

        # Удаляем временные файлы
        os.remove(file_path)
        os.remove(ogg_path)

        await message.reply("Готово.")

    except Exception as e:
        print(f"Ошибка обработки: {e}")
        await message.reply("Произошла ошибка при конвертации.")

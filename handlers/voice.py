import asyncio
import os
import tempfile
import subprocess  # Для лога и fallback ffmpeg
import warnings  # Для suppress

# Фикс для Windows: PATH глобально ДО import pydub
ffmpeg_bin = r'C:\ProgramData\chocolatey\bin'
if ffmpeg_bin not in os.environ['PATH']:
    os.environ['PATH'] = os.environ['PATH'] + os.pathsep + ffmpeg_bin
    print(f"Global PATH updated for ffmpeg: {ffmpeg_bin}")

# Suppress pydub warnings
warnings.filterwarnings('ignore', category=RuntimeWarning, module='pydub.utils')

# Лог: чек ffmpeg/ffprobe
ffmpeg_path = os.path.join(ffmpeg_bin, 'ffmpeg.exe')
ffprobe_path = os.path.join(ffmpeg_bin, 'ffprobe.exe')
print(f"FFmpeg path: {ffmpeg_path}, exists: {os.path.exists(ffmpeg_path)}, access: {os.access(ffmpeg_path, os.X_OK) if os.path.exists(ffmpeg_path) else 'N/A'}")
print(f"FFprobe path: {ffprobe_path}, exists: {os.path.exists(ffprobe_path)}, access: {os.access(ffprobe_path, os.X_OK) if os.path.exists(ffprobe_path) else 'N/A'}")
subprocess.run([ffmpeg_path, '-version'], capture_output=True, check=False)  # Тихо

# Теперь import pydub — пути уже в теме
from pydub import AudioSegment
from pydub.effects import normalize
from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Устанавливаем пути для pydub
AudioSegment.ffmpeg = ffmpeg_path
AudioSegment.ffprobe = ffprobe_path
AudioSegment.converter = ffmpeg_path

router = Router()

class VoiceStates(StatesGroup):
    waiting_for_file = State()  # Состояние: ждём файл после /send_voice

@router.message(F.text == "/send_voice")
@router.message(F.text == "Голосовуха")  # Кнопка триггерит
async def cmd_send_voice(message: Message, state: FSMContext):
    """Команда: ждём аудио/видео для конвертации в voice OGG."""
    await message.reply("Кидай voice, mp3, wav или видео (mp4/mov). Конвертану в мою голосовуху, братан. Не тяни.")
    await state.set_state(VoiceStates.waiting_for_file)

@router.message(VoiceStates.waiting_for_file, F.voice)
async def process_voice(message: Message, state: FSMContext):
    """Конверт voice (OGG уже, но нормализуем/обрезаем)."""
    await _convert_and_send(message, state, is_video=False)

@router.message(VoiceStates.waiting_for_file, F.audio)
async def process_audio(message: Message, state: FSMContext):
    """Конверт audio (mp3/wav в OGG Opus)."""
    await _convert_and_send(message, state, is_video=False)

@router.message(VoiceStates.waiting_for_file, F.video)
async def process_video(message: Message, state: FSMContext):
    """Из видео режем аудио и конверт в OGG Opus."""
    await _convert_and_send(message, state, is_video=True)

@router.message(VoiceStates.waiting_for_file)
async def invalid_file(message: Message, state: FSMContext):
    """Неподходящий файл — отшиваем."""
    await message.reply("Эй, это не voice/audio/video. Перекинь нормальный файл, не дури.")
    # Не сбрасываем state — ждём правильный

async def _convert_and_send(message: Message, state: FSMContext, is_video: bool):
    """Внутренняя: скачивает, конвертит, шлёт voice OGG."""
    try:
        file_id = None
        audio_format = None
        temp_dir = tempfile.gettempdir()  # Кросс-платформен temp
        
        if message.voice:
            file_id = message.voice.file_id
            ext = 'ogg'
            audio_format = 'ogg'
            file_name = f"{file_id[:8]}.{ext}"  # Короткий name
        elif message.audio:
            file_id = message.audio.file_id
            mime = message.audio.mime_type or 'audio/mpeg'
            ext = mime.split('/')[-1] if '/' in mime else 'mp3'
            audio_format = ext
            file_name = f"{file_id[:8]}.{ext}"
        elif message.video:
            file_id = message.video.file_id
            ext = 'mp4'
            audio_format = 'mp4'
            file_name = f"{file_id[:8]}.{ext}"
        else:
            await message.reply("Не файл, братан. Только voice/audio/video.")
            return
        
        if not file_id:
            await message.reply("File ID фигня. Перекинь заново.")
            return
        
        # Скачиваем в temp
        file_path = os.path.join(temp_dir, file_name)
        file = await message.bot.get_file(file_id)
        await message.bot.download_file(file.file_path, file_path)
        
        # Чек: файл скачался? Размер >0
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Скачал, но файла нет: {file_path}")
        if os.path.getsize(file_path) == 0:
            raise ValueError(f"Файл пустой: {file_path}. Telegram фигня?")
        
        # Конвертация: try pydub, fallback на subprocess
        try:
            if is_video:
                audio = AudioSegment.from_file(file_path, format="mp4")
            else:
                audio = AudioSegment.from_file(file_path, format=audio_format)
            # Нормализуем, обрезаем
            audio = normalize(audio)
            if len(audio) > 60000:
                audio = audio[:60000]
            # Export OGG
            ogg_name = file_name.rsplit('.', 1)[0] + '.ogg'
            ogg_path = os.path.join(temp_dir, ogg_name)
            audio.export(ogg_path, format="ogg", codec="libopus", bitrate="64k")
            duration = len(audio) // 1000  # Точная duration
        except Exception as pydub_err:
            print(f"Pydub failed: {pydub_err}. Fallback to subprocess.")
            # Fallback: прямой ffmpeg
            ogg_name = file_name.rsplit('.', 1)[0] + '.ogg'
            ogg_path = os.path.join(temp_dir, ogg_name)
            cmd = [ffmpeg_path, '-i', file_path, '-c:a', 'libopus', '-b:a', '64k', '-t', '60', ogg_path, '-y']
            print(f"FFmpeg command: {' '.join(cmd)}")  # Лог команды
            result = subprocess.run(cmd, capture_output=True, check=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg failed: returncode {result.returncode}, stderr: {result.stderr}")
            # Duration через ffprobe
            probe_cmd = [ffprobe_path, '-v', 'quiet', '-print_format', 'json', '-show_format', ogg_path]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
            if probe_result.returncode == 0:
                import json
                probe_data = json.loads(probe_result.stdout)
                duration = int(float(probe_data['format']['duration']))
            else:
                duration = 60  # Fallback 60 сек
        
        # Чек OGG создался
        if not os.path.exists(ogg_path):
            raise FileNotFoundError(f"OGG не создался: {ogg_path}")
        if os.path.getsize(ogg_path) == 0:
            raise ValueError(f"OGG пустой: {ogg_path}. Конверт фейлил?")
        
        # Шлём как voice
        await message.reply_voice(
            voice=FSInputFile(path=ogg_path),
            duration=duration
        )
        
        # Уборка
        for path in [file_path, ogg_path]:
            if os.path.exists(path):
                os.remove(path)
        
        await message.reply("Готово, братан!")
        
    except Exception as e:
        print(f"Voice convert error: {e}")
        await message.reply("Фигня с файлом: не конвертится. Перекинь другой, или чекни формат. Не ной.")
    import asyncio
import os
import tempfile
import subprocess  # Для лога и fallback ffmpeg
import warnings  # Для suppress

# Фикс для Windows: PATH глобально ДО import pydub
ffmpeg_bin = r'C:\ProgramData\chocolatey\bin'
if ffmpeg_bin not in os.environ['PATH']:
    os.environ['PATH'] = os.environ['PATH'] + os.pathsep + ffmpeg_bin
    print(f"Global PATH updated for ffmpeg: {ffmpeg_bin}")

# Suppress pydub warnings
warnings.filterwarnings('ignore', category=RuntimeWarning, module='pydub.utils')

# Лог: чек ffmpeg/ffprobe
ffmpeg_path = os.path.join(ffmpeg_bin, 'ffmpeg.exe')
ffprobe_path = os.path.join(ffmpeg_bin, 'ffprobe.exe')
print(f"FFmpeg path: {ffmpeg_path}, exists: {os.path.exists(ffmpeg_path)}, access: {os.access(ffmpeg_path, os.X_OK) if os.path.exists(ffmpeg_path) else 'N/A'}")
print(f"FFprobe path: {ffprobe_path}, exists: {os.path.exists(ffprobe_path)}, access: {os.access(ffprobe_path, os.X_OK) if os.path.exists(ffprobe_path) else 'N/A'}")
subprocess.run([ffmpeg_path, '-version'], capture_output=True, check=False)  # Тихо

# Теперь import pydub — пути уже в теме
from pydub import AudioSegment
from pydub.effects import normalize
from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Устанавливаем пути для pydub
AudioSegment.ffmpeg = ffmpeg_path
AudioSegment.ffprobe = ffprobe_path
AudioSegment.converter = ffmpeg_path

router = Router()

class VoiceStates(StatesGroup):
    waiting_for_file = State()  # Состояние: ждём файл после /send_voice

@router.message(F.text == "/send_voice")
@router.message(F.text == "Голосовуха")  # Кнопка триггерит
async def cmd_send_voice(message: Message, state: FSMContext):
    """Команда: ждём аудио/видео для конвертации в voice OGG."""
    await message.reply("Кидай voice, mp3, wav или видео (mp4/mov). Конвертану в мою голосовуху, братан. Не тяни.")
    await state.set_state(VoiceStates.waiting_for_file)

@router.message(VoiceStates.waiting_for_file, F.voice)
async def process_voice(message: Message, state: FSMContext):
    """Конверт voice (OGG уже, но нормализуем/обрезаем)."""
    await _convert_and_send(message, state, is_video=False)

@router.message(VoiceStates.waiting_for_file, F.audio)
async def process_audio(message: Message, state: FSMContext):
    """Конверт audio (mp3/wav в OGG Opus)."""
    await _convert_and_send(message, state, is_video=False)

@router.message(VoiceStates.waiting_for_file, F.video)
async def process_video(message: Message, state: FSMContext):
    """Из видео режем аудио и конверт в OGG Opus."""
    await _convert_and_send(message, state, is_video=True)

@router.message(VoiceStates.waiting_for_file)
async def invalid_file(message: Message, state: FSMContext):
    """Неподходящий файл — отшиваем."""
    await message.reply("Эй, это не voice/audio/video. Перекинь нормальный файл, не дури.")
    # Не сбрасываем state — ждём правильный

async def _convert_and_send(message: Message, state: FSMContext, is_video: bool):
    """Внутренняя: скачивает, конвертит, шлёт voice OGG."""
    try:
        file_id = None
        audio_format = None
        temp_dir = tempfile.gettempdir()  # Кросс-платформен temp
        
        if message.voice:
            file_id = message.voice.file_id
            ext = 'ogg'
            audio_format = 'ogg'
            file_name = f"{file_id[:8]}.{ext}"  # Короткий name
        elif message.audio:
            file_id = message.audio.file_id
            mime = message.audio.mime_type or 'audio/mpeg'
            ext = mime.split('/')[-1] if '/' in mime else 'mp3'
            audio_format = ext
            file_name = f"{file_id[:8]}.{ext}"
        elif message.video:
            file_id = message.video.file_id
            ext = 'mp4'
            audio_format = 'mp4'
            file_name = f"{file_id[:8]}.{ext}"
        else:
            await message.reply("Не файл, братан. Только voice/audio/video.")
            return
        
        if not file_id:
            await message.reply("File ID фигня. Перекинь заново.")
            return
        
        # Скачиваем в temp
        file_path = os.path.join(temp_dir, file_name)
        file = await message.bot.get_file(file_id)
        await message.bot.download_file(file.file_path, file_path)
        
        # Чек: файл скачался? Размер >0
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Скачал, но файла нет: {file_path}")
        if os.path.getsize(file_path) == 0:
            raise ValueError(f"Файл пустой: {file_path}. Telegram фигня?")
        
        # Конвертация: try pydub, fallback на subprocess
        try:
            if is_video:
                audio = AudioSegment.from_file(file_path, format="mp4")
            else:
                audio = AudioSegment.from_file(file_path, format=audio_format)
            # Нормализуем, обрезаем
            audio = normalize(audio)
            if len(audio) > 60000:
                audio = audio[:60000]
            # Export OGG
            ogg_name = file_name.rsplit('.', 1)[0] + '.ogg'
            ogg_path = os.path.join(temp_dir, ogg_name)
            audio.export(ogg_path, format="ogg", codec="libopus", bitrate="64k")
            duration = len(audio) // 1000  # Точная duration
        except Exception as pydub_err:
            print(f"Pydub failed: {pydub_err}. Fallback to subprocess.")
            # Fallback: прямой ffmpeg
            ogg_name = file_name.rsplit('.', 1)[0] + '.ogg'
            ogg_path = os.path.join(temp_dir, ogg_name)
            cmd = [ffmpeg_path, '-i', file_path, '-c:a', 'libopus', '-b:a', '64k', '-t', '60', ogg_path, '-y']
            print(f"FFmpeg command: {' '.join(cmd)}")  # Лог команды
            result = subprocess.run(cmd, capture_output=True, check=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg failed: returncode {result.returncode}, stderr: {result.stderr}")
            # Duration через ffprobe
            probe_cmd = [ffprobe_path, '-v', 'quiet', '-print_format', 'json', '-show_format', ogg_path]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
            if probe_result.returncode == 0:
                import json
                probe_data = json.loads(probe_result.stdout)
                duration = int(float(probe_data['format']['duration']))
            else:
                duration = 60  # Fallback 60 сек
        
        # Чек OGG создался
        if not os.path.exists(ogg_path):
            raise FileNotFoundError(f"OGG не создался: {ogg_path}")
        if os.path.getsize(ogg_path) == 0:
            raise ValueError(f"OGG пустой: {ogg_path}. Конверт фейлил?")
        
        # Шлём как voice
        await message.reply_voice(
            voice=FSInputFile(path=ogg_path),
            duration=duration
        )
        
        # Уборка
        for path in [file_path, ogg_path]:
            if os.path.exists(path):
                os.remove(path)
        
        await message.reply("Готово, братан! Твоя голосовуха в OGG — чистая, как слеза. Ещё?")
        
    except Exception as e:
        print(f"Voice convert error: {e}")
        await message.reply("Фигня с файлом: не конвертится. Перекинь другой, или чекни формат. Не ной.")
    
    # Сброс state
    await state.clear()
    # Сброс state
    await state.clear()
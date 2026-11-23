import asyncio
import os
import re
import shutil
import tempfile
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message
from PIL import Image
import yt_dlp

BOT_TOKEN = os.getenv("9130391413:AAGkuQygUe0s2Fn9u56und04gb9DRzIVjUo")
MAX_CONCURRENT_TASKS = 3
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

URL_PATTERN = re.compile(
    r"https?://(?:www\.|vm\.|vt\.|m\.)?(?:tiktok\.com|instagram\.com|pinterest\.com|pin\.it)/\S+"
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)


def download_media_sync(url: str, tmp_dir: str) -> str | None:
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": os.path.join(tmp_dir, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    if os.path.exists("cookies.txt"):
        ydl_opts["cookiefile"] = "cookies.txt"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    files = [
        os.path.join(tmp_dir, f)
        for f in os.listdir(tmp_dir)
        if os.path.isfile(os.path.join(tmp_dir, f))
    ]
    return files[0] if files else None


def compress_image_sync(in_path: str, out_path: str) -> None:
    with Image.open(in_path) as img:
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(out_path, "JPEG", quality=60, optimize=True)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Отправь ссылку (TikTok, Instagram, Pinterest), видео для конвертации в MP3 или фото для сжатия."
    )


@dp.message(F.text.regexp(URL_PATTERN))
async def handle_link(message: Message) -> None:
    match = URL_PATTERN.search(message.text or "")
    if not match:
        return
    url = match.group(0)
    status = await message.answer("В очереди...")

    async with semaphore:
        await status.edit_text("Скачиваю...")
        tmp_dir = tempfile.mkdtemp()
        try:
            file_path = await asyncio.to_thread(download_media_sync, url, tmp_dir)
            if not file_path or not os.path.exists(file_path):
                await status.edit_text("Не удалось скачать медиа.")
                return

            if os.path.getsize(file_path) > MAX_UPLOAD_BYTES:
                await status.edit_text("Файл больше 50 МБ — лимит Telegram Bot API.")
                return

            ext = os.path.splitext(file_path)[1].lower()
            media = FSInputFile(file_path)
            if ext in (".jpg", ".jpeg", ".png", ".webp"):
                await message.answer_photo(media)
            else:
                await message.answer_video(media)
            await status.delete()
        except Exception as e:
            await status.edit_text(f"Ошибка загрузки: {e}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@dp.message(F.video)
async def handle_video_to_mp3(message: Message) -> None:
    video = message.video
    if not video:
        return
    if video.file_size and video.file_size > MAX_DOWNLOAD_BYTES:
        await message.answer("Видео больше 20 МБ — лимит скачивания Telegram Bot API.")
        return

    status = await message.answer("В очереди...")
    async with semaphore:
        await status.edit_text("Извлекаю MP3...")
        tmp_dir = tempfile.mkdtemp()
        try:
            in_path = os.path.join(tmp_dir, "input.mp4")
            out_path = os.path.join(tmp_dir, "audio.mp3")
            await bot.download(video, destination=in_path)

            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-i",
                in_path,
                "-vn",
                "-acodec",
                "libmp3lame",
                "-q:a",
                "2",
                out_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()

            if not os.path.exists(out_path):
                await status.edit_text("Ошибка конвертации FFmpeg.")
                return

            await message.answer_audio(FSInputFile(out_path))
            await status.delete()
        except Exception as e:
            await status.edit_text(f"Ошибка: {e}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@dp.message(F.photo)
async def handle_photo_compress(message: Message) -> None:
    if not message.photo:
        return
    photo = message.photo[-1]
    status = await message.answer("Сжимаю...")

    async with semaphore:
        tmp_dir = tempfile.mkdtemp()
        try:
            in_path = os.path.join(tmp_dir, "in.jpg")
            out_path = os.path.join(tmp_dir, "compressed.jpg")
            await bot.download(photo, destination=in_path)

            before = os.path.getsize(in_path)
            await asyncio.to_thread(compress_image_sync, in_path, out_path)
            after = os.path.getsize(out_path)

            await message.answer_document(
                FSInputFile(out_path),
                caption=f"{before // 1024} КБ → {after // 1024} КБ",
            )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"Ошибка сжатия: {e}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def main() -> None:
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import logging
import re
from typing import AsyncGenerator, Optional, Tuple
from fastapi import HTTPException
from app.config import settings

logger = logging.getLogger(__name__)


class StreamingService:
    @staticmethod
    def parse_range_header(
        range_header: Optional[str],
        file_size: int,
    ) -> Tuple[int, int, int, int]:
        if not range_header or not range_header.startswith("bytes="):
            return 200, 0, max(0, file_size - 1), file_size

        try:
            match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", range_header.strip())
            if not match or not any(match.groups()):
                raise ValueError
            start_str, end_str = match.groups()

            if start_str and end_str:
                start = int(start_str)
                end = int(end_str)
            elif start_str:
                start = int(start_str)
                end = file_size - 1
            else:
                end = file_size - 1
                start = max(0, file_size - int(end_str))

            if start >= file_size or start > end:
                raise ValueError

            end = min(end, file_size - 1)
            content_length = end - start + 1
            return 206, start, end, content_length

        except ValueError:
            raise HTTPException(
                status_code=416,
                detail="Requested Range Not Satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            ) from None

    @classmethod
    async def create_file_stream_generator(
        cls,
        file_path: str,
        start_byte: int,
        content_length: int,
        chunk_size: int = settings.STREAM_CHUNK_SIZE,
    ) -> AsyncGenerator[bytes, None]:
        bytes_remaining = content_length
        try:
            with open(file_path, "rb") as f:
                f.seek(start_byte)
                while bytes_remaining > 0:
                    to_read = min(chunk_size, bytes_remaining)
                    chunk = await asyncio.to_thread(f.read, to_read)
                    if not chunk:
                        break
                    yield chunk
                    bytes_remaining -= len(chunk)
        except (asyncio.CancelledError, GeneratorExit):
            return


streaming_service = StreamingService()

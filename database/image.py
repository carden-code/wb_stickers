from sqlalchemy import select

from database.setup import ImageFile, get_session


async def save_image_file_id(tag, file_id):
    async with get_session() as session:
        video_file = ImageFile(tag=tag, file_id=file_id)
        session.add(video_file)


async def get_image_file_id(tag):
    async with get_session() as session:
        query = select(ImageFile).where(ImageFile.tag == tag)
        result = await session.execute(query)
        video_file = result.scalars().first()
        if video_file:
            return video_file.file_id
        return None
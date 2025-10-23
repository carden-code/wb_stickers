from contextlib import asynccontextmanager

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, func, Boolean
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL

engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    pool_size=100,  # Увеличьте размер пула соединений
    max_overflow=200,  # Увеличьте максимальное количество дополнительных соединений
    pool_timeout=120,  # Увеличьте тайм-аут ожидания свободного соединения в пуле
    pool_recycle=1800  # Время в секундах для рециклирования соединений
)

Base = declarative_base()


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String)


class AccessKey(Base):
    __tablename__ = 'access_keys'

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)  # Уникальный ключ
    used = Column(Boolean, default=False)  # Флаг использования
    created_at = Column(DateTime(timezone=True), server_default=func.now())  # Время создания
    

# Модель для хранения информации о файлах изображений
class ImageFile(Base):
    __tablename__ = "image_files"

    id = Column(Integer, primary_key=True)
    tag = Column(String, unique=True, index=True)  # Уникальный тег и индекс
    file_id = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)  # Индекс для created_at


# Создайте асинхронный сеанс
async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@asynccontextmanager
async def get_session():
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise e
        finally:
            await session.close()

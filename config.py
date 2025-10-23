import os

from dotenv import load_dotenv

load_dotenv()

ADMINS = [306083015, 669479300]

TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

IMAGE_NAME = 'stickers.webp'
MAX_ARTICLE_LENGTH = 50  # Максимальная длина артикула


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Settings:
    BIOPORTAL_API_KEY = os.getenv("BIOPORTAL_API_KEY")
    NEO4J_URI = os.getenv("NEO4J_URI")
    NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

settings = Settings()
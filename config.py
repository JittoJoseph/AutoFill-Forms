import os

from dotenv import load_dotenv


def load_env() -> None:
	"""Load environment variables from .env."""
	load_dotenv()


def get_webhook_url() -> str:
	return os.getenv("DISCORD_WEBHOOK_URL", "").strip()
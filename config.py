import os
from dotenv import load_dotenv

load_dotenv()


def get_env_int(key: str, default: int = 0) -> int:
    """Safely retrieves integer IDs from environment variables."""
    val = os.getenv(key, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


# =====================================================================
# Discord Authentication & Database Configuration
# =====================================================================
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "bot_database.db").strip()
DEFAULT_PREFIX = os.getenv("BOT_PREFIX", ".").strip()

# =====================================================================
# Primary Channel & Category Routing
# =====================================================================
BACKUP_CHANNEL_ID = get_env_int("BACKUP_CHANNEL_ID", 0)
COMMANDS_CHANNEL_ID = get_env_int("COMMANDS_CHANNEL_ID", 0)
AUDIT_LOGS_CHANNEL_ID = get_env_int("AUDIT_LOGS_CHANNEL_ID", 0)
ANNOUNCEMENT_CHANNEL_ID = get_env_int("ANNOUNCEMENT_CHANNEL_ID", 0)
CONFESSION_CHANNEL_ID = get_env_int("CONFESSION_CHANNEL_ID", 0)
TICKET_LOGS_CHANNEL_ID = get_env_int("TICKET_LOGS_CHANNEL_ID", 0)
BOT_ERRORS_CHANNEL_ID = get_env_int("BOT_ERRORS_CHANNEL_ID", 0)
WELCOME_CHANNEL_ID = get_env_int("WELCOME_CHANNEL_ID", 0)
TICKET_CATEGORY_ID = get_env_int("TICKET_CATEGORY_ID", 0)

# =====================================================================
# Staff Roles & Authority Permissions
# =====================================================================
DIRECTOR_ROLE_NAME = os.getenv("DIRECTOR_ROLE_NAME", "Director").strip()
MEMBER_ROLE_NAME = os.getenv("MEMBER_ROLE_NAME", "Member").strip()
LEVEL_UP_PING_ROLE_NAME = "Level Alerts"
BUMP_PING_ROLE_NAME = "Bump Pings"
REVIVE_ROLE_NAME = "Revive Pings"

# =====================================================================
# Assets, Graphics & Banners
# =====================================================================
DEFAULT_ICON = "https://cdn.discordapp.com/emojis/853989392277569566.gif?quality=lossless"
TICKET_BANNER = "https://images.unsplash.com/photo-1517245386807-bb43f82c33c4?auto=format&fit=crop&w=1000&q=80"
BDAY_BANNER = "https://images.unsplash.com/photo-1513151233558-d860c5398176?auto=format&fit=crop&w=1000&q=80"
CONFESSION_BANNER = "https://images.unsplash.com/photo-1518495973542-4542c06a5843?auto=format&fit=crop&w=1000&q=80"
COLOR_BANNER = "https://images.unsplash.com/photo-1550684848-fac1c5b4e853?auto=format&fit=crop&w=1000&q=80"
WELCOME_BANNER = "https://images.unsplash.com/photo-1511512578047-dfb367046420?auto=format&fit=crop&w=1000&q=80"

LEVEL_UP_SOUND_FILE = os.getenv("LEVEL_UP_SOUND_FILE", "assets/levelup.mp3").strip()

# =====================================================================
# Palette Settings for Color Roles Studio
# =====================================================================
COLOR_PALETTE = {
    "Red": (0xFF2A2A, "🔴"),
    "Yellow": (0xFFDF00, "🟡"),
    "Green": (0x2ECC71, "🟢"),
    "Blue": (0x3498DB, "🔵"),
    "Pink": (0xFF69B4, "🌸"),
    "Orange": (0xFF7F00, "🟠")
}

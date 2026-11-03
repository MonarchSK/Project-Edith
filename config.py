import os
from dotenv import load_dotenv

# Load local environment variables from .env if present
load_dotenv()

# =====================================================================
# Discord Authentication & Primary Channels
# =====================================================================
TOKEN = os.getenv("DISCORD_TOKEN")

# Channel IDs (Fall back to 0 if not provided in environment)
BACKUP_CHANNEL_ID = int(os.getenv("BACKUP_CHANNEL_ID", "0"))
ANNOUNCEMENT_CHANNEL_ID = int(os.getenv("ANNOUNCEMENT_CHANNEL_ID", "0"))
CONFESSION_CHANNEL_ID = int(os.getenv("CONFESSION_CHANNEL_ID", "0"))
TICKET_LOGS_CHANNEL_ID = int(os.getenv("TICKET_LOGS_CHANNEL_ID", "0"))
BOT_ERRORS_CHANNEL_ID = int(os.getenv("BOT_ERRORS_CHANNEL_ID", "0"))

# =====================================================================
# Staff Roles & Authority Permissions
# =====================================================================
DIRECTOR_ROLE_NAME = "Director"
LEVEL_UP_PING_ROLE_NAME = "Level Alerts"  # Role mentioned upon level-up (set to "" or None to disable)
BUMP_PING_ROLE_NAME = "Bump Pings"

# =====================================================================
# Assets, Graphics & Banners
# =====================================================================
DEFAULT_ICON = "https://cdn.discordapp.com/emojis/853989392277569566.gif?quality=lossless"
TICKET_BANNER = "https://images.unsplash.com/photo-1517245386807-bb43f82c33c4?auto=format&fit=crop&w=1000&q=80"
BDAY_BANNER = "https://images.unsplash.com/photo-1513151233558-d860c5398176?auto=format&fit=crop&w=1000&q=80"
CONFESSION_BANNER = "https://images.unsplash.com/photo-1518495973542-4542c06a5843?auto=format&fit=crop&w=1000&q=80"
COLOR_BANNER = "https://images.unsplash.com/photo-1550684848-fac1c5b4e853?auto=format&fit=crop&w=1000&q=80"

# Dedicated Welcome Portal Banner
WELCOME_BANNER = "https://images.unsplash.com/photo-1511512578047-dfb367046420?auto=format&fit=crop&w=1000&q=80"

# Audio Files (For Voice Notifications)
LEVEL_UP_SOUND_FILE = "assets/levelup.mp3"

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

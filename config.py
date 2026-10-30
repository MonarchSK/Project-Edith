import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
BACKUP_CHANNEL_ID = int(os.getenv("BACKUP_CHANNEL_ID", "0"))
ANNOUNCEMENT_CHANNEL_ID = int(os.getenv("ANNOUNCEMENT_CHANNEL_ID", "0"))
CONFESSION_CHANNEL_ID = int(os.getenv("CONFESSION_CHANNEL_ID", "0"))
DIRECTOR_ROLE_NAME = "Director"

DEFAULT_ICON = "https://cdn.discordapp.com/emojis/853989392277569566.gif?quality=lossless"
TICKET_BANNER = "https://images.unsplash.com/photo-1517245386807-bb43f82c33c4?auto=format&fit=crop&w=1000&q=80"
BDAY_BANNER = "https://images.unsplash.com/photo-1513151233558-d860c5398176?auto=format&fit=crop&w=1000&q=80"
CONFESSION_BANNER = "https://images.unsplash.com/photo-1518495973542-4542c06a5843?auto=format&fit=crop&w=1000&q=80"
COLOR_BANNER = "https://images.unsplash.com/photo-1550684848-fac1c5b4e853?auto=format&fit=crop&w=1000&q=80"

COLOR_PALETTE = {
    "Red": (0xFF2A2A, "🔴"),
    "Yellow": (0xFFDF00, "🟡"),
    "Green": (0x2ECC71, "🟢"),
    "Blue": (0x3498DB, "🔵"),
    "Pink": (0xFF69B4, "🌸"),
    "Orange": (0xFF7F00, "🟠")
}

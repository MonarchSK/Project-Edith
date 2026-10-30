import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
BACKUP_CHANNEL_ID = int(os.getenv("BACKUP_CHANNEL_ID", "0"))
ANNOUNCEMENT_CHANNEL_ID = int(os.getenv("ANNOUNCEMENT_CHANNEL_ID", "0"))
CONFESSION_CHANNEL_ID = int(os.getenv("CONFESSION_CHANNEL_ID", "0"))
DIRECTOR_ROLE_NAME = "Director"

DEFAULT_ICON = "https://media.giphy.com/media/26tn3334vHJwdN0Os/giphy.gif"
TICKET_BANNER = "https://i.imgur.com/8N4kUeF.png"
BDAY_BANNER = "https://i.imgur.com/uO60Q7j.png"
CONFESSION_BANNER = "https://i.imgur.com/x0qjPz1.png"
COLOR_BANNER = "https://i.imgur.com/8N4kUeF.png"

COLOR_PALETTE = {
    "Red": (0xFF2A2A, "🔴"),
    "Yellow": (0xFFDF00, "🟡"),
    "Green": (0x2ECC71, "🟢"),
    "Blue": (0x3498DB, "🔵"),
    "Pink": (0xFF69B4, "🌸"),
    "Orange": (0xFF7F00, "🟠")
}

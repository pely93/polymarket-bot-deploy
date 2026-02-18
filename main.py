import threading
from flask import Flask
import asyncio
from bot_logic import PolyBot

app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    # Render requires binding to 0.0.0.0 and port 10000 by default
    app.run(host='0.0.0.0', port=10000)

if __name__ == "__main__":
    # 1. Start Flask in a background thread
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 2. Start the Async Bot loop
    bot = PolyBot()
    asyncio.run(bot.run())
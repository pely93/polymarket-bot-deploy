"""
Gunicorn hook: start the bot loop when gunicorn loads the app.
This replaces the if __name__ == "__main__" block for production.
"""
import threading
import logging

logger = logging.getLogger("polybot")

def on_starting(server):
    """Called just before the master process is initialized."""
    pass

def post_fork(server, worker):
    """Called just after a worker has been forked — start the bot here."""
    from bot import bot_main_loop
    logger.info("Gunicorn worker forked — starting bot loop thread")
    t = threading.Thread(target=bot_main_loop, daemon=True)
    t.start()

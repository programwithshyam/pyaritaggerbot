import asyncio
import logging
import random
from telethon import TelegramClient, events
from telethon.tl.types import ChannelParticipantsAdmins, UserStatusRecently, UserStatusOnline
import os
from dotenv import load_dotenv
from flask import Flask

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
API_ID = int(os.getenv('API_ID'))  
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ONLY = True
BATCH_SIZE = 5
COOLDOWN_SECONDS = 30

# Global variables
last_command_time = {}
active_tag_operations = {}
processing_messages = {}

# Emoji configuration
EMOJI_POOL = [
    # (Your emoji list here)
]

def get_emoji_for_user(user):
    """Returns consistent emoji for each user"""
    random.seed(user.id)  # Same user gets same emoji
    return random.choice(EMOJI_POOL)

async def is_admin(client, chat_id, user_id):
    """Check if user is admin in the chat"""
    if not ADMIN_ONLY:
        return True
        
    try:
        participants = await client.get_participants(
            chat_id,
            filter=ChannelParticipantsAdmins
        )
        return any(admin.id == user_id for admin in participants)
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

async def get_active_members(client, chat_id, include_admins=True, include_online=False):
    """Get active members with filtering options"""
    members = []
    async for user in client.iter_participants(chat_id):
        if user.bot or user.deleted or user.is_self:
            continue
        if not include_admins and hasattr(user, 'participant') and user.participant.admin_rights:
            continue
        if include_online and not isinstance(user.status, (UserStatusOnline, UserStatusRecently)):
            continue
        members.append(user)
    return members

async def send_batched_mentions(client, chat_id, members, mention_prefix=""):
    """Send mentions with emoji display"""
    for i in range(0, len(members), BATCH_SIZE):
        if chat_id in active_tag_operations and active_tag_operations[chat_id]:
            logger.info(f"Tagging operation cancelled in chat {chat_id}")
            del active_tag_operations[chat_id]
            return False
            
        batch = members[i:i + BATCH_SIZE]
        mentions = []
        
        for user in batch:
            emoji = get_emoji_for_user(user)
            mentions.append(f"[{emoji}](tg://user?id={user.id})")
        
        message = f"{mention_prefix}{' '.join(mentions)}"
        try:
            await client.send_message(chat_id, message)
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error sending batch: {e}")
    return True

async def handle_tag_command(event, client, include_admins=True, include_online=False, mention_prefix=""):
    """Handle tag commands"""
    chat_id = event.chat_id
    user_id = event.sender_id
    
    if chat_id not in active_tag_operations:
        active_tag_operations[chat_id] = False
    
    current_time = asyncio.get_event_loop().time()
    last_time = last_command_time.get(chat_id, 0)
    if current_time - last_time < COOLDOWN_SECONDS and not active_tag_operations[chat_id]:
        remaining = int(COOLDOWN_SECONDS - (current_time - last_time))
        await event.reply(f"⌛ Please wait {remaining} seconds before using this command again.")
        return
        
    if not await is_admin(client, chat_id, user_id):
        await event.reply("⚠️ Only admins can use this command.")
        return
    
    active_tag_operations[chat_id] = False
        
    try:
        members = await get_active_members(
            client, 
            chat_id, 
            include_admins=include_admins,
            include_online=include_online
        )
        
        if not members:
            await event.reply("❌ No matching members found to tag.")
            return
            
        processing_msg = await event.reply(f"⏳ Preparing to tag {len(members)} members as emoji...\nSend /tagclose to cancel.")
        processing_messages[chat_id] = processing_msg.id
        
        completed = await send_batched_mentions(
            client, 
            chat_id, 
            members, 
            mention_prefix=mention_prefix
        )
        
        if completed:
            last_command_time[chat_id] = current_time
            
        if chat_id in processing_messages:
            try:
                await client.delete_messages(chat_id, processing_messages[chat_id])
                del processing_messages[chat_id]
            except Exception as e:
                logger.error(f"Error deleting processing message: {e}")
        
    except Exception as e:
        logger.error(f"Error in tag handler: {e}")
        await event.reply("❌ An error occurred while processing the command.")
    finally:
        if chat_id in active_tag_operations:
            del active_tag_operations[chat_id]

async def handle_tag_close(event, client):
    """Handle tagclose command"""
    chat_id = event.chat_id
    user_id = event.sender_id
    
    if not await is_admin(client, chat_id, user_id):
        await event.reply("⚠️ Only admins can cancel tagging operations.")
        return
    
    if chat_id not in active_tag_operations or not active_tag_operations[chat_id]:
        await event.reply("ℹ️ No active tagging operation to cancel.")
        return
    
    active_tag_operations[chat_id] = True
    await event.reply("🛑 Tagging operation cancelled successfully!")
    
    if chat_id in processing_messages:
        try:
            await client.delete_messages(chat_id, processing_messages[chat_id])
            del processing_messages[chat_id]
        except Exception as e:
            logger.error(f"Error deleting processing message: {e}")

# Initialize Flask app
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running!"

async def main():
    """Main function to start the bot"""
    client = TelegramClient('tagbot', API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)

    @client.on(events.NewMessage(pattern=r'^[/.!]tag(all)?$'))
    async def tag_all_handler(event):
        await handle_tag_command(event, client, include_admins=True, include_online=True, mention_prefix="👥 All Members: ")

    @client.on(events.NewMessage(pattern=r'^[/.!]taguser(s)?$'))
    async def tag_users_handler(event):
        await handle_tag_command(event, client, include_admins=False, include_online=False)

    @client.on(events.NewMessage(pattern=r'^[/.!]tagadmin(s)?$'))
    async def tag_admins_handler(event):
        await handle_tag_command(event, client, include_admins=True, include_online=True, mention_prefix="👮 Admins: ")

    @client.on(events.NewMessage(pattern=r'^[/.!]tagclose$'))
    async def tag_close_handler(event):
        await handle_tag_close(event, client)

    logger.info("Bot started successfully with emoji tagging")
    await client.run_until_disconnected()

if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # Start the Flask app in a separate thread
        from threading import Thread
        Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': int(os.environ.get("PORT", 5000))}).start()
        
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        loop.close()

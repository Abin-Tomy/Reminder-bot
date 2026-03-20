from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.background import BackgroundScheduler
import sqlite3
import logging
from datetime import datetime, timedelta
import pytz
import re

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Setup DB for reminders ---
conn = sqlite3.connect("reminders.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    message TEXT,
    remind_time TEXT,
    created_at TEXT,
    status TEXT DEFAULT 'pending'
)
""")
conn.commit()

# --- Scheduler ---
scheduler = BackgroundScheduler()
scheduler.start()

# --- Helper Functions ---
def parse_time_input(time_str):
    """Parse various time formats like '30m', '2h', '1d', '5 minutes', etc."""
    time_str = time_str.lower().strip()
    
    # Match patterns like: 30m, 2h, 1d, 45min, 2hrs, 1day
    pattern = r'(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)'
    match = re.match(pattern, time_str)
    
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        
        if unit in ['m', 'min', 'mins', 'minute', 'minutes']:
            return amount
        elif unit in ['h', 'hr', 'hrs', 'hour', 'hours']:
            return amount * 60
        elif unit in ['d', 'day', 'days']:
            return amount * 24 * 60
    
    # Try to parse as plain number (assume minutes)
    try:
        return int(time_str)
    except ValueError:
        return None

# --- Command: /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = """
🤖 **Welcome to Reminder Bot!**

**Commands:**
- `/remind <time> <message>` - Set a reminder
- `/list` - View your pending reminders
- `/cancel <id>` - Cancel a reminder
- `/help` - Show this help message

**Time formats you can use:**
- `5m` or `5 minutes` - 5 minutes
- `2h` or `2 hours` - 2 hours  
- `1d` or `1 day` - 1 day
- `30` - 30 minutes (default)

**Examples:**
- `/remind 15m Take a break`
- `/remind 2h Call mom`
- `/remind 1d Pay rent`
    """
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

# --- Command: /help ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# --- Command: /remind ---
async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ **Usage:** `/remind <time> <message>`\n"
            "**Example:** `/remind 30m Buy groceries`",
            parse_mode='Markdown'
        )
        return

    time_input = context.args[0]
    message = " ".join(context.args[1:])
    
    # Parse time
    minutes = parse_time_input(time_input)
    if minutes is None:
        await update.message.reply_text(
            "❌ **Invalid time format!**\n"
            "Use formats like: `5m`, `2h`, `1d`, or just `30` (minutes)",
            parse_mode='Markdown'
        )
        return
    
    if minutes <= 0 or minutes > 525600:  # Max 1 year
        await update.message.reply_text("❌ Time must be between 1 minute and 1 year!")
        return
    
    remind_time = datetime.now() + timedelta(minutes=minutes)
    created_at = datetime.now().isoformat()
    
    try:
        # Save to DB
        cur.execute(
            "INSERT INTO reminders (user_id, message, remind_time, created_at) VALUES (?, ?, ?, ?)",
            (update.effective_user.id, message, remind_time.isoformat(), created_at)
        )
        conn.commit()
        reminder_id = cur.lastrowid
        
        # Schedule
        scheduler.add_job(
            send_reminder, 'date', 
            run_date=remind_time,
            args=[update.effective_user.id, message, context.application, reminder_id],
            id=f"reminder_{reminder_id}"
        )
        
        # Format response
        if minutes < 60:
            time_str = f"{minutes} minute{'s' if minutes != 1 else ''}"
        elif minutes < 1440:
            hours = minutes // 60
            time_str = f"{hours} hour{'s' if hours != 1 else ''}"
        else:
            days = minutes // 1440
            time_str = f"{days} day{'s' if days != 1 else ''}"
        
        await update.message.reply_text(
            f"⏰ **Reminder #{reminder_id} set!**\n"
            f"📝 Message: {message}\n"
            f"⏱️ Time: {time_str} from now\n"
            f"📅 At: {remind_time.strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error setting reminder: {e}")
        await update.message.reply_text("❌ Error setting reminder. Please try again.")

# --- Command: /list ---
async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cur.execute(
        "SELECT id, message, remind_time FROM reminders WHERE user_id = ? AND status = 'pending' ORDER BY remind_time",
        (user_id,)
    )
    reminders = cur.fetchall()
    
    if not reminders:
        await update.message.reply_text("📭 You have no pending reminders.")
        return
    
    response = "📋 **Your Pending Reminders:**\n\n"
    for reminder_id, message, remind_time_str in reminders:
        remind_time = datetime.fromisoformat(remind_time_str)
        time_left = remind_time - datetime.now()
        
        if time_left.total_seconds() > 0:
            if time_left.days > 0:
                time_str = f"{time_left.days}d {time_left.seconds//3600}h"
            elif time_left.seconds > 3600:
                time_str = f"{time_left.seconds//3600}h {(time_left.seconds%3600)//60}m"
            else:
                time_str = f"{time_left.seconds//60}m"
            
            response += f"**#{reminder_id}** - {message}\n"
            response += f"⏰ In {time_str} ({remind_time.strftime('%m/%d %H:%M')})\n\n"
    
    await update.message.reply_text(response, parse_mode='Markdown')

# --- Command: /cancel ---
async def cancel_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ **Usage:** `/cancel <reminder_id>`\nUse `/list` to see your reminders.")
        return
    
    try:
        reminder_id = int(context.args[0])
        user_id = update.effective_user.id
        
        # Check if reminder exists and belongs to user
        cur.execute(
            "SELECT message FROM reminders WHERE id = ? AND user_id = ? AND status = 'pending'",
            (reminder_id, user_id)
        )
        result = cur.fetchone()
        
        if not result:
            await update.message.reply_text("❌ Reminder not found or already completed.")
            return
        
        # Update status in DB
        cur.execute(
            "UPDATE reminders SET status = 'cancelled' WHERE id = ?",
            (reminder_id,)
        )
        conn.commit()
        
        # Remove from scheduler
        try:
            scheduler.remove_job(f"reminder_{reminder_id}")
        except:
            pass  # Job might have already executed
        
        await update.message.reply_text(
            f"✅ **Reminder #{reminder_id} cancelled**\n"
            f"📝 Message was: {result[0]}",
            parse_mode='Markdown'
        )
        
    except ValueError:
        await update.message.reply_text("❌ Invalid reminder ID. Please use a number.")
    except Exception as e:
        logger.error(f"Error cancelling reminder: {e}")
        await update.message.reply_text("❌ Error cancelling reminder.")

# --- Function to send reminder ---
async def send_reminder(user_id, text, app, reminder_id):
    try:
        # Mark as completed in DB
        cur.execute("UPDATE reminders SET status = 'completed' WHERE id = ?", (reminder_id,))
        conn.commit()
        
        await app.bot.send_message(
            chat_id=user_id, 
            text=f"🔔 **REMINDER #{reminder_id}**\n\n📝 {text}",
            parse_mode='Markdown'
        )
        logger.info(f"Sent reminder {reminder_id} to user {user_id}")
    except Exception as e:
        logger.error(f"Error sending reminder {reminder_id}: {e}")

# --- Handle unknown commands ---
async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Unknown command. Use /help to see available commands."
    )

# --- Main ---
def main():
    # ⚠️ REPLACE THIS WITH YOUR ACTUAL BOT TOKEN ⚠️
    TOKEN = "8272201598:AAHe2lzOsjtug__5VLtqi32vt4dtxkQghrQ"
    
    app = Application.builder().token(TOKEN).build()
    
    # Add command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("remind", remind))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("cancel", cancel_reminder))
    
    # Handle unknown commands
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    
    print("🤖 Bot started! Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped.")
        scheduler.shutdown()
        conn.close()

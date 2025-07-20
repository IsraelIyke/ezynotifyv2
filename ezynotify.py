import os
import requests
from bs4 import BeautifulSoup
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime
import difflib
import time

# Load env variables
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_UPDATES_BOT_TOKEN = os.getenv("TELEGRAM_UPDATES_BOT_TOKEN")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def send_telegram_notification(chat_id, message, is_update=False):
    """Send a message to a Telegram chat using the appropriate bot token"""
    bot_token = TELEGRAM_UPDATES_BOT_TOKEN if is_update else TELEGRAM_BOT_TOKEN
    if not bot_token or not chat_id:
        print(f"⚠️ Telegram bot token or chat ID missing - notification not sent (is_update={is_update})")
        return
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"⚠️ Failed to send Telegram notification: {response.text}")
    except Exception as e:
        print(f"⚠️ Error sending Telegram notification: {e}")

def get_text_from_url(url):
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        text = soup.get_text(separator=' ')
        return text.lower()
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return ""

def get_diff(old_text, new_text):
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    changes = []

    # Compare line by line
    d = difflib.Differ()
    diff_lines = list(d.compare(old_lines, new_lines))

    for line in diff_lines:
        if line.startswith('+ ') or line.startswith('- '):
            action = 'added' if line.startswith('+ ') else 'removed'
            sentence = line[2:].strip()

            # Use word-level diff on this changed line
            old_words = sentence.split()
            ref_line = ''
            if action == 'added':
                ref_line = ''
                word_diff = difflib.ndiff([], old_words)
            else:
                ref_line = sentence
                word_diff = difflib.ndiff(old_words, [])

            for word_line in word_diff:
                if word_line.startswith('+ ') or word_line.startswith('- '):
                    word_action = 'added' if word_line.startswith('+ ') else 'removed'
                    word = word_line[2:]
                    changes.append({
                        "change": word,
                        "action": word_action,
                        "context": sentence,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })

    return changes

def format_updates_message(url, updates, new_detected_changes):
    """Format the updates message with detected changes"""
    message = f"🔄 <b>Website Changes Detected</b>\n\n<b>URL:</b> {url}\n\n"
    
    if new_detected_changes:
        message += "<b>New Changes:</b>\n"
        for change in new_detected_changes:
            emoji = "🟢" if change['action'] == 'added' else "🔴"
            message += f"{emoji} <b>{change['action'].title()}:</b> {change['change']}\n"
            if 'context' in change and change['context']:
                message += f"   <i>Context:</i> {change['context']}\n"
        message += "\n"
    
    if updates:
        message += "<b>Previous Changes:</b>\n"
        for update in updates[-5:]:  # Show only last 5 updates to avoid message being too long
            emoji = "🟢" if update['action'] == 'added' else "🔴"
            message += f"{emoji} <b>{update['action'].title()}:</b> {update['change']}\n"
            if 'context' in update and update['context']:
                message += f"   <i>Context:</i> {update['context']}\n"
    
    return message

def process_row(row):
    """Process a single row from the Supabase table"""
    row_id = row.get("id")
    url = row.get("url")
    keywords_dict = row.get("keywords") or {}
    old_reference = row.get("reference") or ""
    existing_found = row.get("foundKeyword") or []
    updates_log = row.get("Updates") or []
    telegram_id = row.get("telegramID")
    should_send_updates = row.get("shouldSendUpdates", False)
    should_send_detailed = row.get("shouldSendDetailedUpdates", False)
    check_updates = row.get("checkUpdates", False)
    should_continue_check = row.get("shouldContinueCheck", True)
    completed = row.get("completed", False)
    new_detected_changes = []
    
    # Skip if completed
    if completed:
        print(f"⏭️ Skipping completed check for URL: {url}")
        return
    
    # Skip if no keywords and not checking updates
    keyword_list = [k.lower() for k in keywords_dict.get("keywords", [])]
    if not keyword_list and not check_updates:
        print(f"⏭️ Skipping row - no keywords and checkUpdates is False for URL: {url}")
        return
    
    # Fetch and normalize new text
    new_text = get_text_from_url(url)
    update_data = {}
    
    # Process keywords if they exist
    if keyword_list:
        new_found = []
        remaining_keywords = [kw.lower() for kw in keywords_dict.get("keywords", [])]
        found_keywords = []

        for keyword in keyword_list:
            if keyword in new_text:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                found_entry = {"keyword": keyword, "foundAt": timestamp}
                new_found.append(found_entry)
                found_keywords.append(keyword)
                if keyword in remaining_keywords:
                    remaining_keywords.remove(keyword)

        print(f"✅ Keywords found: {new_found}")

        # Send notification if any keywords were found
        if telegram_id and found_keywords:
            if len(found_keywords) == 1:
                message = (f"🔔 <b>Keyword Found!</b>\n\n"
                          f"<b>Keyword:</b> {found_keywords[0]}\n"
                          f"<b>URL:</b> {url}\n"
                          f"<b>Found at:</b> {timestamp}")
            else:
                keywords_list = "\n".join([f"• {kw}" for kw in found_keywords])
                message = (f"🔔 <b>Multiple Keywords Found!</b>\n\n"
                          f"<b>Keywords:</b>\n{keywords_list}\n\n"
                          f"<b>URL:</b> {url}\n"
                          f"<b>Found at:</b> {timestamp}")
            send_telegram_notification(telegram_id, message, is_update=False)

        # Update keywords data
        update_data.update({
            "keywords": {"keywords": remaining_keywords},
            "foundKeyword": existing_found + new_found,
        })

        # Check if all keywords have been found and should stop checking
        if not remaining_keywords and not should_continue_check:
            update_data["completed"] = True
            print(f"🏁 All keywords found and shouldContinueCheck is False. Marking as completed.")

    # Process updates if enabled
    if check_updates:
        # If no old reference, save the text
        if not old_reference.strip():
            print("📌 No reference found. Saving new text.")
            update_data.update({
                "reference": new_text,
                "isUpdated": False
            })
        elif new_text != old_reference:
            print("🟡 Change detected... comparing for diff")

            # Detect changes and update 'Updates'
            text_diffs = get_diff(old_reference, new_text)
            if text_diffs:
                new_detected_changes = text_diffs
                updates_log.extend(text_diffs)
                print("📝 Updates:")
                for change in text_diffs:
                    print(f" - [{change['action']}] {change['change']} at {change['time']}")

            update_data.update({
                "reference": new_text,
                "isUpdated": True,
                "Updates": updates_log
            })

            # Send update notification if enabled
            if should_send_updates and telegram_id:
                if should_send_detailed:
                    message = format_updates_message(url, updates_log, new_detected_changes)
                else:
                    message = (f"🔄 <b>Website Changes Detected</b>\n\n"
                              f"<b>URL:</b> {url}\n"
                              f"<b>Changes detected at:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                              f"ℹ️ Detailed updates are available but not shown. Enable detailed updates to see them.")
                send_telegram_notification(telegram_id, message, is_update=True)
        else:
            print("✅ No change detected.")

    # Push to Supabase if we have updates
    if update_data:
        supabase.table("ezynotify").update(update_data).eq("id", row_id).execute()
        print(f"📤 Updated Supabase record for URL: {url}")

def main():
    print("⏳ Fetching all rows from Supabase...")
    try:
        response = supabase.table("ezynotify").select(
            "id, url, keywords, email, telegramID, reference, isUpdated, foundKeyword, "
            "shouldContinueCheck, Updates, shouldSendUpdates, shouldSendDetailedUpdates, "
            "checkUpdates, completed"
        ).execute()
        
        if response.data:
            print(f"🔍 Found {len(response.data)} rows to process")
            for i, row in enumerate(response.data, 1):
                print(f"\n🔹 Processing row {i}/{len(response.data)} - URL: {row.get('url')}")
                process_row(row)
                # Add a small delay between processing rows to avoid rate limiting
                time.sleep(1)
        else:
            print("No data found in the table.")
    except Exception as e:
        print(f"⚠️ Error fetching data from Supabase: {e}")

if __name__ == "__main__":
    main()
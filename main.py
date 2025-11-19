# -*- coding: utf-8 -*-
"""
accesvidbot — FileShareAdvanceBot (FULL FIXED) — PART 1/3

This file is a corrected, working version of the code you provided.
Part 1 contains:
 - imports + config
 - DB helpers
 - admin helpers
 - force-join helpers (including is_user_joined)
 - user/premium helpers
 - shortener helpers
 - access helpers
 - start flow, download flow, file upload handler
 - send/unlock helpers
 - join / check callback
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import time
import json
import random
import string
import threading
import requests
import traceback

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# =========================
# CONFIG
# =========================

BOT_TOKEN = "8262328116:AAGLcwnAW7FDrnHhEaX0pmNVpJNELn99iUQ"
BOT_USERNAME = "filesharekrbebot"  # without @
INITIAL_ADMIN = 8002925055
DATA_FILE = "filebot_data.json"

# Create bot
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
BOT_ID = None  # will be set on start

# runtime admin-state dict used by admin UI
ADMIN_STATE = {}  # chat_id -> {"mode": "...", "meta": ...}

# =========================
# DB HELPERS
# =========================

def load_db():
    if not os.path.exists(DATA_FILE):
        db = {
            "users": {},
            "files": {},
            "config": {
                "start_image_file_id": "",
                "join_image_file_id": "",
                "broadcast_channels": [],
                "access_api_url": "",
                "access_api_key": "",
                "force_join_channels": [],
            },
            "admins": [INITIAL_ADMIN],
            "access": {},
            "access_codes": {},
            "premium": {},
        }
        save_db(db)
        return db

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            db = json.load(f)
        except Exception:
            # if file corrupted, back it up and recreate
            backup = DATA_FILE + ".bak"
            try:
                os.rename(DATA_FILE, backup)
            except Exception:
                pass
            db = {
                "users": {},
                "files": {},
                "config": {
                    "start_image_file_id": "",
                    "join_image_file_id": "",
                    "broadcast_channels": [],
                    "access_api_url": "",
                    "access_api_key": "",
                    "force_join_channels": [],
                },
                "admins": [INITIAL_ADMIN],
                "access": {},
                "access_codes": {},
                "premium": {},
            }
            save_db(db)
            return db

    db.setdefault("users", {})
    db.setdefault("files", {})
    db.setdefault("config", {})
    cfg = db["config"]
    cfg.setdefault("start_image_file_id", "")
    cfg.setdefault("join_image_file_id", "")
    cfg.setdefault("broadcast_channels", [])
    cfg.setdefault("access_api_url", "")
    cfg.setdefault("access_api_key", "")
    cfg.setdefault("force_join_channels", [])
    db["config"] = cfg

    db.setdefault("admins", [INITIAL_ADMIN])
    db.setdefault("access", {})
    db.setdefault("access_codes", {})
    db.setdefault("premium", {})

    for uid, info in db["users"].items():
        info.setdefault("api_url", "")
        info.setdefault("api_key", "")
        info.setdefault("last_seen", 0)
        info.setdefault("total_files", 0)
        info.setdefault("welcome_pinned", False)
        db["users"][uid] = info

    return db

def save_db(db):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2)
    try:
        os.replace(tmp, DATA_FILE)
    except Exception:
        try:
            os.remove(DATA_FILE)
        except Exception:
            pass
        os.replace(tmp, DATA_FILE)

# =========================
# ADMIN HELPERS
# =========================

def get_admins():
    db = load_db()
    return db.get("admins", [])

def is_admin(uid: int) -> bool:
    return uid in get_admins()

def add_admin(uid: int) -> bool:
    db = load_db()
    admins = db.get("admins", [])
    if uid not in admins:
        admins.append(uid)
        db["admins"] = admins
        save_db(db)
        return True
    return False

def remove_admin(uid: int) -> bool:
    db = load_db()
    admins = db.get("admins", [])
    if uid in admins:
        admins.remove(uid)
        db["admins"] = admins
        save_db(db)
        return True
    return False

# =========================
# FORCE-JOIN HELPERS
# =========================

def canonical_username_from_input(s: str):
    """
    Convert '@name' or 'https://t.me/name' to '@name'.
    If it's an invite link like t.me/+xxxx or t.me/joinchat/... return None.
    """
    if not s:
        return None
    s = s.strip()
    if s.startswith("@"):
        return s
    if "t.me/" in s:
        try:
            part = s.split("t.me/", 1)[1]
            part = part.split("?", 1)[0].strip("/")
            if not part:
                return None
            low = part.lower()
            if low.startswith("+") or low.startswith("joinchat") or low.startswith("c/"):
                return None
            if not part.startswith("@"):
                part = "@" + part
            return part
        except Exception:
            return None
    return None

def join_url_for_channel_input(s: str):
    s = s.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    uname = canonical_username_from_input(s)
    if uname:
        return f"https://t.me/{uname.lstrip('@')}"
    return s

def is_bot_admin_in_channel(channel_username_or_id: str) -> bool:
    """
    Check if the bot is admin in a public channel by username (@channame or https://t.me/channame).
    Returns False on any error.
    """
    global BOT_ID
    if not BOT_ID:
        try:
            BOT_ID = bot.get_me().id
        except Exception:
            return False
    try:
        uname = canonical_username_from_input(channel_username_or_id)
        if not uname:
            # if not a canonical public username, we cannot check admin status
            return False
        # get_chat_member expects chat_id as either '@username' or numeric id
        member = bot.get_chat_member(uname, BOT_ID)
        status = getattr(member, "status", "")
        return status in ("administrator", "creator")
    except Exception:
        return False

def add_force_channel(ch_str: str) -> (bool, str):
    """
    Add a global force join channel.
    - if ch_str is @username: check bot admin in that channel; if not -> reject
    - if ch_str is invite link -> accept
    """
    db = load_db()
    cfg = db["config"]
    fj = cfg.get("force_join_channels", [])

    ch = ch_str.strip()
    uname = canonical_username_from_input(ch)
    if uname:
        if not is_bot_admin_in_channel(uname):
            return False, f"Bot is not admin in {uname}. Promote the bot there and try again."
        # store canonical form
        store = uname
        if store not in fj:
            fj.append(store)
            cfg["force_join_channels"] = fj
            db["config"] = cfg
            save_db(db)
        return True, f"Added force-join channel: {store}"
    else:
        # treat as invite link or unsupported form
        if ch not in fj:
            fj.append(ch)
            cfg["force_join_channels"] = fj
            db["config"] = cfg
            save_db(db)
        return True, f"Added private invite force-join link: {ch}"

def remove_force_channel(ch_str: str) -> (bool, str):
    db = load_db()
    cfg = db["config"]
    fj = cfg.get("force_join_channels", [])
    ch = ch_str.strip()
    if ch in fj:
        fj.remove(ch)
        cfg["force_join_channels"] = fj
        db["config"] = cfg
        save_db(db)
        return True, f"Removed: {ch}"
    # try canonical variant (if user passed without @)
    uname = canonical_username_from_input(ch)
    if uname and uname in fj:
        fj.remove(uname)
        cfg["force_join_channels"] = fj
        db["config"] = cfg
        save_db(db)
        return True, f"Removed: {uname}"
    return False, "That channel/link was not in force-join list."

def list_force_channels() -> list:
    db = load_db()
    cfg = db["config"]
    return cfg.get("force_join_channels", [])

# -------------------------
# CRITICAL: is_user_joined
# -------------------------
def is_user_joined(channel_username: str, user_id: int) -> bool:
    """
    Returns True if user_id is a member of the public channel @channel_username.
    For invite links/private links we cannot verify membership and this function should not be called.
    channel_username must be like '@channelname'.
    """
    # sanity
    if not channel_username or not channel_username.startswith("@"):
        return False
    try:
        # get_chat_member returns a ChatMember object
        member = bot.get_chat_member(channel_username, user_id)
        status = getattr(member, "status", "")
        # statuses: 'creator', 'administrator', 'member', 'restricted', 'left', 'kicked'
        return status in ("creator", "administrator", "member", "restricted")
    except telebot.apihelper.ApiException as e:
        # If user or chat not found or bot not in chat, treat as not joined.
        # For some channels (private or large), Telegram may not allow get_chat_member -> treat as False.
        # Log for debugging:
        # print("is_user_joined ApiException:", e)
        return False
    except Exception as e:
        # print("is_user_joined exception:", e)
        return False

# =========================
# USER / PREMIUM HELPERS
# =========================

def touch_user(user_id: int):
    db = load_db()
    uid = str(user_id)
    users = db["users"]
    if uid not in users:
        users[uid] = {
            "api_url": "",
            "api_key": "",
            "last_seen": time.time(),
            "total_files": 0,
            "welcome_pinned": False,
        }
    users[uid]["last_seen"] = time.time()
    db["users"] = users
    save_db(db)

def get_user_settings(user_id: int):
    db = load_db()
    uid = str(user_id)
    users = db["users"]
    if uid not in users:
        users[uid] = {
            "api_url": "",
            "api_key": "",
            "last_seen": time.time(),
            "total_files": 0,
            "welcome_pinned": False,
        }
        db["users"] = users
        save_db(db)
    return db["users"][uid]

def set_user_api_url(user_id: int, api_url: str):
    db = load_db()
    uid = str(user_id)
    users = db["users"]
    if uid not in users:
        users[uid] = {"api_url": "", "api_key": "", "last_seen": time.time(), "total_files": 0, "welcome_pinned": False}
    users[uid]["api_url"] = api_url.strip()
    db["users"] = users
    save_db(db)

def set_user_api_key(user_id: int, api_key: str):
    db = load_db()
    uid = str(user_id)
    users = db["users"]
    if uid not in users:
        users[uid] = {"api_url": "", "api_key": "", "last_seen": time.time(), "total_files": 0, "welcome_pinned": False}
    users[uid]["api_key"] = api_key.strip()
    db["users"] = users
    save_db(db)

def disable_shortener(user_id: int):
    db = load_db()
    uid = str(user_id)
    users = db["users"]
    if uid not in users:
        users[uid] = {"api_url": "", "api_key": "", "last_seen": time.time(), "total_files": 0, "welcome_pinned": False}
    users[uid]["api_url"] = ""
    users[uid]["api_key"] = ""
    db["users"] = users
    save_db(db)

# Premium
def set_premium(user_id: int, days: int):
    db = load_db()
    prem = db.get("premium", {})
    prem[str(user_id)] = time.time() + max(1, int(days)) * 86400
    db["premium"] = prem
    save_db(db)

def revoke_premium(user_id: int):
    db = load_db()
    prem = db.get("premium", {})
    if str(user_id) in prem:
        prem.pop(str(user_id))
        db["premium"] = prem
        save_db(db)
        return True
    return False

def is_premium(user_id: int) -> bool:
    db = load_db()
    ts = db.get("premium", {}).get(str(user_id), 0)
    return ts > time.time()

# =========================
# SHORTENER HELPERS
# =========================

def gen_code(length=10) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))

def shorten_with_api(api_url: str, api_key: str, url: str) -> str:
    if not api_url or not api_key:
        return url
    try:
        r = requests.get(api_url, params={"api": api_key, "url": url, "format": "json"}, timeout=15)
        j = r.json()
        for k in ("shortenedUrl", "short", "short_url", "url"):
            if j.get(k):
                return str(j[k])
    except Exception:
        return url
    return url

def shorten_for_owner_or_global(owner_id: int, url: str) -> str:
    s = get_user_settings(owner_id)
    api_url = (s.get("api_url") or "").strip()
    api_key = (s.get("api_key") or "").strip()
    if api_url and api_key:
        short = shorten_with_api(api_url, api_key, url)
        if short and short != url:
            return short
    db = load_db()
    cfg = db.get("config", {})
    g_api = (cfg.get("access_api_url") or "").strip()
    g_key = (cfg.get("access_api_key") or "").strip()
    if g_api and g_key:
        return shorten_with_api(g_api, g_key, url)
    return url

# =========================
# ACCESS HELPERS
# =========================

def has_access(user_id: int) -> bool:
    db = load_db()
    ts = db.get("access", {}).get(str(user_id), 0)
    return ts > time.time()

def grant_access_for_user(user_id: int, hours=24):
    db = load_db()
    db_access = db.get("access", {})
    db_access[str(user_id)] = time.time() + hours * 3600
    db["access"] = db_access
    save_db(db)

def gen_and_store_access_code():
    db = load_db()
    code = gen_code(12)
    while code in db.get("access_codes", {}):
        code = gen_code(12)
    ac = db.get("access_codes", {})
    ac[code] = time.time()
    db["access_codes"] = ac
    save_db(db)
    return code

def access_code_exists(code: str) -> bool:
    db = load_db()
    return code in db.get("access_codes", {})

# =========================
# MESSAGES / START FLOW
# =========================

def send_normal_start(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    touch_user(user_id)

    db = load_db()
    uid = str(user_id)
    users = db["users"]
    info = users.get(uid, {})

    cfg = db["config"]
    img_id = cfg.get("start_image_file_id", "")

    caption = (
        "Welcome to File sharing bot ! created by - @s5pydy \n\n"
    )

    sent_msg = None
    if img_id:
        try:
            sent_msg = bot.send_photo(chat_id, img_id, caption=caption)
        except Exception:
            try:
                sent_msg = bot.send_video(chat_id, img_id, caption=caption)
            except Exception:
                sent_msg = bot.send_message(chat_id, caption)
    else:
        sent_msg = bot.send_message(chat_id, caption)

    if message.chat.type == "private" and not info.get("welcome_pinned"):
        try:
            bot.pin_chat_message(chat_id, sent_msg.message_id, disable_notification=True)
        except Exception:
            pass
        info["welcome_pinned"] = True
        users[uid] = info
        db["users"] = users
        save_db(db)

def send_welcome_or_join_gate(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    touch_user(user_id)

    db = load_db()
    cfg = db["config"]
    fj = cfg.get("force_join_channels", [])
    if not fj:
        return send_normal_start(message)

    required_usernames = []
    join_buttons_info = []
    for ch in fj:
        join_url = join_url_for_channel_input(ch)
        join_buttons_info.append(join_url)
        uname = canonical_username_from_input(ch)
        if uname and uname not in required_usernames:
            required_usernames.append(uname)

    # If there are public usernames, check membership; otherwise show join buttons
    if required_usernames:
        all_joined = all(is_user_joined(u, user_id) for u in required_usernames)
    else:
        all_joined = False

    if all_joined:
        return send_normal_start(message)

    kb = InlineKeyboardMarkup()
    for i, join_url in enumerate(join_buttons_info, start=1):
        kb.row(InlineKeyboardButton(f"Channel {i}", url=join_url))
    kb.row(InlineKeyboardButton("✅ I Joined", callback_data=f"chk|nofile"))

    text = (
        "🔒 Access restricted.\n\n"
        "Please join all channels below, then press 'I Joined' To use this bot \n"
        "After you join, you will be shown the option to unlock access."
    )

    img_id = cfg.get("join_image_file_id", "")
    if img_id:
        sent = False
        try:
            bot.send_photo(chat_id, img_id, caption=text, reply_markup=kb)
            sent = True
        except Exception:
            try:
                bot.send_video(chat_id, img_id, caption=text, reply_markup=kb)
                sent = True
            except Exception:
                sent = False
        if not sent:
            bot.send_message(chat_id, text, reply_markup=kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)

# Helper to robustly extract payload from /start even when message.json absent
def get_start_payload_from_message(message):
    """
    Telegram sometimes provides message.text, sometimes message.json["text"].
    This helper extracts payload safely.
    Returns payload string after /start or empty string.
    """
    text = ""
    try:
        raw = getattr(message, "json", None) or {}
        if isinstance(raw, dict):
            text = raw.get("text") or ""
    except Exception:
        text = ""
    if not text:
        try:
            text = message.text or ""
        except Exception:
            text = ""
    # split into command and payload
    parts = text.split(maxsplit=1)
    if len(parts) > 1:
        return parts[1].strip()
    return ""

@bot.message_handler(commands=["start"])
def handle_start(message):
    payload = get_start_payload_from_message(message)
    db = load_db()
    cfg = db["config"]
    fj = cfg.get("force_join_channels", [])

    if fj:
        # If no payload -> show join gate or normal start
        if not payload:
            return send_welcome_or_join_gate(message)
        if payload.startswith("file_"):
            download_code = payload[5:]
            return handle_download_start(message, download_code)
        if payload.startswith("access_"):
            access_code = payload[7:]
            return handle_access_unlock(message, access_code)
        # unknown payload: still show join gate (safest)
        return send_welcome_or_join_gate(message)
    else:
        if not payload:
            return send_normal_start(message)
        if payload.startswith("file_"):
            download_code = payload[5:]
            return handle_download_start(message, download_code)
        if payload.startswith("access_"):
            access_code = payload[7:]
            return handle_access_unlock(message, access_code)
        return send_normal_start(message)

def handle_access_unlock(message, access_code: str):
    user_id = message.from_user.id
    chat_id = message.chat.id
    touch_user(user_id)

    if not access_code_exists(access_code):
        bot.send_message(chat_id, "❌ Invalid or expired access link.")
        return

    grant_access_for_user(user_id, hours=24)
    bot.send_message(
        chat_id,
        "🔓 <b>Token Refreshed for 24 Hours Now you can access any video/image ! </b>\nYou can now access any file without watching ads again.",
        parse_mode="HTML"
    )

# =========================
# FILE UPLOAD HANDLER (ADMINS ONLY)
# =========================

@bot.message_handler(content_types=["document", "video", "photo"])
def fileupload(message):
    uid = message.from_user.id
    chat = message.chat.id
    touch_user(uid)

    if not is_admin(uid):
        bot.send_message(chat, "❌ Only bot admins can upload/share files.")
        return

    caption = message.caption or ""
    if message.document:
        ftype = "document"
        fid = message.document.file_id
        fname = message.document.file_name or "Document"
    elif message.video:
        ftype = "video"
        fid = message.video.file_id
        fname = message.video.file_name if hasattr(message.video, "file_name") else "Video"
    else:
        ftype = "photo"
        fid = message.photo[-1].file_id
        fname = "Photo"

    settings = get_user_settings(uid)
    has_shortener = bool((settings.get("api_url") or "").strip() and (settings.get("api_key") or "").strip())

    db = load_db()

    download_code = gen_code()
    while download_code in db["files"]:
        download_code = gen_code()

    short_link = ""
    file_deep_link = f"https://t.me/{BOT_USERNAME}?start=file_{download_code}"

    if has_shortener:
        try:
            short_link = shorten_for_owner_or_global(uid, file_deep_link)
        except Exception:
            short_link = file_deep_link

    db["files"][download_code] = {
        "owner_id": uid,
        "file_type": ftype,
        "file_id": fid,
        "file_name": fname,
        "caption": caption,
        "created_at": time.time(),
        "short_link": short_link,
    }

    uid_str = str(uid)
    users = db["users"]
    if uid_str not in users:
        users[uid_str] = {"api_url": "", "api_key": "", "last_seen": time.time(), "total_files": 0, "welcome_pinned": False}
    users[uid_str]["total_files"] = users[uid_str].get("total_files", 0) + 1
    db["users"] = users

    save_db(db)

    # Feedback to admin
    if has_shortener and short_link:
        bot.send_message(
            chat,
            "✅ File saved successfully.\n\n"
            f"📁 Name: <code>{fname}</code>\n"
            "🔗 Share this earning link:\n"
            f"<code>{short_link}</code>",
            parse_mode="HTML"
        )
    elif has_shortener and not short_link:
        bot.send_message(
            chat,
            "✅ File saved successfully, but shortener failed.\n"
            "Share this bot link instead:\n"
            f"<code>{file_deep_link}</code>",
            parse_mode="HTML"
        )
    else:
        bot.send_message(
            chat,
            "✅ File saved successfully.\n\n"
            f"📁 Name: <code>{fname}</code>\n"
            "🔗 Share this link with your users:\n"
            f"<code>{file_deep_link}</code>\n\n"
            "When they open this link, they will receive the file (after joining global channels).",
            parse_mode="HTML"
        )

# =========================
# SEND / UNLOCK / DELETE HELPERS
# =========================

def send_file_to_user(chat_id: int, file_info: dict):
    file_type = file_info.get("file_type")
    file_id = file_info.get("file_id")
    caption = file_info.get("caption") or ""
    file_name = file_info.get("file_name") or ""

    final_caption = file_name
    if caption:
        if final_caption:
            final_caption += "\n\n" + caption
        else:
            final_caption = caption

    try:
        sent = None
        if file_type == "document":
            sent = bot.send_document(chat_id, file_id, caption=final_caption)
        elif file_type == "video":
            sent = bot.send_video(chat_id, file_id, caption=final_caption)
        elif file_type == "photo":
            sent = bot.send_photo(chat_id, file_id, caption=final_caption)
        else:
            sent = bot.send_document(chat_id, file_id, caption=final_caption)
        if sent:
            # delete after 20 minutes (1200 seconds)
            thread = threading.Thread(target=_delayed_delete, args=(chat_id, sent.message_id, 1200))
            thread.daemon = True
            thread.start()
    except Exception as e:
        # log
        try:
            traceback.print_exc()
        except Exception:
            pass
        try:
            bot.send_message(chat_id, "⚠ Failed to send file. Try again later.")
        except Exception:
            pass

def _delayed_delete(chat_id: int, message_id: int, delay_seconds: int):
    time.sleep(delay_seconds)
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass

def send_download_button(chat_id: int, file_info: dict, final_url: str):
    file_name = file_info.get("file_name") or "File"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⬇️ Download File", url=final_url))
    text = (
        f"🔓 <b>Unlocked!</b>\n\n"
        f"📁 <b>{file_name}</b>\n"
        f"Tap the button below to download."
    )
    bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")

def unlocked_send(chat_id: int, download_code: str, file_info: dict):
    owner_id = file_info["owner_id"]
    settings = get_user_settings(owner_id)
    has_shortener = bool((settings.get("api_url") or "").strip() and (settings.get("api_key") or "").strip())

    if not has_shortener:
        # if owner has no shortener configured, send the file directly
        send_file_to_user(chat_id, file_info)
        return

    short_link = file_info.get("short_link") or ""
    if not short_link:
        deep_link = f"https://t.me/{BOT_USERNAME}?start=file_{download_code}"
        short_link = shorten_for_owner_or_global(owner_id, deep_link)
        db = load_db()
        if download_code in db["files"]:
            db["files"][download_code]["short_link"] = short_link
            save_db(db)

    send_download_button(chat_id, file_info, short_link)

# =========================
# JOIN / CHECK CALLBACK
# =========================

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("chk|"))
def handle_check_join(call):
    data = call.data
    try:
        _, token = data.split("|", 1)
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid data.", show_alert=True)
        return

    user_id = call.from_user.id
    chat_id = call.message.chat.id
    touch_user(user_id)

    db = load_db()
    cfg = db["config"]
    fj = cfg.get("force_join_channels", [])

    required_usernames = []
    for ch in fj:
        uname = canonical_username_from_input(ch)
        if uname and uname not in required_usernames:
            required_usernames.append(uname)

    if required_usernames:
        all_joined = all(is_user_joined(u, user_id) for u in required_usernames)
    else:
        # If no public usernames to check, treat as passed (private invites can't be verified)
        all_joined = True

    if not all_joined:
        bot.answer_callback_query(call.id, "❌ You must join all public channels first.", show_alert=True)
        return

    bot.answer_callback_query(call.id, "✅ Verified joins!")

    # token == 'nofile' -> user clicked I Joined from main start
    if token == "nofile":
        if has_access(user_id) or is_premium(user_id):
            bot.send_message(chat_id, "You already have access (active/premium).")
            return
        admins = get_admins()
        owner_for_short = admins[0] if admins else INITIAL_ADMIN
        unlock_code = gen_and_store_access_code()
        deep_link = f"https://t.me/{BOT_USERNAME}?start=access_{unlock_code}"
        short_link = shorten_for_owner_or_global(owner_for_short, deep_link)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔓 Unlock Access", url=short_link))
        bot.send_message(chat_id, "🔓 You can now unlock 24h access:", reply_markup=kb)
        return

    # Otherwise token is download_code (file)
    download_code = token
    file_info = db["files"].get(download_code)
    if not file_info:
        bot.send_message(chat_id, "❌ File not found or removed.")
        return

    # After join verified: if user has access OR premium -> send file; else show unlock
    if has_access(user_id) or is_premium(user_id):
        send_file_to_user(chat_id, file_info)
        return

    owner_for_short = file_info.get("owner_id", get_admins()[0] if get_admins() else INITIAL_ADMIN)
    unlock_code = gen_and_store_access_code()
    deep_link = f"https://t.me/{BOT_USERNAME}?start=access_{unlock_code}"
    short_link = shorten_for_owner_or_global(owner_for_short, deep_link)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔓 Unlock Access", url=short_link))
    bot.send_message(chat_id, "Your access expired. Unlock for 24 hours:", reply_markup=kb)

# =========================
# DOWNLOAD START (FIXED LOGIC)
# =========================

def handle_download_start(message, download_code: str):
    """
    Handle /start file_<download_code>:
    - Check global force-join channels first (join gate)
    - If joined:
        - If user has access OR premium -> send file
        - Else -> show unlock (shortener) button (owner-based)
    - If not joined -> show join gate with I Joined button (callback contains download_code)
    """
    user_id = message.from_user.id
    chat_id = message.chat.id
    touch_user(user_id)

    db = load_db()
    file_info = db["files"].get(download_code)
    if not file_info:
        bot.send_message(chat_id, "❌ File not found or removed.")
        return

    # Build required usernames and join buttons
    cfg = db["config"]
    fj = cfg.get("force_join_channels", [])
    required_usernames = []
    join_buttons_info = []
    for ch in fj:
        join_url = join_url_for_channel_input(ch)
        join_buttons_info.append(join_url)
        uname = canonical_username_from_input(ch)
        if uname and uname not in required_usernames:
            required_usernames.append(uname)

    if required_usernames:
        all_joined = all(is_user_joined(u, user_id) for u in required_usernames)
    else:
        # If no public usernames to check -> we treat all_joined as False to show private invite buttons
        all_joined = False

    # If not joined -> show join gate for this specific file
    if not all_joined:
        kb = InlineKeyboardMarkup()
        for i, join_url in enumerate(join_buttons_info, start=1):
            kb.row(InlineKeyboardButton(f"Channel {i}", url=join_url))
        kb.row(InlineKeyboardButton("✅ I Joined", callback_data=f"chk|{download_code}"))

        text = (
            "🔒 This file is locked.\n\n"
            "Please join all channels below, then press 'I Joined'.\n"
            "After you join, you will be shown the option to unlock access for this file."
        )

        join_img_id = cfg.get("join_image_file_id", "")
        if join_img_id:
            sent = False
            try:
                bot.send_photo(chat_id, join_img_id, caption=text, reply_markup=kb)
                sent = True
            except Exception:
                try:
                    bot.send_video(chat_id, join_img_id, caption=text, reply_markup=kb)
                    sent = True
                except Exception:
                    sent = False
            if not sent:
                bot.send_message(chat_id, text, reply_markup=kb)
        else:
            bot.send_message(chat_id, text, reply_markup=kb)
        return

    # If user joined all required public channels:
    # Now allow direct send if user has access or is premium; else show unlock shortener
    if has_access(user_id) or is_premium(user_id):
        send_file_to_user(chat_id, file_info)
        return

    owner_for_short = file_info.get("owner_id", get_admins()[0] if get_admins() else INITIAL_ADMIN)
    unlock_code = gen_and_store_access_code()
    deep_link = f"https://t.me/{BOT_USERNAME}?start=access_{unlock_code}"
    short_link = shorten_for_owner_or_global(owner_for_short, deep_link)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔓 Unlock Access", url=short_link))
    bot.send_message(chat_id, "Your access expired. Unlock for 24 hours:", reply_markup=kb)

# =========================
# USER COMMANDS (shortener config)
# =========================

@bot.message_handler(commands=["setapi"])
def setapi(message):
    uid = message.from_user.id
    chat = message.chat.id
    touch_user(uid)
    raw = getattr(message, "json", {}) or {}
    text = raw.get("text", "") or ""
    # support usage via caption or text arg
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        value = parts[1].strip()
    else:
        value = (raw.get("caption") or "").strip()
    if not value:
        bot.send_message(chat, "Usage:\n/setapi https://yourshortner.com/api")
        return
    set_user_api_url(uid, value)
    bot.send_message(chat, "✅ Shortener API URL saved.")

@bot.message_handler(commands=["setkey"])
def setkey(message):
    uid = message.from_user.id
    chat = message.chat.id
    touch_user(uid)
    raw = getattr(message, "json", {}) or {}
    text = raw.get("text", "") or ""
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        value = parts[1].strip()
    else:
        value = (raw.get("caption") or "").strip()
    if not value:
        bot.send_message(chat, "Usage:\n/setkey YOUR_API_KEY")
        return
    set_user_api_key(uid, value)
    bot.send_message(chat, "✅ Shortener API KEY saved.")

@bot.message_handler(commands=["disableshort"])
def cmd_disableshort(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    touch_user(user_id)
    disable_shortener(user_id)
    bot.send_message(chat_id, "✅ Link shortener disabled for your account.")

@bot.message_handler(commands=["mysettings"])
def cmd_mysettings(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    touch_user(user_id)
    s = get_user_settings(user_id)
    api_url = s.get("api_url") or "Not set"
    api_key = s.get("api_key") or "Not set"
    lines = [
        "⚙️ Your Settings\n",
        f"🔗 Shortener API URL:\n<code>{api_url}</code>\n",
        f"🔑 Shortener API key:\n<code>{api_key}</code>\n",
    ]
    bot.send_message(chat_id, "\n".join(lines))

# End of PART 1
# (Part 2 will continue with admin UI, admin callback handlers, admin state flows and admin text commands)
# =========================
# ADMIN PANEL UI & HANDLERS
# =========================

def admin_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("Set Start Image", callback_data="adm_set_start_img"),
        InlineKeyboardButton("Set Join Image", callback_data="adm_set_join_img"),
    )
    kb.row(
        InlineKeyboardButton("Add BC Channel", callback_data="adm_add_bc"),
        InlineKeyboardButton("List BC Channels", callback_data="adm_list_bc"),
    )
    kb.row(
        InlineKeyboardButton("Broadcast Users", callback_data="adm_bcast_users"),
        InlineKeyboardButton("Broadcast Channels", callback_data="adm_bcast_channels"),
    )
    kb.row(
        InlineKeyboardButton("Set Global Access API", callback_data="adm_set_access_api"),
        InlineKeyboardButton("Set Global Access KEY", callback_data="adm_set_access_key"),
    )
    kb.row(
        InlineKeyboardButton("Show Status", callback_data="adm_status"),
    )
    kb.row(
        InlineKeyboardButton("Add Premium User", callback_data="adm_add_premium"),
        InlineKeyboardButton("Remove All Access", callback_data="adm_remove_all_access"),
    )
    kb.row(
        InlineKeyboardButton("Add Admin", callback_data="adm_add_admin_panel"),
        InlineKeyboardButton("Remove Admin", callback_data="adm_remove_admin_panel"),
    )
    kb.row(
        InlineKeyboardButton("Add Force-Join Channel", callback_data="adm_add_force"),
        InlineKeyboardButton("Remove Force-Join Channel", callback_data="adm_remove_force"),
    )
    kb.row(
        InlineKeyboardButton("List Force-Join Channels", callback_data="adm_list_force"),
    )
    return kb

@bot.message_handler(commands=["admin"])
def handle_admin(message):
    if not is_admin(message.from_user.id):
        return
    chat_id = message.chat.id
    db = load_db()
    users = db.get("users", {})
    files = db.get("files", {})
    now = time.time()
    active_24h = sum(1 for info in users.values() if info.get("last_seen", 0) > now - 24 * 3600)
    text = (
        "🛠 Admin Panel\n\n"
        f"👥 Users in DB: {len(users)}\n"
        f"📈 Active in last 24h: {active_24h}\n"
        f"📁 Total files shared: {len(files)}"
    )
    bot.send_message(chat_id, text, reply_markup=admin_keyboard())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("adm_"))
def handle_admin_callbacks(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id)
        return

    cid = call.message.chat.id
    data = call.data

    # ---- IMAGES ----
    if data == "adm_set_start_img":
        ADMIN_STATE[cid] = {"mode": "set_start_img"}
        bot.send_message(cid, "Send file_id for START image.")
    elif data == "adm_set_join_img":
        ADMIN_STATE[cid] = {"mode": "set_join_img"}
        bot.send_message(cid, "Send file_id for JOIN PAGE image.")

    # ---- BC CHANNELS ----
    elif data == "adm_add_bc":
        ADMIN_STATE[cid] = {"mode": "add_bc"}
        bot.send_message(cid, "Send @username or link to add as broadcast channel.")
    elif data == "adm_list_bc":
        db = load_db()
        bcs = db["config"].get("broadcast_channels", [])
        if not bcs:
            bot.send_message(cid, "No broadcast channels set.")
        else:
            msg = "📢 Broadcast Channels:\n" + "\n".join(f"- <code>{ch}</code>" for ch in bcs)
            bot.send_message(cid, msg, parse_mode="HTML")

    elif data == "adm_bcast_users":
        ADMIN_STATE[cid] = {"mode": "bcast_users"}
        bot.send_message(cid, "Send message to broadcast to ALL USERS.")

    elif data == "adm_bcast_channels":
        ADMIN_STATE[cid] = {"mode": "bcast_channels"}
        bot.send_message(cid, "Send message to broadcast to ALL BROADCAST CHANNELS.")

    # ---- GLOBAL ACCESS SHORTENER ----
    elif data == "adm_set_access_api":
        ADMIN_STATE[cid] = {"mode": "set_access_api"}
        bot.send_message(cid, "Send GLOBAL access shortener API URL.")
    elif data == "adm_set_access_key":
        ADMIN_STATE[cid] = {"mode": "set_access_key"}
        bot.send_message(cid, "Send GLOBAL access shortener API KEY.")

    # ---- STATUS ----
    elif data == "adm_status":
        db = load_db()
        users = db.get("users", {})
        files = db.get("files", {})
        now = time.time()
        active_24h = sum(1 for info in users.values() if info.get("last_seen", 0) > now - 24 * 3600)
        text = (
            "📊 Bot Status\n\n"
            f"👥 Users in DB: {len(users)}\n"
            f"📈 Active in last 24h: {active_24h}\n"
            f"📁 Total files shared: {len(files)}"
        )
        bot.send_message(cid, text, reply_markup=admin_keyboard())

    # ---- PREMIUM ----
    elif data == "adm_add_premium":
        ADMIN_STATE[cid] = {"mode": "add_premium_step1"}
        bot.send_message(cid, "Send USER ID (numeric) to make premium.")

    elif data == "adm_remove_all_access":
        ADMIN_STATE[cid] = {"mode": "confirm_remove_all_access"}
        bot.send_message(cid, "⚠️ Type YES to REMOVE ALL users' temporary access (premium unaffected).")

    # ---- ADMIN MGMT ----
    elif data == "adm_add_admin_panel":
        ADMIN_STATE[cid] = {"mode": "panel_add_admin"}
        bot.send_message(cid, "Send USER ID to ADD as admin.")
    elif data == "adm_remove_admin_panel":
        ADMIN_STATE[cid] = {"mode": "panel_remove_admin"}
        bot.send_message(cid, "Send USER ID to REMOVE from admins.")

    # ---- FORCE JOIN MGMT ----
    elif data == "adm_add_force":
        ADMIN_STATE[cid] = {"mode": "add_force_step1"}
        bot.send_message(cid, "Send @channel or invite link to ADD to force-join.")
    elif data == "adm_remove_force":
        ADMIN_STATE[cid] = {"mode": "remove_force_step1"}
        bot.send_message(cid, "Send EXACT channel or invite link to REMOVE.")
    elif data == "adm_list_force":
        fcs = list_force_channels()
        if not fcs:
            bot.send_message(cid, "No force-join channels configured.")
        else:
            msg = "📢 GLOBAL Force-Join Channels:\n" + "\n".join(f"- <code>{ch}</code>" for ch in fcs)
            bot.send_message(cid, msg, parse_mode="HTML")

    bot.answer_callback_query(call.id)

# =========================
# ADMIN STATE HANDLER
# =========================

@bot.message_handler(func=lambda m: m.chat.id in ADMIN_STATE and is_admin(m.from_user.id))
def handle_admin_state(message):
    cid = message.chat.id
    state = ADMIN_STATE.get(cid, {})
    mode = state.get("mode")
    text = message.text or ""

    # ---- SET START IMAGE ----
    if mode == "set_start_img":
        db = load_db()
        db["config"]["start_image_file_id"] = text.strip()
        save_db(db)
        bot.send_message(cid, "✅ Start image saved.")
        ADMIN_STATE.pop(cid, None)
        return

    # ---- SET JOIN IMAGE ----
    if mode == "set_join_img":
        db = load_db()
        db["config"]["join_image_file_id"] = text.strip()
        save_db(db)
        bot.send_message(cid, "✅ Join image saved.")
        ADMIN_STATE.pop(cid, None)
        return

    # ---- ADD BC ----
    if mode == "add_bc":
        db = load_db()
        cfg = db["config"]
        bc = cfg.get("broadcast_channels", [])
        bc.append(text.strip())
        cfg["broadcast_channels"] = bc
        db["config"] = cfg
        save_db(db)
        bot.send_message(cid, f"✅ Added BC Channel: {text.strip()}")
        ADMIN_STATE.pop(cid, None)
        return

    # ---- BROADCAST USERS ----
    if mode == "bcast_users":
        db = load_db()
        users = db.get("users", {})
        msg_text = text
        sent = failed = 0
        for uid in users:
            try:
                bot.send_message(int(uid), msg_text)
                sent += 1
            except:
                failed += 1
        bot.send_message(cid, f"📢 USERS Broadcast done.\nSent: {sent}\nFailed: {failed}")
        ADMIN_STATE.pop(cid, None)
        return

    # ---- BROADCAST CHANNELS ----
    if mode == "bcast_channels":
        db = load_db()
        bcs = db["config"].get("broadcast_channels", [])
        msg_text = text
        sent = failed = 0
        for ch in bcs:
            try:
                bot.send_message(ch, msg_text)
                sent += 1
            except:
                failed += 1
        bot.send_message(cid, f"📢 CHANNEL Broadcast done.\nSent: {sent}\nFailed: {failed}")
        ADMIN_STATE.pop(cid, None)
        return

    # ---- GLOBAL SHORTENER API ----
    if mode == "set_access_api":
        db = load_db()
        db["config"]["access_api_url"] = text.strip()
        save_db(db)
        bot.send_message(cid, "✅ Global API URL saved.")
        ADMIN_STATE.pop(cid, None)
        return

    if mode == "set_access_key":
        db = load_db()
        db["config"]["access_api_key"] = text.strip()
        save_db(db)
        bot.send_message(cid, "✅ Global API KEY saved.")
        ADMIN_STATE.pop(cid, None)
        return

    # ---- PREMIUM STEP 1 ----
    if mode == "add_premium_step1":
        try:
            uid = int(text.strip())
            ADMIN_STATE[cid] = {"mode": "add_premium_step2", "meta": {"uid": uid}}
            bot.send_message(cid, "Send number of days (example: 30).")
        except:
            bot.send_message(cid, "❌ Invalid user ID.")
        return

    # ---- PREMIUM STEP 2 ----
    if mode == "add_premium_step2":
        meta = state.get("meta", {})
        uid = meta.get("uid")
        try:
            days = int(text.strip())
            if days <= 0:
                raise ValueError()
            set_premium(uid, days)
            bot.send_message(cid, f"✅ User {uid} premium for {days} days.")
            ADMIN_STATE.pop(cid, None)
        except:
            bot.send_message(cid, "❌ Invalid days.")
        return

    # ---- REMOVE ALL ACCESS ----
    if mode == "confirm_remove_all_access":
        if text.strip().upper() == "YES":
            db = load_db()
            db["access"] = {}
            save_db(db)
            bot.send_message(cid, "✅ All temporary access removed.")
        else:
            bot.send_message(cid, "Cancelled.")
        ADMIN_STATE.pop(cid, None)
        return

    # ---- ADD ADMIN ----
    if mode == "panel_add_admin":
        try:
            uid = int(text.strip())
            if add_admin(uid):
                bot.send_message(cid, f"✅ Added admin: {uid}")
            else:
                bot.send_message(cid, "⚠ User already admin.")
        except:
            bot.send_message(cid, "❌ Invalid user ID.")
        ADMIN_STATE.pop(cid, None)
        return

    # ---- REMOVE ADMIN ----
    if mode == "panel_remove_admin":
        try:
            uid = int(text.strip())
            if remove_admin(uid):
                bot.send_message(cid, f"🗑 Removed admin: {uid}")
            else:
                bot.send_message(cid, "❌ Not an admin.")
        except:
            bot.send_message(cid, "❌ Invalid user ID.")
        ADMIN_STATE.pop(cid, None)
        return

    # ---- ADD FORCE-JOIN ----
    if mode == "add_force_step1":
        ok, msg = add_force_channel(text.strip())
        bot.send_message(cid, msg)
        ADMIN_STATE.pop(cid, None)
        return

    # ---- REMOVE FORCE-JOIN ----
    if mode == "remove_force_step1":
        ok, msg = remove_force_channel(text.strip())
        bot.send_message(cid, msg)
        ADMIN_STATE.pop(cid, None)
        return
    # =========================
# ADMIN COMMANDS (text)
# =========================

@bot.message_handler(commands=["addadmin"])
def cmd_add_admin(message):
    if not is_admin(message.from_user.id):
        return bot.send_message(message.chat.id, "❌ You are not allowed.")
    parts = message.text.split()
    if len(parts) != 2:
        return bot.send_message(message.chat.id, "Usage: /addadmin USER_ID")
    try:
        uid = int(parts[1])
    except:
        return bot.send_message(message.chat.id, "❌ Invalid user ID.")
    if add_admin(uid):
        bot.send_message(message.chat.id, f"✅ Added new admin: {uid}")
    else:
        bot.send_message(message.chat.id, "⚠ User is already an admin.")

@bot.message_handler(commands=["deladmin"])
def cmd_del_admin(message):
    if not is_admin(message.from_user.id):
        return bot.send_message(message.chat.id, "❌ You are not allowed.")
    parts = message.text.split()
    if len(parts) != 2:
        return bot.send_message(message.chat.id, "Usage: /deladmin USER_ID")
    try:
        uid = int(parts[1])
    except:
        return bot.send_message(message.chat.id, "❌ Invalid user ID.")
    if remove_admin(uid):
        bot.send_message(message.chat.id, f"🗑 Removed admin: {uid}")
    else:
        bot.send_message(message.chat.id, "❌ That user was not an admin.")

@bot.message_handler(commands=["listadmins"])
def cmd_list_admins(message):
    if not is_admin(message.from_user.id):
        return
    admins = get_admins()
    lines = ["🛡 Admins:"]
    for a in admins:
        lines.append(f"- <code>{a}</code>")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")

# =========================
# START BOT
# =========================

if __name__ == "__main__":
    try:
        me = bot.get_me()
        BOT_ID = me.id
        print(f"Bot started as @{me.username}")
    except Exception as e:
        print("Failed to get bot info:", e)

    # IMPORTANT: skip_pending=True prevents bug where /start not shown
    bot.infinity_polling(skip_pending=True)
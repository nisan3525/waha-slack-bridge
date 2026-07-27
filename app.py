import os
import re
import ssl
import logging
import requests
import threading
import traceback
import base64
from flask import Flask, request, jsonify
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_bolt import App as BoltApp
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv
from channel_store import load_store, save_store

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
WAHA_URL = os.getenv("WAHA_URL", "").rstrip("/")
WAHA_API_KEY = os.getenv("WAHA_API_KEY", "")
WAHA_SESSION = os.getenv("WAHA_SESSION", "default")
AUTO_JOIN_EMAIL = os.getenv("AUTO_JOIN_EMAIL", "nisan@kmbmgt.com")
PORT = int(os.getenv("PORT", "5000"))

INCOMING_COLOR = "#25D366"
EMOJI_MAP = {
    "thumbsup": "👍", "+1": "👍", "thumbsdown": "👎", "-1": "👎",
    "heart": "❤️", "fire": "🔥", "laughing": "😂", "joy": "😂",
    "white_check_mark": "✅", "x": "❌", "clap": "👏", "pray": "🙏",
    "wave": "👋", "ok_hand": "👌", "raised_hands": "🙌",
    "eyes": "👀", "100": "💯", "rocket": "🚀", "tada": "🎉",
}

ssl_ctx = ssl._create_unverified_context()
slack_client = WebClient(token=SLACK_BOT_TOKEN, ssl=ssl_ctx)
bolt_app = BoltApp(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)

BOT_USER_ID = None

def _init_bot_id():
    global BOT_USER_ID
    try:
        BOT_USER_ID = slack_client.auth_test()["user_id"]
        logger.info(f"[startup] BOT_USER_ID={BOT_USER_ID}")
    except Exception as e:
        BOT_USER_ID = "UNKNOWN"

logger.info("[startup] loading store...")
store = load_store()
wa_to_channel = store["wa_to_channel"]
channel_to_wa = store["channel_to_wa"]
msg_id_map = store.get("msg_id_map", {})
logger.info(f"[startup] {len(wa_to_channel)} wa, {len(channel_to_wa)} channels")

def waha_headers():
    return {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"}

def waha_post(endpoint, payload):
    resp = requests.post(f"{WAHA_URL}{endpoint}",
        headers=waha_headers(), json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json() if resp.content else {}

def waha_get_media(msg_id):
    resp = requests.get(
        f"{WAHA_URL}/api/{WAHA_SESSION}/messages/{msg_id}/download-media",
        headers={"X-Api-Key": WAHA_API_KEY}, timeout=60)
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")

def send_waha_text(chat_id, text):
    url = f"{WAHA_URL}/api/sendText"
    body = {
        "chatId": chat_id,
        "text": text,
        "session": WAHA_SESSION
    }
    logger.info(f"[send_waha_text] POST {url} → {body}")
    try:
        resp = requests.post(
            url,
            headers=waha_headers(),
            json=body,
            timeout=60
        )
        logger.info(f"[send_waha_text] status={resp.status_code}, "
            f"response={resp.text[:200]}")
        resp.raise_for_status()
        return resp.json() if resp.content else {}
    except Exception as e:
        logger.error(f"[send_waha_text] FAILED: {e}, "
            f"response={getattr(e, 'response', None) and e.response.text[:200]}")
        raise

def send_waha_media(chat_id, file_bytes, filename, mimetype, caption=None):
    b64 = base64.b64encode(file_bytes).decode()
    file_obj = {"mimetype": mimetype, "filename": filename, "data": b64}
    if mimetype.startswith("image/"):
        ep, pl = "/api/sendImage", {"chatId": chat_id, "session": WAHA_SESSION,
            "file": file_obj, "caption": caption or ""}
    elif mimetype.startswith("video/"):
        ep, pl = "/api/sendVideo", {"chatId": chat_id, "session": WAHA_SESSION,
            "file": file_obj, "caption": caption or ""}
    elif mimetype.startswith("audio/"):
        ep, pl = "/api/sendVoice", {"chatId": chat_id, "session": WAHA_SESSION,
            "file": file_obj}
    else:
        ep, pl = "/api/sendFile", {"chatId": chat_id, "session": WAHA_SESSION,
            "file": file_obj, "caption": caption or ""}
    return waha_post(ep, pl)

def to_chat_id(number):
    digits_only = re.sub(r'[^\d]', '', number)
    return f"{digits_only}@c.us"

def from_chat_id(chat_id):
    return re.sub(r"@.*", "", chat_id)

def sanitize_name(name):
    if not name:
        return ""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    return re.sub(r"-+", "-", name).strip("-")

def invite_auto_join(channel_id):
    try:
        uid = slack_client.users_lookupByEmail(email=AUTO_JOIN_EMAIL)["user"]["id"]
        slack_client.conversations_invite(channel=channel_id, users=[uid])
    except Exception as e:
        logger.warning(f"[invite] {e}")

def get_or_create_channel(wa_number, contact_name=None):
    if wa_number in wa_to_channel:
        return wa_to_channel[wa_number]
    sanitized = sanitize_name(contact_name)
    ch_name = f"waha-{sanitized}" if sanitized else f"waha-{wa_number}"
    ch_name = ch_name[:80].lower().strip("-")
    try:
        ch_id = slack_client.conversations_create(name=ch_name)["channel"]["id"]
    except SlackApiError as e:
        if e.response["error"] == "name_taken":
            ch_id = None
            for ch in slack_client.conversations_list(
                types="public_channel,private_channel", limit=200)["channels"]:
                if ch["name"] == ch_name:
                    ch_id = ch["id"]
                    break
            if not ch_id:
                raise
        else:
            raise
    try:
        slack_client.conversations_join(channel=ch_id)
    except SlackApiError:
        pass
    invite_auto_join(ch_id)
    try:
        slack_client.conversations_setTopic(
            channel=ch_id,
            topic=f"WhatsApp chat with +{wa_number} | Reply here to send")
    except SlackApiError:
        pass
    wa_to_channel[wa_number] = ch_id
    channel_to_wa[ch_id] = {"wa_number": wa_number, "contact_name": contact_name}
    save_store({"wa_to_channel": wa_to_channel,
        "channel_to_wa": channel_to_wa,
        "msg_id_map": msg_id_map})
    return ch_id

def format_incoming(contact_name, wa_number, text):
    return {"attachments": [{"color": INCOMING_COLOR,
        "author_name": f"{contact_name} (+{wa_number})" if contact_name else f"+{wa_number}",
        "text": text, "footer": "WhatsApp", "mrkdwn_in": ["text"]}]}

def add_reaction(channel_id, ts, reaction):
    try:
        slack_client.reactions_add(channel=channel_id, name=reaction, timestamp=ts)
    except SlackApiError as e:
        if e.response.get("error") != "already_reacted":
            logger.warning(f"[reaction] {e}")

def get_user_name(user_id):
    try:
        return slack_client.users_info(user=user_id)["user"].get("real_name", "Unknown")
    except Exception:
        return "Unknown"

def download_slack_file(file_id):
    info = slack_client.files_info(file=file_id)["file"]
    url = info.get("url_private_download") or info.get("url_private")
    resp = requests.get(url, headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        timeout=60)
    resp.raise_for_status()
    return resp.content, info.get("mimetype", "application/octet-stream"), info.get("name", "file")

@flask_app.route("/waha/webhook", methods=["POST"])
def waha_webhook():
    data = request.json or {}
    event = data.get("event", "")
    payload = data.get("payload", {})
    logger.info(f"[waha_webhook] event={event}")
    if event == "message":
        threading.Thread(target=_handle_wa_message, args=(payload,), daemon=True).start()
    elif event == "message.reaction":
        threading.Thread(target=_handle_wa_reaction, args=(payload,), daemon=True).start()
    elif event in ("message.ack", "message_ack", "ack"):
        threading.Thread(target=_handle_wa_ack, args=(payload,), daemon=True).start()
    return jsonify({"ok": True})

def _handle_wa_message(payload):
    try:
        if payload.get("fromMe", False):
            return
        from_id = payload.get("from", "")
        wa_number = from_chat_id(from_id)
        contact_name = (payload.get("_data", {}).get("notifyName")
            or payload.get("notifyName")
            or payload.get("sender", {}).get("pushname") or None)
        msg_id = payload.get("id", "")
        body = payload.get("body", "")
        has_media = payload.get("hasMedia", False)
        msg_type = payload.get("type", "chat")
        channel_id = get_or_create_channel(wa_number, contact_name)
        slack_ts = None
        if has_media:
            try:
                media_bytes, ct = waha_get_media(msg_id)
                ext = ct.split("/")[-1].split(";")[0]
                filename = "voice_note.m4a" if msg_type in ("audio","ptt") else f"{msg_type}.{ext}"
                comment = f"{contact_name} (+{wa_number})" if contact_name else f"+{wa_number}"
                if body:
                    comment += f" — {body}"
                resp = slack_client.files_upload_v2(
                    channel=channel_id, file=media_bytes,
                    filename=filename, title=filename, initial_comment=comment)
                slack_ts = resp.get("ts")
            except Exception as e:
                logger.error(f"[wa_msg] media error: {e}")
        msg_payload = format_incoming(contact_name, wa_number,
            f"[{msg_type}] {body or '(media)'}")
        slack_ts = slack_client.chat_postMessage(
            channel=channel_id, **msg_payload).get("ts")
        if msg_id and slack_ts:
            msg_id_map[msg_id] = {"channel_id": channel_id, "ts": slack_ts}
            save_store({"wa_to_channel": wa_to_channel,
                "channel_to_wa": channel_to_wa,
                "msg_id_map": msg_id_map})
    except Exception as e:
        logger.error(f"[wa_msg] {e}\n{traceback.format_exc()}")

def _handle_wa_reaction(payload):
    try:
        reaction = payload.get("reaction", {})
        emoji = reaction.get("text", "")
        msg_id = reaction.get("msgId", "")
        info = msg_id_map.get(msg_id)
        if info:
            slack_client.chat_postMessage(
                channel=info["channel_id"],
                text=f"Reacted {emoji}", thread_ts=info["ts"])
    except Exception as e:
        logger.error(f"[wa_reaction] {e}")

def _handle_wa_ack(payload):
    try:
        msg_id = payload.get("id", "")
        ack = payload.get("ack", 0)
        info = msg_id_map.get(msg_id)
        logger.info(f"[wa_ack] msg_id={msg_id}, ack={ack}, found={info is not None}")
        if info:
            channel_id = info["channel_id"]
            ts = info["ts"]
            if ack == 1:
                add_reaction(channel_id, ts, "white_check_mark")
            elif ack == 2:
                add_reaction(channel_id, ts, "mailbox_with_mail")
            elif ack == 3:
                add_reaction(channel_id, ts, "eyes")
    except Exception as e:
        logger.error(f"[wa_ack] {e}")

@bolt_app.event("message")
def handle_slack_message(event, say):
    try:
        # Skip bot messages, app messages, and non-standard subtypes
        subtype = event.get("subtype")
        if event.get("bot_id"):
            return
        if event.get("app_id"):
            return
        if not event.get("user"):
            return
        if subtype is not None and subtype != "file_share":
            return
        channel_id = event.get("channel")
        text = event.get("text", "").strip()
        event_ts = event.get("ts")
        files = event.get("files", [])
        logger.info(f"[slack_msg] channel={channel_id}, "
            f"in_mapping={channel_id in channel_to_wa}, "
            f"text={text[:50]}")
        # Get wa_number from mapping or channel name/topic
        if channel_id in channel_to_wa:
            ch_data = channel_to_wa[channel_id]
            wa_number = ch_data.get("wa_number") if isinstance(ch_data, dict) else ch_data
        else:
            try:
                info = slack_client.conversations_info(channel=channel_id)
                ch = info["channel"]
                channel_name = ch.get("name", "")
                topic = ch.get("topic", {}).get("value", "")
            except Exception as e:
                logger.error(f"[slack_msg] conversations_info failed: {e}")
                return
            # Try to get number from topic first
            # Topic format: "WhatsApp chat with +14848910977 | ..."
            topic_match = re.search(r"WhatsApp chat with \+(\d+)", topic)
            if topic_match:
                wa_number = topic_match.group(1)
            elif channel_name.startswith("waha-"):
                # fallback: extract digits from channel name
                number = re.sub(r"[^\d]", "", channel_name[5:])
                if not number or len(number) < 7:
                    logger.info(f"[slack_msg] not a waha channel: {channel_name}")
                    return
                wa_number = number
            else:
                return
            wa_to_channel[wa_number] = channel_id
            channel_to_wa[channel_id] = {
                "wa_number": wa_number,
                "contact_name": None
            }
            save_store({"wa_to_channel": wa_to_channel,
                "channel_to_wa": channel_to_wa,
                "msg_id_map": msg_id_map})
            logger.info(f"[slack_msg] auto-mapped {channel_id} → {wa_number}")
        try:
            slack_client.conversations_setTopic(
                channel=channel_id,
                topic=f"WhatsApp chat with +{wa_number} | Reply here to send")
        except Exception:
            pass
        if not wa_number:
            logger.error("[slack_msg] wa_number is empty!")
            return
        chat_id = to_chat_id(wa_number)
        logger.info(f"[slack_msg] sending to chat_id={chat_id}")
        if files:
            for f in files:
                if not isinstance(f, dict):
                    continue
                try:
                    file_bytes, mimetype, filename = download_slack_file(f["id"])
                    send_waha_media(chat_id, file_bytes, filename, mimetype,
                        caption=text or None)
                    add_reaction(channel_id, event_ts, "white_check_mark")
                except Exception as e:
                    logger.error(f"[slack_msg] file error: {e}")
                    add_reaction(channel_id, event_ts, "x")
            return
        if not text:
            return
        try:
            result = send_waha_text(chat_id, text)
            logger.info(f"[slack_msg] send result: {result}")
            add_reaction(channel_id, event_ts, "white_check_mark")
            msg_id = result.get("id", "")
            if msg_id:
                msg_id_map[msg_id] = {"channel_id": channel_id, "ts": event_ts}
                save_store({"wa_to_channel": wa_to_channel,
                    "channel_to_wa": channel_to_wa,
                    "msg_id_map": msg_id_map})
        except Exception as e:
            logger.error(f"[slack_msg] send_waha_text FAILED: {e}")
            add_reaction(channel_id, event_ts, "x")
    except Exception as e:
        logger.error(f"[slack_msg] {e}\n{traceback.format_exc()}")

@bolt_app.event("reaction_added")
def handle_reaction(event):
    try:
        if event.get("user") == BOT_USER_ID:
            return
        channel_id = event.get("item", {}).get("channel", "")
        reaction_name = event.get("reaction", "")
        if channel_id not in channel_to_wa:
            return
        ch_data = channel_to_wa[channel_id]
        wa_number = ch_data.get("wa_number") if isinstance(ch_data, dict) else ch_data
        chat_id = to_chat_id(wa_number)
        emoji = EMOJI_MAP.get(reaction_name, f":{reaction_name}:")
        send_waha_text(chat_id, emoji)
    except Exception as e:
        logger.error(f"[slack_reaction] {e}")

@flask_app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    threading.Thread(target=_init_bot_id, daemon=True).start()
    socket_handler = SocketModeHandler(bolt_app, SLACK_APP_TOKEN)
    threading.Thread(target=socket_handler.start, daemon=True).start()
    logger.info(f"[startup] Flask starting on port {PORT}")
    flask_app.run(host="0.0.0.0", port=PORT, debug=False)


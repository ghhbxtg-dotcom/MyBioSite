import logging
import os
from typing import Any

import requests
from flask import Flask, jsonify, request


app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("deepsy-chat-server")


BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "8790773247:AAF3Q9Pn0-_LcbVJ2OevoLHc1GunTN2nuuc",
).strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6576927659").strip()
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
REQUEST_TIMEOUT = 15
MAX_MESSAGE_LENGTH = 1500
SITE_PREFIX = "Новое сообщение с сайта:"

STATE: dict[str, Any] = {
    "last_update_id": 0,
    "delivered_update_ids": set(),
}


def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.after_request
def after_request(response):
    return add_cors_headers(response)


@app.route("/", methods=["GET"])
def index():
    return jsonify({"ok": True, "service": "deepsy-chat-server"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "ok": True,
            "telegram_configured": bool(BOT_TOKEN and CHAT_ID),
            "last_update_id": STATE["last_update_id"],
        }
    )


@app.route("/send", methods=["POST", "OPTIONS"])
@app.route("/sendMessage", methods=["POST", "OPTIONS"])
def send_message():
    if request.method == "OPTIONS":
        return ("", 204)

    if not BOT_TOKEN or not CHAT_ID:
        return jsonify({"ok": False, "error": "Server is not configured"}), 500

    data = request.get_json(silent=True) or {}
    text = (data.get("message") or "").strip()

    if not text:
        return jsonify({"ok": False, "error": "Message is empty"}), 400

    if len(text) > MAX_MESSAGE_LENGTH:
        return jsonify({"ok": False, "error": "Message is too long"}), 400

    payload = {
        "chat_id": CHAT_ID,
        "text": f"{SITE_PREFIX}\n\n{text}",
    }

    try:
        response = requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        telegram_data = response.json()

        if not telegram_data.get("ok"):
            logger.error("Telegram sendMessage failed: %s", telegram_data)
            return jsonify({"ok": False, "error": "Telegram rejected the message"}), 502

        logger.info("Forwarded message from site to Telegram chat %s", CHAT_ID)
        return jsonify({"ok": True, "reply": "Сообщение отправлено"})
    except requests.RequestException as exc:
        logger.exception("Telegram sendMessage request failed")
        return jsonify({"ok": False, "error": f"Telegram request failed: {exc}"}), 502


def fetch_updates():
    if not BOT_TOKEN:
        return []

    params = {
        "timeout": 0,
        "allowed_updates": ["message"],
    }

    if STATE["last_update_id"]:
        params["offset"] = STATE["last_update_id"] + 1

    response = requests.get(
        f"{TELEGRAM_API_URL}/getUpdates",
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    data = response.json()
    if not data.get("ok"):
        raise RuntimeError("Telegram getUpdates returned ok=false")

    return data.get("result", [])


@app.route("/getReply", methods=["GET", "OPTIONS"])
def get_reply():
    if request.method == "OPTIONS":
        return ("", 204)

    if not BOT_TOKEN or not CHAT_ID:
        return jsonify({"ok": False, "error": "Server is not configured"}), 500

    try:
        updates = fetch_updates()
    except (requests.RequestException, RuntimeError) as exc:
        logger.exception("Failed to fetch Telegram updates")
        return jsonify({"ok": False, "error": f"Failed to fetch replies: {exc}"}), 502

    reply_text = None

    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            STATE["last_update_id"] = max(STATE["last_update_id"], update_id)

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        text = (message.get("text") or "").strip()

        if str(chat.get("id")) != CHAT_ID or not text:
            continue

        if text.startswith("/") or text.startswith(SITE_PREFIX):
            continue

        if update_id in STATE["delivered_update_ids"]:
            continue

        STATE["delivered_update_ids"].add(update_id)
        reply_text = text

    return jsonify({"ok": True, "reply": reply_text})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)

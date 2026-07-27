import json
import os
import logging

logger = logging.getLogger(__name__)

def _get_store_file():
    path = os.environ.get("STORE_PATH", "/app/data/channel_store.json")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # test write
        with open(path, "a"):
            pass
        logger.info(f"[store] using {path}")
        return path
    except Exception:
        logger.warning(f"[store] cannot write to {path}, falling back to /tmp")
        return "/tmp/channel_store.json"

STORE_FILE = _get_store_file()

def load_store():
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE, "r") as f:
                data = json.load(f)
                return {
                    "wa_to_channel": data.get("wa_to_channel", {}),
                    "channel_to_wa": data.get("channel_to_wa", {}),
                    "msg_id_map": data.get("msg_id_map", {})
                }
        except Exception:
            pass
    return {"wa_to_channel": {}, "channel_to_wa": {}, "msg_id_map": {}}

def save_store(store):
    os.makedirs(os.path.dirname(STORE_FILE), exist_ok=True)
    with open(STORE_FILE, "w") as f:
        json.dump(store, f)


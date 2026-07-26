import json
import os

STORE_FILE = "/app/data/channel_store.json"

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

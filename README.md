# WAHA Slack Bridge

A Flask app that bridges [WAHA (WhatsApp HTTP API)](https://github.com/devlikeapro/waha) with Slack.

## Features

- **WhatsApp → Slack**: Incoming WhatsApp messages are posted to Slack channels
- **Slack → WhatsApp**: Reply in Slack to send messages back to WhatsApp
- **Media Support**: Images, videos, audio, and files
- **Auto-channel Creation**: Creates Slack channels automatically per WhatsApp contact
- **Persistent Storage**: Stores channel mappings and message IDs in `/app/data`
- **Reaction Sync**: Reactions and message acknowledgments sync both ways
- **Socket Mode**: Uses Slack's Socket Mode for real-time events (no IP allowlist needed)

## Environment Variables

- `SLACK_BOT_TOKEN` - Slack bot OAuth token (xoxb-...)
- `SLACK_APP_TOKEN` - Slack app-level token (xapp-...)
- `SLACK_SIGNING_SECRET` - Slack signing secret
- `WAHA_URL` - URL of WAHA service (e.g., https://waha.example.com)
- `WAHA_API_KEY` - WAHA API key
- `WAHA_SESSION` - WAHA session name (default: "default")
- `AUTO_JOIN_EMAIL` - Email of Slack user to auto-invite to channels
- `PORT` - Flask port (default: 5000)

## Deployment on Railway

1. Connect this repo to Railway
2. Set all environment variables (see above)
3. Add a 1GB persistent volume at `/app/data`
4. Deploy

## Webhooks

Configure WAHA to send webhooks to:

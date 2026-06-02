import os, json, logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import anthropic
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
SHEET_ID       = os.environ["GOOGLE_SHEET_ID"]
GROUP_CHAT_ID  = int(os.environ["GROUP_CHAT_ID"])

# Maps Telegram first name / username → billing name
PARTNER_MAP = {
    "kenneth": "kenneth",
    "rea": "rea",
    "rea pintor": "rea",
    "ralph": "ralph",
    "ralph catipay": "ralph",
    "rrsc2594": "ralph",
    "rami": "rami",
    "ramihourani": "rami",
}

HOURLY_RATES = {
    "kenneth": 3000,
    "rea": 3000,
    "ralph": 3000,
    "rami": 3000,
}

SYSTEM_PROMPT = """
You are a billing parser for Catipay, Pintor, Varona & Hourani Law Office.
Extract billing info from a partner's casual message and return ONLY valid JSON.
JSON schema:
{
  "partner": "first name in lowercase (kenneth, rea, ralph, or rami) or null if unclear",
  "activity": "description of the work done",
  "hours": number or null,
  "billing_type": "retainer|appearance|acceptance|notarization|consultation|drafting|research|other",
  "is_billing": true or false
}
If the message is NOT a billing entry, return {"is_billing": false}.
Always return raw JSON only, no markdown, no explanation.
"""

def get_sheet():
    creds_json = json.loads(os.environ["GOOGLE_CREDS_JSON"])
    creds = Credentials.from_service_account_info(
        creds_json,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID).worksheet("Bot Log")

def parse_billing(text: str, sender_name: str) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    msg = f"Sender: {sender_name}\nMessage: {text}"
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": msg}]
    )
    raw = response.content[0].text.strip()
    logging.info(f"Claude raw response: {raw}")
    if not raw:
        return {"is_billing": False}
    clean = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)

def compute_splits(gross: float) -> dict:
    cost_fund = round(gross * 0.20, 2)
    net = round(gross * 0.80, 2)
    return {"gross": gross, "cost_fund": cost_fund, "net_80": net}

def resolve_partner(telegram_name: str, parsed_partner: str) -> str:
    # First try to resolve from Telegram name
    tg = telegram_name.lower().strip()
    if tg in PARTNER_MAP:
        return PARTNER_MAP[tg]
    # Fall back to what Claude parsed
    if parsed_partner and parsed_partner.lower() in HOURLY_RATES:
        return parsed_partner.lower()
    return tg

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"Received message from chat ID: {update.effective_chat.id}")
    logging.info(f"Message text: {update.message.text}")

    msg  = update.message.text
    user = update.effective_user
    sender = (user.first_name or user.username or "Unknown")

    try:
        data = parse_billing(msg, sender)
        logging.info(f"Parsed data: {data}")
    except Exception as e:
        logging.error(f"Parse error: {e}")
        await update.message.reply_text(f"Parse error: {e}")
        return

    if not data.get("is_billing"):
        logging.info("Not a billing message, ignoring.")
        return

    partner = resolve_partner(sender, data.get("partner", ""))
    hours = data.get("hours") or 0
    rate = HOURLY_RATES.get(partner, 3000)
    gross = round(hours * rate, 2)
    splits = compute_splits(gross)

    try:
        sheet = get_sheet()
        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            partner.title(),
            data.get("activity", ""),
            data.get("billing_type", ""),
            hours,
            rate,
            gross,
            splits["cost_fund"],
            splits["net_80"]
        ])
        sheet_status = "Logged to sheet."
    except Exception as e:
        logging.error(f"SHEET ERROR: {e}", exc_info=True)
        sheet_status = f"Sheet error: {e}"

    reply = (
        f"Billing entry logged\n"
        f"Partner: {partner.title()}\n"
        f"Activity: {data.get('activity')}\n"
        f"Hours: {hours}h @ PHP {rate:,.0f}/hr\n"
        f"Gross: PHP {gross:,.2f}\n"
        f"20% Cost Fund: PHP {splits['cost_fund']:,.2f}\n"
        f"Net (80%): PHP {splits['net_80']:,.2f}\n"
        f"{sheet_status}"
    )
    await update.message.reply_text(reply)

logging.basicConfig(level=logging.INFO)
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()

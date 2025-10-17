
import sys
import oci
import json
import os

# === CONFIGURATION ===
COMPARTMENT_ID = "ocid1.compartment.oc1..aaaaaaaagz67e5f36rxph7l6xuolm7toexy3ylresvl2ijpxefj76db57dvq"
CONFIG_PROFILE = "DEFAULT"
ENDPOINT = "https://inference.generativeai.eu-frankfurt-1.oci.oraclecloud.com"
MODEL_ID="ocid1.generativeaimodel.oc1.eu-frankfurt-1.amaaaaaask7dceyaaypm2hg4db3evqkmjfdli5mggcxrhp2i4qmhvggyb4ja"
#MODEL_ID = "ocid1.generativeaimodel.oc1.eu-frankfurt-1.amaaaaaask7dceyaeamxpkvjhthrqorbgbwlspi564yxfud6igdcdhdu2whq"

# === CHAT HISTORY CONFIG ===
HISTORY_FILE = "/tmp/mvp_chat_history.json"
MAX_HISTORY_LENGTH = 4  # including the system prompt

# === SYSTEM PROMPT (doctor persona) ===
SYSTEM_PROMPT = {
    "message": (
        "You are an experienced medical diagnostician with decades of clinical practice, focused on rare orphan deceases attributed to DNA. "
        "You are talking to your patient. "
        "Your task is to provide detailed diagnostic reasoning and recommendations based on "
        "the patient's complaint, a list of identified symptoms, and preliminary suspected conditions. "
        "Wherever possible, your assistant adds identified symptoms and correspondent candidate illnesses to the patient's description. "
        "Each candidate illnesses has percentage scorers: condition_coverage is how well identified symptoms are covered by the candidate illness, "
        "and illness_coverage is how well the candidate illness is covered by identified symptoms. "
        "The higher the score, the more important. And the higher condition coverage, the more important."
    ),
    "role": "CHATBOT"
}

# === INIT ===
config = oci.config.from_file('~/.oci/config', CONFIG_PROFILE)
client = oci.generative_ai_inference.GenerativeAiInferenceClient(
    config=config,
    service_endpoint=ENDPOINT,
    retry_strategy=oci.retry.NoneRetryStrategy(),
    timeout=(10, 240)
)

# === Load chat history if it exists ===
chat_history = []
if os.path.exists(HISTORY_FILE):
    try:
        with open(HISTORY_FILE, 'r') as f:
            raw = json.load(f)
            chat_history = raw
    except Exception as e:
        print(f"[mvp-ai] ⚠️ Failed to load chat history: {e}")

# === Ensure the system prompt is always the first message ===
if not chat_history or chat_history[0]["message"] != SYSTEM_PROMPT["message"]:
    chat_history = [SYSTEM_PROMPT] + [m for m in chat_history if m["role"] != "CHATBOT" or m["message"] != SYSTEM_PROMPT["message"]]

# === Get user message from command line ===
user_message = sys.argv[1]
chat_history.append({"message": user_message, "role": "USER"})

# === Prepare request ===
chat_request = oci.generative_ai_inference.models.CohereChatRequest()
chat_request.message = user_message
chat_request.chat_history = chat_history[-MAX_HISTORY_LENGTH:]
chat_request.max_tokens = 4000
chat_request.temperature = 0
chat_request.frequency_penalty = 0
chat_request.top_p = 0
chat_request.top_k = 0

chat_detail = oci.generative_ai_inference.models.ChatDetails()
chat_detail.compartment_id = COMPARTMENT_ID
chat_detail.serving_mode = oci.generative_ai_inference.models.OnDemandServingMode(model_id=MODEL_ID)
chat_detail.chat_request = chat_request

# === Execute inference ===
chat_response = client.chat(chat_detail)
chatbot_message = chat_response.data.chat_response.text.strip()

# === Display reply ===
print(chatbot_message)

# === Update and save history ===
chat_history.append({"message": chatbot_message, "role": "CHATBOT"})
try:
    with open(HISTORY_FILE, 'w') as f:
        json.dump(chat_history[-MAX_HISTORY_LENGTH:], f, indent=2)
except Exception as e:
    print(f"[mvp-ai] ⚠️ Failed to save chat history: {e}")


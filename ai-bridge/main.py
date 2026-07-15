"""
AI-SOC Bridge
-------------
Receives Wazuh alert webhooks, sends the alert to a local Ollama model
for triage analysis, and automatically files the result as a case in
TheHive.

Required environment variables:
  THEHIVE_API_KEY   - API key for a TheHive user with case-create rights
                       (see README: this must be a user in an org whose
                       profile includes manageCase/create -- the built-in
                       "admin" platform account does NOT have this)
  OLLAMA_URL         - default: http://localhost:11434/api/generate
  THEHIVE_URL         - default: http://localhost:9000/api/v1/case
  OLLAMA_MODEL        - default: llama3.2:3b

Run with:
  uvicorn main:app --host 0.0.0.0 --port 8000
"""

import os
import json
import requests
from fastapi import FastAPI, Request

app = FastAPI()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
THEHIVE_URL = os.getenv("THEHIVE_URL", "http://localhost:9000/api/v1/case")
THEHIVE_API_KEY = os.getenv("THEHIVE_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

if not THEHIVE_API_KEY:
    raise RuntimeError(
        "THEHIVE_API_KEY environment variable is not set. "
        "Export it before starting the service -- see README."
    )


def ask_ollama(alert_text: str) -> str:
    prompt = f"""You are a SOC analyst assistant. Analyze this security alert and provide:
1. A brief summary
2. Likely severity (Low/Medium/High/Critical)
3. Recommended next steps

Alert: {alert_text}
"""
    response = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=120,
    )
    return response.json().get("response", "No response from model")


def create_thehive_case(title: str, description: str):
    headers = {
        "Authorization": f"Bearer {THEHIVE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "title": title,
        "description": description,
        "severity": 2,
        "tlp": 2,
        "tags": ["ai-soc-lab", "auto-triage"],
    }
    # NOTE: verify=False because this lab uses a self-signed cert internally.
    # In production, use a real certificate and remove verify=False.
    resp = requests.post(THEHIVE_URL, headers=headers, json=payload, verify=False)
    return resp.status_code, resp.text


@app.post("/webhook/wazuh")
async def receive_wazuh_alert(request: Request):
    alert = await request.json()
    alert_text = json.dumps(alert, indent=2)

    ai_analysis = ask_ollama(alert_text)

    case_title = f"AI-Triage: {alert.get('rule', {}).get('description', 'Unknown Alert')}"
    status_code, resp_text = create_thehive_case(case_title, ai_analysis)

    return {
        "status": "processed",
        "ai_analysis": ai_analysis,
        "thehive_status": status_code,
    }


@app.get("/")
def health():
    return {"status": "AI-SOC bridge is running"}

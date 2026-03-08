"""
Master RAG Router
-----------------
Routes a user query to the correct Bedrock Knowledge Base (KB)
using a lightweight classifier LLM, then runs the *exact same*
retrieve_and_generate flow as the main chatbot.
"""

import os
import json
import boto3
from typing import Dict
from dotenv import load_dotenv

load_dotenv(override=True)

# ------------------------------------------------------------------
# AWS clients
# ------------------------------------------------------------------
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")

bedrock_runtime = boto3.client(
    "bedrock-runtime",
    region_name=AWS_REGION
)

bedrock_agent = boto3.client(
    "bedrock-agent-runtime",
    region_name=AWS_REGION
)

ROUTER_MODEL_ARN = os.getenv(
    "ROUTER_MODEL_ARN",
    # sensible default
    "arn:aws:bedrock:us-east-2:251132308857:inference-profile/us.anthropic.claude-3-haiku-20240307-v1:0"
)

# ------------------------------------------------------------------
# Knowledge base registry (IDs from env; see .env.example)
# ------------------------------------------------------------------
KB_REGISTRY = {
    "lwfa-sim": os.getenv("NEW_KB_LWFA_SIM_ID"),
    "lwfa-not-sim": os.getenv("NEW_KB_LWFA_NOT_SIM_ID"),
    "pwa": os.getenv("NEW_KB_PWA_ID"),
    "swa": os.getenv("NEW_KB_SWA_ID"),
}

DEFAULT_DOMAIN = "lwfa-sim"

# ------------------------------------------------------------------
# 1) Router classifier
# ------------------------------------------------------------------
def classify_query(question: str) -> str:
    """
    Returns ONE domain key: 'lwfa-sim', 'lwfa-not-sim', 'pwa', or 'swa'
    """

    prompt = f"""
You are a routing classifier for particle accelerator physics.

Choose the SINGLE most relevant domain for the question.

Domains:
- lwfa-sim: Laser wakefield acceleration (simulation) — PIC codes, simulations, computational methods, numerical modeling, code (e.g. WarpX, OSIRIS, EPOCH, Smilei)
- lwfa-not-sim: Laser wakefield acceleration (not simulation) — experimental LWFA, laser-plasma experiments, diagnostics, beam characterization, real-world LWFA
- pwa: Plasma wakefield acceleration — beam-driven PWA, plasma wakefield, PWFA (not laser-driven)
- swa: Structure wakefield acceleration — dielectric wakefield, metallic structures, DLWFA, structure-based acceleration

Return ONLY one of:
"lwfa-sim", "lwfa-not-sim", "pwa", or "swa"

Question:
{question}
"""

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
        "max_tokens": 16,
        "temperature": 0.0,
    }

    try:
        response = bedrock_runtime.invoke_model(
            modelId=ROUTER_MODEL_ARN,
            body=json.dumps(body).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )

        payload = json.loads(response["body"].read())
        text = payload["content"][0]["text"].strip().lower()

        if text in KB_REGISTRY:
            return text

    except Exception as e:
        print(f"[Router] Classification failed: {e}")

    # Safe fallback
    return DEFAULT_DOMAIN

def query_kb(
    kb_id: str,
    question: str,
    model_arn: str,
    result_limit: int = 10,
) -> Dict:
    """
    Runs Bedrock retrieve_and_generate against a specific KB.
    """

    response = bedrock_agent.retrieve_and_generate(
        input={"text": question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": kb_id,
                "modelArn": model_arn,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {
                        "numberOfResults": result_limit
                    }
                },
            },
        },
    )

    return response

# ------------------------------------------------------------------
# 3) Master entry point (drop-in replacement)
# ------------------------------------------------------------------
def run_master_query(
    question: str,
    model_arn: str,
    result_limit: int = 10,
) -> Dict:
    """
    - Routes question to correct KB
    - Executes retrieve_and_generate
    - Returns SAME response shape as main pipeline
    """

    domain = classify_query(question)
    kb_id = KB_REGISTRY.get(domain)

    if not kb_id:
        raise RuntimeError(f"No KB configured for domain '{domain}'")

    print(f"[Router] Routed to KB: {domain}")

    response = query_kb(
        kb_id=kb_id,
        question=question,
        model_arn=model_arn,
        result_limit=result_limit,
    )

    # Normalize output so main app doesn't care about routing
    return {
        "domain": domain,
        "kb_id": kb_id,
        "answer": response.get("output", {}).get("text", ""),
        "citations": response.get("citations", []),
        "raw_response": response,  # optional: useful for debugging
    }

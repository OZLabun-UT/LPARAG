"""
Master RAG Router
-----------------
Routes a user query to one or more domain-specific knowledge bases
(AI, Cosmology, LWFA) using a lightweight intent classifier LLM,
then queries the appropriate sub-agent(s) and merges results.
"""

import os
import boto3
from typing import List, Dict
import json
from dotenv import load_dotenv
load_dotenv(override=True)


AWS_REGION = os.getenv("AWS_REGION", "us-east-2")

ROUTER_MODEL_ARN = os.getenv(
    "ROUTER_MODEL_ARN",
    "arn:aws:bedrock:us-east-2:251132308857:inference-profile/us.anthropic.claude-sonnet-4-20250514-v1:0"
)

bedrock_runtime = boto3.client(
    "bedrock-runtime",
    region_name=AWS_REGION
)
# Knowledge base IDs (set in env)
KB_REGISTRY = {
    "ai": os.getenv("KB_AI_ID"),
    "cosmology": os.getenv("KB_COSMOLOGY_ID"),
    "lwfa": os.getenv("KB_LWFA_ID"),
}

bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
bedrock_runtime = boto3.client("bedrock-runtime", region_name=AWS_REGION)


def classify_query(question: str) -> list[str]:
    prompt = f"""
You are a routing classifier.

Decide which domains are relevant to the question.

Domains:
- ai: machine learning, LLMs, training, evaluation
- cosmology: CMB, inflation, dark matter, structure formation
- lwfa: laser wakefield acceleration, plasma physics, betatron radiation

Return ONLY a JSON array chosen from:
["ai", "cosmology", "lwfa"]

Question:
{question}
"""

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt}
                ]
            }
        ],
        "max_tokens": 64,
        "temperature": 0
    }

    response = bedrock_runtime.invoke_model(
        modelId=ROUTER_MODEL_ARN,
        body=json.dumps(body).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )

    payload = json.loads(response["body"].read())
    text = payload["content"][0]["text"]

    try:
        return json.loads(text)
    except Exception:
        return ["lwfa"]  # safe fallback


def query_kb(kb_id: str, question: str, model_arn: str, k: int = 8) -> str:
    resp = bedrock_agent.retrieve_and_generate(
        input={"text": question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": kb_id,
                "modelArn": model_arn,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {"numberOfResults": k}
                },
            },
        },
    )
    return resp.get("output", {}).get("text", "")


def run_master_query(question: str, model_arn: str) -> Dict:
    domains = classify_query(question)
    print("KB_REGISTRY =", KB_REGISTRY)


    results = {}
    for d in domains:
        kb_id = KB_REGISTRY.get(d)
        if not kb_id:
            continue
        results[d] = query_kb(kb_id, question, model_arn)

    return {
        "domains": domains,
        "answers": results,
    }

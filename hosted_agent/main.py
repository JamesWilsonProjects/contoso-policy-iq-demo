"""
Microsoft Foundry Hosted Agent (Responses Protocol) for Contoso Enterprise Policy Assistant.
Combines:
1. Pre-flight PII & PHI Guardrail Tool / Instructions
2. Foundry IQ (Azure AI Search Knowledge Base) retrieval tool (`search_policy_knowledge_base`)
3. Microsoft Agent Framework + FoundryChatClient + ResponsesHostServer
"""
import json
import os
import re
from typing import Annotated
import requests
from azure.identity import DefaultAzureCredential
from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from dotenv import load_dotenv

load_dotenv()

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "https://demo-newfoundry-srch-r5nb.search.windows.net")
KNOWLEDGE_BASE_NAME = os.getenv("KNOWLEDGE_BASE_NAME", "contoso-policy-kb")
KNOWLEDGE_SOURCE_NAME = os.getenv("KNOWLEDGE_SOURCE_NAME", "contoso-policy-ks")
SEARCH_API_VERSION = "2026-08-01-preview"

PII_PATTERNS = {
    "US_SSN": re.compile(r"\b(?!000|666|9\d{2})\d{3}[-\s]?(?!00)\d{2}[-\s]?(?!0000)\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    "PHONE_NUMBER": re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
    "EMAIL_ADDRESS": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "EMPLOYEE_ID": re.compile(r"\bEMP-\d{5,8}\b", re.IGNORECASE),
    "BANK_ROUTING_OR_ACCOUNT": re.compile(r"\b(?:routing|account)\s*(?:number|#|no\.?)?\s*[:#]?\s*\d{8,17}\b", re.IGNORECASE),
    "MEDICAL_DIAGNOSIS_PHI": re.compile(
        r"\b(?:diagnosed\s+with|diagnosis\s+of|ulcerative\s+colitis|lymphoma|chemotherapy|stage[-\s]?[1-4]\s+cancer|hiv\s+positive|bipolar\s+disorder|psychiatric\s+hospitalization)\b",
        re.IGNORECASE,
    ),
}


def scan_for_pii(
    user_query: Annotated[str, "The raw user query to scan for PII or PHI before policy lookup."]
) -> str:
    """Scans a user message for PII/PHI (SSN, Credit Card, Phone, Email, Employee ID, Bank Account, Medical Diagnosis)."""
    findings = []
    for pii_type, pattern in PII_PATTERNS.items():
        matches = pattern.findall(user_query)
        if matches:
            findings.append({"type": pii_type, "count": len(matches)})
    if findings:
        return json.dumps({
            "pii_detected": True,
            "findings": findings,
            "action": "BLOCK_AND_WARN",
            "message": "PII/PHI detected in user input. Do NOT process or repeat the PII. Refuse and instruct the user to remove personal identifiers per LEAVE-001 Section 5.3.",
        })
    return json.dumps({"pii_detected": False, "action": "ALLOW"})


def query_policy_knowledge_base(
    query: Annotated[str, "The policy question or search query to send to Foundry IQ (contoso-policy-kb)."]
) -> str:
    """Queries the Foundry IQ Knowledge Base (contoso-policy-kb) on Azure AI Search for grounded policy sections."""
    credential = DefaultAzureCredential()
    token = credential.get_token("https://search.azure.com/.default").token
    url = f"{SEARCH_ENDPOINT}/knowledgebases/{KNOWLEDGE_BASE_NAME}/retrieve?api-version={SEARCH_API_VERSION}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": query}],
            }
        ],
        "knowledgeSourceParams": [
            {
                "kind": "searchIndex",
                "knowledgeSourceName": KNOWLEDGE_SOURCE_NAME,
                "includeReferences": True,
                "includeReferenceSourceData": True,
            }
        ],
        "includeActivity": True,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=45)
    if not resp.ok:
        return json.dumps({"error": f"Knowledge base retrieve returned {resp.status_code}: {resp.text}"})
    data = resp.json()
    references = data.get("references", [])
    extracted = []
    for ref in references:
        src = ref.get("sourceData", {})
        extracted.append({
            "ref_id": ref.get("id"),
            "doc_id": src.get("doc_id"),
            "policy_title": src.get("policy_title"),
            "section_title": src.get("section_title"),
            "content": src.get("page_chunk"),
        })
    return json.dumps({
        "knowledge_base": KNOWLEDGE_BASE_NAME,
        "activity": data.get("activity", []),
        "retrieved_sections": extracted,
    })


INSTRUCTIONS = """You are the Contoso Enterprises Internal Employee Policy Assistant (Hosted Agent).
Follow these mandatory rules:
1. First call `scan_for_pii` on the user's message. If `pii_detected` is true, IMMEDIATELY stop, do NOT call `query_policy_knowledge_base`, do NOT repeat the user's sensitive numbers/diagnoses, and respond with a compliance alert explaining which PII/PHI category was blocked and asking them to re-submit their question without personal identifiers.
2. If no PII is detected, ALWAYS call `query_policy_knowledge_base` to retrieve official policy chunks from `contoso-policy-kb`.
3. Answer ONLY using facts, dollar caps, timelines, and approval chains explicitly stated in the retrieved chunks from the 10 Contoso policies (HR-001 to HR-005, LEAVE-001, TRV-001 to TRV-003, COMP-001).
4. NEVER infer, guess, or use external knowledge. If a topic is not in the retrieved chunks, state: "I cannot answer this question because it is not covered in the official Contoso Enterprises policy documents."
5. Cite every factual claim using `【PolicyID†Section】` (for example `【TRV-002†Section 3】`).
"""


def main():
    model_name = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME") or os.getenv("FOUNDRY_MODEL_NAME", "gpt-4.1")
    project_endpoint = os.getenv(
        "FOUNDRY_PROJECT_ENDPOINT",
        "https://demo-newfoundry-resource.services.ai.azure.com/api/projects/demo-newfoundry",
    )

    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model_name,
        credential=DefaultAzureCredential(),
    )

    agent = Agent(
        client=client,
        instructions=INSTRUCTIONS,
        tools=[scan_for_pii, query_policy_knowledge_base],
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    server.run()


if __name__ == "__main__":
    main()

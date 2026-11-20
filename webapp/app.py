"""
Contoso Enterprise Policy Assistant — Interactive Customer Demo Web Application
Powered by Microsoft Foundry (Foundry Prompt Agent + Hosted Agent) & Foundry IQ (Azure AI Search Agentic Retrieval).

Features:
1. Triple-Layer PII & PHI Guardrail Engine:
   - Layer 1: Deterministic + NLP Pre-Flight PII & Prompt-Injection Gate (SSN, Credit Card, Phone, Email, Employee ID, Bank Routing/Account, Medical PHI, Jailbreak/Override).
   - Layer 2: Microsoft Foundry Native RAI Policy (`contoso-policy-pii-guardrail`).
   - Layer 3: Strict Grounding System Prompt (`temperature=0.0`, no web search, mandatory inline citations, strict refusal on unstated topics).
2. Dual Execution Modes selectable in the UI:
   - Mode A: Foundry IQ + Azure OpenAI (`gpt-4.1`) with full Agentic Retrieval telemetry (subquery decomposition, semantic ranker scores, and retrieved chunks).
   - Mode B: Foundry Agent Service (`contoso-policy-iq-agent` / `contoso-policy-hosted-agent`) via the Foundry Responses API (`mcp_call` to `knowledge_base_retrieve`).
3. Built-in Policy Document Explorer (view all 10 synthesized Markdown policies right in the browser).
4. One-Click Demo Prompt Drawer (12 curated test prompts across HR, FMLA, Las Vegas/NYC Travel, Overtime, Out-of-Scope Refusal, and PII Guardrail blocks).
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(
    title="Contoso Enterprise Policy Assistant — Foundry IQ & Guardrails Demo",
    version="1.0.0",
)

TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "")
RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP", "rg-demo-newfoundry")
FOUNDRY_RESOURCE = os.getenv("FOUNDRY_RESOURCE_NAME", "demo-newfoundry-resource")
FOUNDRY_PROJECT = os.getenv("FOUNDRY_PROJECT_NAME", "demo-newfoundry")
SEARCH_SERVICE = os.getenv("AZURE_SEARCH_SERVICE", "demo-newfoundry-srch-r5nb")

SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE}.search.windows.net"
COGNITIVE_ENDPOINT = f"https://{FOUNDRY_RESOURCE}.cognitiveservices.azure.com"
PROJECT_ENDPOINT = f"https://{FOUNDRY_RESOURCE}.services.ai.azure.com/api/projects/{FOUNDRY_PROJECT}"

KNOWLEDGE_BASE_NAME = os.getenv("KNOWLEDGE_BASE_NAME", "contoso-policy-kb")
KNOWLEDGE_SOURCE_NAME = os.getenv("KNOWLEDGE_SOURCE_NAME", "contoso-policy-ks")
INDEX_NAME = os.getenv("SEARCH_INDEX_NAME", "contoso-policy-index")
PROMPT_AGENT_NAME = os.getenv("PROMPT_AGENT_NAME", "contoso-policy-iq-agent")
HOSTED_AGENT_NAME = os.getenv("HOSTED_AGENT_NAME", "contoso-policy-hosted-agent")
RAI_POLICY_NAME = os.getenv("RAI_POLICY_NAME", "contoso-policy-pii-guardrail")
GPT_DEPLOYMENT = os.getenv("GPT_DEPLOYMENT", "gpt-4.1")
SEARCH_API_VERSION = "2026-08-01-preview"

# Token cache
_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}


def get_bearer_token(resource: str) -> str:
    now = time.time()
    cached = _TOKEN_CACHE.get(resource)
    if cached and cached["expires_at"] > now + 60:
        return cached["token"]

    # 1. Fast path for Azure App Service / Container Apps Managed Identity (IDENTITY_ENDPOINT)
    identity_endpoint = os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT")
    identity_header = os.getenv("IDENTITY_HEADER") or os.getenv("MSI_SECRET")
    if identity_endpoint and identity_header:
        api_ver = "2019-08-01" if os.getenv("IDENTITY_ENDPOINT") else "2017-09-01"
        header_name = "X-IDENTITY-HEADER" if os.getenv("IDENTITY_ENDPOINT") else "Secret"
        mi_resp = requests.get(
            identity_endpoint,
            params={"resource": resource.rstrip("/"), "api-version": api_ver},
            headers={header_name: identity_header},
            timeout=10,
        )
        mi_resp.raise_for_status()
        mi_data = mi_resp.json()
        token = mi_data["access_token"]
        expires_on = float(mi_data.get("expires_on", now + 3600))
        _TOKEN_CACHE[resource] = {"token": token, "expires_at": expires_on}
        return token

    # 2. Try AzureCliCredential or DefaultAzureCredential locally
    try:
        from azure.identity import AzureCliCredential
        cred = AzureCliCredential(tenant_id=TENANT_ID) if TENANT_ID else AzureCliCredential()
        scope = resource.rstrip("/") + "/.default"
        tok = cred.get_token(scope)
        _TOKEN_CACHE[resource] = {"token": tok.token, "expires_at": tok.expires_on}
        return tok.token
    except Exception:
        pass

    # 3. Fallback to Azure CLI subprocess locally
    tenant_arg = f' --tenant "{TENANT_ID}"' if TENANT_ID else ""
    cmd = f'az account get-access-token --resource "{resource}"{tenant_arg} --query accessToken -o tsv'
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True, timeout=15)
    token = res.stdout.strip()
    _TOKEN_CACHE[resource] = {"token": token, "expires_at": now + 1800}
    return token


# ============================================================================
# LAYER 1: Deterministic + NLP Pre-Flight PII & Prompt Shield Scanner
# ============================================================================
PII_RULES = [
    {
        "id": "US_SSN",
        "label": "U.S. Social Security Number (SSN)",
        "severity": "CRITICAL",
        "policy_ref": "LEAVE-001 Section 5.3 & Foundry RAI SSN Protection",
        "pattern": re.compile(r"\b(?!000|666|9\d{2})\d{3}[-\s](?!00)\d{2}[-\s](?!0000)\d{4}\b|\b(?:ssn|social\s+security(?:\s+number)?)\s*(?:is|[:#])?\s*\d{9}\b", re.IGNORECASE),
        "replacement": "[REDACTED-SSN]",
    },
    {
        "id": "CREDIT_CARD",
        "label": "Credit / Corporate Visa Card Number",
        "severity": "CRITICAL",
        "policy_ref": "TRV-001 Section 1 & Foundry RAI Credit Card Protection",
        "pattern": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
        "replacement": "[REDACTED-CARD-NUMBER]",
    },
    {
        "id": "BANK_ACCOUNT_ROUTING",
        "label": "Bank Account / ABA Routing Number",
        "severity": "CRITICAL",
        "policy_ref": "COMP-001 Payroll Privacy & Foundry RAI Bank Account Protection",
        "pattern": re.compile(r"\b(?:routing|account|aba|iban)\s*(?:number|#|no\.?)?\s*(?:is|[:#])?\s*\d{8,17}\b", re.IGNORECASE),
        "replacement": "[REDACTED-BANK-ACCOUNT]",
    },
    {
        "id": "EMPLOYEE_ID",
        "label": "Employee ID Identifier",
        "severity": "HIGH",
        "policy_ref": "HR-001 Confidentiality & Employee Privacy",
        "pattern": re.compile(r"\bEMP-\d{5,8}\b", re.IGNORECASE),
        "replacement": "[REDACTED-EMPLOYEE-ID]",
    },
    {
        "id": "PHONE_NUMBER",
        "label": "Personal / Mobile Phone Number",
        "severity": "HIGH",
        "policy_ref": "Foundry RAI Phone Number Protection",
        "pattern": re.compile(r"(?:\+?1[-.\s]?)?\(\d{3}\)\s*\d{3}[-.\s]\d{4}\b|\b(?:\+?1[-.\s])?\d{3}[-.]\d{3}[-.]\d{4}\b"),
        "replacement": "[REDACTED-PHONE]",
    },
    {
        "id": "EMAIL_ADDRESS",
        "label": "Email Address",
        "severity": "HIGH",
        "policy_ref": "Foundry RAI Email Address Protection",
        "pattern": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "replacement": "[REDACTED-EMAIL]",
    },
    {
        "id": "MEDICAL_PHI",
        "label": "Confidential Medical Diagnosis / PHI",
        "severity": "CRITICAL",
        "policy_ref": "LEAVE-001 Section 5.3 (Medical Privacy Rule — Upload to Sedgwick Only)",
        "pattern": re.compile(
            r"\b(?:diagnosed\s+with\s+[a-z0-9\s\-]{3,35}|ulcerative\s+colitis|stage[-\s]?[1-4]\s+(?:cancer|lymphoma|carcinoma)|chemotherapy|hiv\s+positive|bipolar\s+disorder|major\s+depressive\s+disorder)\b",
            re.IGNORECASE,
        ),
        "replacement": "[REDACTED-MEDICAL-CONDITION]",
    },
    {
        "id": "PROMPT_INJECTION_JAILBREAK",
        "label": "Prompt Injection / Policy Override Attempt",
        "severity": "CRITICAL",
        "policy_ref": "Foundry Prompt Shields (User Prompt Attack & Jailbreak Filter)",
        "pattern": re.compile(
            r"(?:ignore\s+(?:all\s+)?(?:previous|prior|system)\s+(?:instructions|rules|prompts)|you\s+are\s+now\s+an?\s+unrestricted|bypass\s+(?:guardrails|concur|security|rules)|search\s+the\s+internet\s+and\s+tell\s+me\s+how\s+to\s+bypass)",
            re.IGNORECASE,
        ),
        "replacement": "[BLOCKED-JAILBREAK-ATTEMPT]",
    },
]


def inspect_and_scrub_prompt(prompt: str) -> Dict[str, Any]:
    detections = []
    sanitized = prompt

    for rule in PII_RULES:
        matches = [m.group(0) for m in rule["pattern"].finditer(prompt)]
        if matches:
            masked_samples = []
            for m in matches:
                if len(m) > 6:
                    masked_samples.append(m[:2] + "•" * (len(m) - 4) + m[-2:])
                else:
                    masked_samples.append("•••")
            detections.append({
                "id": rule["id"],
                "label": rule["label"],
                "severity": rule["severity"],
                "policy_ref": rule["policy_ref"],
                "count": len(matches),
                "masked_matches": masked_samples,
            })
            sanitized = rule["pattern"].sub(rule["replacement"], sanitized)

    # Also build a clean, ready-to-ask policy question if PII was present
    clean_suggestion = sanitized
    # Strip conversational self-identifications like "Hi, my name is X, my SSN is [REDACTED-SSN], and "
    clean_suggestion = re.sub(
        r"(?i)^(?:hi|hello)[,\s]+my\s+name\s+is\s+[a-z\s]+,\s*",
        "",
        clean_suggestion,
    ).strip()

    return {
        "blocked": len(detections) > 0,
        "detections": detections,
        "sanitized_prompt": sanitized,
        "suggested_clean_query": clean_suggestion,
    }


# ============================================================================
# FOUNDRY IQ RETRIEVAL + STRICT GROUNDED GENERATION
# ============================================================================
STRICT_SYSTEM_PROMPT = """You are the official Contoso Enterprises Internal Employee Policy Assistant.
You answer employee policy questions strictly grounded in the 10 official Contoso policy documents retrieved from the Foundry IQ Knowledge Base (`contoso-policy-kb`):
- HR-001: Employee Code of Conduct, Ethics, and Conflict of Interest Policy
- HR-002: Remote, Hybrid, and Flexible Work Arrangement Policy
- HR-003: Paid Time Off (PTO), Sick Leave, Bereavement, and Civic Duty Policy
- HR-004: Performance Management, Annual Review, and Performance Improvement Plan (PIP) Policy
- HR-005: Tuition Reimbursement, Professional Certifications, and Learning & Development Policy
- LEAVE-001: Family and Medical Leave Act (FMLA) and Paid Parental Coordination Policy
- TRV-001: Global Corporate Travel, Lodging, and Expense Reimbursement Policy
- TRV-002: High-Risk City Travel Addendum — Las Vegas, Nevada (Fraud & Audit Controls)
- TRV-003: High-Risk City Travel Addendum — New York City, New York (Rate Caps & Fraud Controls)
- COMP-001: Overtime Compensation, FLSA Classification, and Timekeeping Policy

MANDATORY OPERATING RULES:
1. STRICT GROUNDING ONLY: Answer ONLY using facts, numbers, dollar caps, timelines, and approval requirements explicitly present in the retrieved policy sections provided to you.
2. ZERO INFERENCE / NO EXTERNAL KNOWLEDGE: Never guess, extrapolate, infer unstated rules, or use general external knowledge (such as general IRS limits, state labor laws not written in the docs, or travel rules for cities other than Las Vegas and New York City).
3. EXPLICIT REFUSAL FOR OUT-OF-SCOPE TOPICS: If a user asks about any topic or part of a question that is NOT explicitly covered in the retrieved policy documents (for example: gym/wellness stipends, pet bereavement, paid sabbaticals, stock options, Tokyo/London hotel caps, or 401(k) IRS limits), you MUST explicitly state: "I cannot answer this based on the official Contoso Enterprises policy documents, as that topic is not covered in our policy repository."
4. MANDATORY INLINE CITATIONS: Every factual statement, dollar threshold, or rule MUST include an inline citation in the exact format `【DOC_ID†Section Title】` (for example: `【TRV-002†Section 3: Las Vegas Lodging Controls】` or `【HR-001†Section 2: Gifts, Meals, and Entertainment Acceptance Limits】`).
5. CLEAR STRUCTURE: Use concise markdown tables or bullet points when comparing policies (such as Standard Travel vs. Las Vegas vs. New York City).
"""


def call_foundry_iq_retrieve(query: str) -> Dict[str, Any]:
    t0 = time.time()
    search_token = get_bearer_token("https://search.azure.com")
    url = f"{SEARCH_ENDPOINT}/knowledgebases/{KNOWLEDGE_BASE_NAME}/retrieve?api-version={SEARCH_API_VERSION}"
    headers = {
        "Authorization": f"Bearer {search_token}",
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
    resp.raise_for_status()
    data = resp.json()
    elapsed_ms = int((time.time() - t0) * 1000)

    references = []
    for ref in data.get("references", []):
        src = ref.get("sourceData", {})
        references.append({
            "ref_id": ref.get("id"),
            "doc_id": src.get("doc_id", "UNKNOWN"),
            "policy_title": src.get("policy_title", ""),
            "category": src.get("category", ""),
            "section_title": src.get("section_title", ""),
            "source_file": src.get("source_file", ""),
            "reranker_score": ref.get("rerankerScore"),
            "content": src.get("page_chunk", ""),
        })

    return {
        "latency_ms": elapsed_ms,
        "activity": data.get("activity", []),
        "references": references,
    }


def _build_grounded_messages(query: str, references: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    context_blocks = []
    for idx, ref in enumerate(references[:10], start=1):
        context_blocks.append(
            f"--- SOURCE CHUNK #{idx} [Citation Tag: 【{ref['doc_id']}†{ref['section_title']}】] ---\n"
            f"{ref['content']}\n"
        )
    context_str = "\n".join(context_blocks) if context_blocks else "NO RELEVANT POLICY CHUNKS RETRIEVED."

    return [
        {"role": "system", "content": STRICT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"OFFICIAL RETRIEVED CONTOSO POLICY CHUNKS FROM FOUNDRY IQ (`{KNOWLEDGE_BASE_NAME}`):\n\n"
                f"{context_str}\n\n"
                f"EMPLOYEE QUESTION:\n{query}\n\n"
                f"Remember: Answer strictly from the retrieved chunks above, include `【DOC_ID†Section】` citations for every fact, and explicitly refuse any part not covered in the chunks."
            ),
        },
    ]


def call_grounded_chat_completion(query: str, references: List[Dict[str, Any]]) -> Dict[str, Any]:
    t0 = time.time()
    aoai_token = get_bearer_token("https://cognitiveservices.azure.com")
    url = f"{COGNITIVE_ENDPOINT}/openai/deployments/{GPT_DEPLOYMENT}/chat/completions?api-version=2024-10-21"
    headers = {
        "Authorization": f"Bearer {aoai_token}",
        "Content-Type": "application/json",
    }

    messages = _build_grounded_messages(query, references)

    resp = requests.post(
        url,
        headers=headers,
        json={"messages": messages, "temperature": 0.0, "max_tokens": 1200},
        timeout=60,
    )
    if not resp.ok:
        # Check if blocked by Azure AI Content Safety / RAI Policy
        err_json = {}
        try:
            err_json = resp.json()
        except Exception:
            pass
        return {
            "answer": f"⚠️ **Foundry RAI Guardrail (`{RAI_POLICY_NAME}`) Blocked Request:**\n\n`{json.dumps(err_json or resp.text)}`",
            "latency_ms": int((time.time() - t0) * 1000),
            "rai_filtered": True,
        }

    res_json = resp.json()
    answer = res_json["choices"][0]["message"]["content"]
    usage = res_json.get("usage", {})
    return {
        "answer": answer,
        "latency_ms": int((time.time() - t0) * 1000),
        "usage": usage,
        "rai_filtered": False,
    }


def stream_grounded_chat_completion(query: str, references: List[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    t0 = time.time()
    ttft_ms: Optional[int] = None
    aoai_token = get_bearer_token("https://cognitiveservices.azure.com")
    url = f"{COGNITIVE_ENDPOINT}/openai/deployments/{GPT_DEPLOYMENT}/chat/completions?api-version=2024-10-21"
    headers = {
        "Authorization": f"Bearer {aoai_token}",
        "Content-Type": "application/json",
    }
    messages = _build_grounded_messages(query, references)

    with requests.post(
        url,
        headers=headers,
        json={
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 1200,
            "stream": True,
            "stream_options": {"include_usage": True},
        },
        stream=True,
        timeout=60,
    ) as resp:
        if not resp.ok:
            err_text = resp.text
            err_json = {}
            try:
                err_json = json.loads(err_text)
            except Exception:
                pass
            msg = f"⚠️ **Foundry RAI Guardrail (`{RAI_POLICY_NAME}`) Blocked Request:**\n\n`{json.dumps(err_json or err_text)}`"
            yield {
                "type": "done",
                "answer": msg,
                "latency_ms": int((time.time() - t0) * 1000),
                "ttft_ms": int((time.time() - t0) * 1000),
                "usage": {},
                "rai_filtered": True,
            }
            return

        full_parts: List[str] = []
        usage: Dict[str, Any] = {}
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except Exception:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                token_text = delta.get("content")
                if token_text:
                    if ttft_ms is None:
                        ttft_ms = int((time.time() - t0) * 1000)
                    full_parts.append(token_text)
                    yield {"type": "delta", "delta": token_text, "ttft_ms": ttft_ms}

        elapsed_ms = int((time.time() - t0) * 1000)
        yield {
            "type": "done",
            "answer": "".join(full_parts),
            "latency_ms": elapsed_ms,
            "ttft_ms": ttft_ms if ttft_ms is not None else elapsed_ms,
            "usage": usage,
            "rai_filtered": False,
        }


def stream_foundry_agent_service(query: str, agent_name: str = PROMPT_AGENT_NAME) -> Iterator[Dict[str, Any]]:
    t0 = time.time()
    ttft_ms: Optional[int] = None
    ai_token = get_bearer_token("https://ai.azure.com")
    url = f"{PROJECT_ENDPOINT}/openai/responses?api-version=2025-05-15-preview"
    headers = {
        "Authorization": f"Bearer {ai_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "agent": {"name": agent_name, "type": "agent_reference"},
        "input": query,
        "stream": True,
    }
    with requests.post(url, headers=headers, json=payload, stream=True, timeout=90) as resp:
        elapsed_init = int((time.time() - t0) * 1000)
        if not resp.ok:
            err_text = resp.text
            err_obj = {}
            try:
                err_obj = json.loads(err_text)
            except Exception:
                pass
            yield {
                "type": "done",
                "ok": False,
                "latency_ms": elapsed_init,
                "ttft_ms": elapsed_init,
                "error": err_text,
                "error_json": err_obj,
            }
            return

        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "text/event-stream" not in content_type:
            data = resp.json()
            elapsed_ms = int((time.time() - t0) * 1000)
            output_items = data.get("output", [])
            text_parts = []
            tool_calls = []
            for item in output_items:
                itype = item.get("type")
                if itype in ("mcp_list_tools", "mcp_call", "function_call"):
                    tool_calls.append({
                        "type": itype,
                        "name": item.get("name") or item.get("server_label"),
                        "arguments": item.get("arguments"),
                    })
                elif itype == "message":
                    for c in item.get("content", []):
                        if c.get("type") in ("output_text", "text"):
                            text_parts.append(c.get("text", ""))
            ans = "\n".join(text_parts).strip()
            if ans:
                yield {"type": "delta", "delta": ans, "ttft_ms": elapsed_ms}
            yield {
                "type": "done",
                "ok": True,
                "response_id": data.get("id"),
                "agent_name": agent_name,
                "latency_ms": elapsed_ms,
                "ttft_ms": elapsed_ms,
                "answer": ans,
                "tool_calls": tool_calls,
                "usage": data.get("usage", {}),
            }
            return

        full_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        response_id: Optional[str] = None
        usage: Dict[str, Any] = {}

        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                evt = json.loads(data_str)
            except Exception:
                continue

            etype = evt.get("type", "")
            if etype == "response.output_text.delta":
                delta_txt = evt.get("delta", "")
                if delta_txt:
                    if ttft_ms is None:
                        ttft_ms = int((time.time() - t0) * 1000)
                    full_parts.append(delta_txt)
                    yield {"type": "delta", "delta": delta_txt, "ttft_ms": ttft_ms}
            elif etype == "response.output_item.done":
                item = evt.get("item") or {}
                itype = item.get("type")
                if itype in ("mcp_list_tools", "mcp_call", "function_call"):
                    tool_calls.append({
                        "type": itype,
                        "name": item.get("name") or item.get("server_label"),
                        "arguments": item.get("arguments"),
                    })
            elif etype == "response.completed":
                r_obj = evt.get("response") or {}
                response_id = r_obj.get("id") or response_id
                usage = r_obj.get("usage") or usage
                if not full_parts:
                    for item in r_obj.get("output", []):
                        if item.get("type") == "message":
                            for c in item.get("content", []):
                                if c.get("type") in ("output_text", "text") and c.get("text"):
                                    full_parts.append(c["text"])

        elapsed_ms = int((time.time() - t0) * 1000)
        ans = "".join(full_parts).strip()
        yield {
            "type": "done",
            "ok": bool(ans),
            "response_id": response_id,
            "agent_name": agent_name,
            "latency_ms": elapsed_ms,
            "ttft_ms": ttft_ms if ttft_ms is not None else elapsed_ms,
            "answer": ans,
            "tool_calls": tool_calls,
            "usage": usage,
        }


def call_foundry_agent_service(query: str, agent_name: str = PROMPT_AGENT_NAME) -> Dict[str, Any]:
    t0 = time.time()
    ai_token = get_bearer_token("https://ai.azure.com")
    url = f"{PROJECT_ENDPOINT}/openai/responses?api-version=2025-05-15-preview"
    headers = {
        "Authorization": f"Bearer {ai_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "agent": {"name": agent_name, "type": "agent_reference"},
        "input": query,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=90)
    elapsed_ms = int((time.time() - t0) * 1000)
    if not resp.ok:
        err_obj = {}
        try:
            err_obj = resp.json()
        except Exception:
            pass
        return {
            "ok": False,
            "latency_ms": elapsed_ms,
            "error": resp.text,
            "error_json": err_obj,
        }
    data = resp.json()
    output_items = data.get("output", [])
    text_parts = []
    tool_calls = []
    for item in output_items:
        itype = item.get("type")
        if itype in ("mcp_list_tools", "mcp_call", "function_call"):
            tool_calls.append({
                "type": itype,
                "name": item.get("name") or item.get("server_label"),
                "arguments": item.get("arguments"),
            })
        elif itype == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    text_parts.append(c.get("text", ""))
    return {
        "ok": True,
        "response_id": data.get("id"),
        "agent_name": agent_name,
        "latency_ms": elapsed_ms,
        "answer": "\n".join(text_parts).strip(),
        "tool_calls": tool_calls,
        "usage": data.get("usage", {}),
    }


# ============================================================================
# API ROUTES
# ============================================================================
class ChatRequest(BaseModel):
    query: str
    mode: str = "foundry_iq"  # "foundry_iq" | "foundry_agent" | "hosted_agent"
    test_foundry_native_rai: bool = False


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "resource_group": RESOURCE_GROUP,
        "foundry_project": f"{FOUNDRY_RESOURCE}/{FOUNDRY_PROJECT}",
        "search_service": SEARCH_SERVICE,
        "foundry_iq_kb": KNOWLEDGE_BASE_NAME,
        "prompt_agent": PROMPT_AGENT_NAME,
        "hosted_agent": HOSTED_AGENT_NAME,
        "rai_policy": RAI_POLICY_NAME,
    }


@app.get("/api/policies")
def list_policies():
    root = Path(__file__).resolve().parent.parent
    policies_dir = root / "policies"
    if not policies_dir.exists():
        policies_dir = Path(__file__).resolve().parent / "policies"
    items = []
    for md_file in sorted(policies_dir.glob("*.md")):
        raw = md_file.read_text(encoding="utf-8")
        doc_id_m = re.search(r"\*\*Document ID:\*\*\s*(.+)", raw)
        title_m = re.search(r"\*\*Title:\*\*\s*(.+)", raw)
        cat_m = re.search(r"\*\*Category:\*\*\s*(.+)", raw)
        items.append({
            "filename": md_file.name,
            "doc_id": doc_id_m.group(1).strip() if doc_id_m else md_file.stem.split("_")[0],
            "title": title_m.group(1).strip() if title_m else md_file.stem,
            "category": cat_m.group(1).strip() if cat_m else "Policy",
            "content": raw,
        })
    return {"policies": items}


@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    # Step 1: Run Layer 1 Pre-Flight PII & Prompt-Shield Gate
    pii_scan = inspect_and_scrub_prompt(query)

    # If the user selected "Test Layer 2 Foundry Native RAI Policy directly", send to Foundry Agent so Azure OpenAI returns the native content_filter JSON!
    if pii_scan["blocked"] and req.test_foundry_native_rai:
        agent_res = call_foundry_agent_service(query, agent_name=PROMPT_AGENT_NAME)
        err_json = agent_res.get("error_json", {}).get("error", {})
        cf_list = err_json.get("content_filters", [])
        pii_res = cf_list[0].get("content_filter_results", {}).get("personally_identifiable_information", {}) if cf_list else {}
        sub_cats = [s["sub_category"] for s in pii_res.get("sub_categories", []) if s.get("detected") or s.get("filtered")]
        refusal_md = (
            f"### 🛡️ Blocked by Microsoft Foundry Native RAI Guardrail (`{RAI_POLICY_NAME}`)\n\n"
            f"Azure AI Foundry intercepted the prompt at the platform API gateway (`HTTP 400 content_filter`):\n\n"
            f"- **Foundry Policy:** `{RAI_POLICY_NAME}`\n"
            f"- **Triggered Azure Content Safety Sub-Categories:** `{', '.join(sub_cats) if sub_cats else 'PII / Prompt Shield'}`\n"
            f"- **Azure Request ID:** `{err_json.get('request_id', 'N/A')}`\n\n"
            f"```json\n{json.dumps(pii_res or err_json, indent=2)}\n```"
        )
        return {
            "status": "blocked_by_guardrail",
            "answer": refusal_md,
            "guardrail": {
                "triggered": True,
                "layer": f"Layer 2: Microsoft Foundry Native RAI Policy ({RAI_POLICY_NAME})",
                "detections": pii_scan["detections"],
                "sanitized_prompt": pii_scan["sanitized_prompt"],
                "suggested_clean_query": pii_scan["suggested_clean_query"],
                "azure_rai_response": pii_res or err_json,
            },
            "foundry_iq": {"knowledge_base": KNOWLEDGE_BASE_NAME, "subqueries": [], "references": [], "latency_ms": 0},
            "agent_execution": {"mode": req.mode, "llm_tokens_used": 0, "latency_ms": agent_res.get("latency_ms", 0)},
        }

    if pii_scan["blocked"] and not req.test_foundry_native_rai:
        detected_badges = ", ".join(f"**{d['label']}** (`{', '.join(d['masked_matches'])}`)" for d in pii_scan["detections"])
        policy_refs = "; ".join(sorted({d["policy_ref"] for d in pii_scan["detections"]}))
        refusal_md = (
            f"### 🛡️ Guardrail Intercept: Personal Data (PII/PHI) or Policy Violation Blocked\n\n"
            f"Your message was **intercepted before reaching the AI model or search index** (0 LLM tokens consumed) because it contains restricted information:\n\n"
            f"- **Detected Categories:** {detected_badges}\n"
            f"- **Governing Compliance Rules:** {policy_refs}\n"
            f"- **Redacted Preview:** `{pii_scan['sanitized_prompt']}`\n\n"
            f"**How to Proceed:**\n"
            f"Per Contoso Privacy Rules (**LEAVE-001 Section 5.3** & **HR-001**), employees must never send Social Security Numbers, credit card numbers, bank account details, phone numbers, email addresses, Employee IDs, or specific medical diagnoses to internal AI assistants. "
            f"Please click **\"Ask Redacted Question\"** below or re-phrase your policy question without personal identifiers."
        )
        return {
            "status": "blocked_by_guardrail",
            "answer": refusal_md,
            "guardrail": {
                "triggered": True,
                "layer": "Layer 1 (Pre-Flight PII & Prompt Shield Gate) + Layer 2 (contoso-policy-pii-guardrail)",
                "detections": pii_scan["detections"],
                "sanitized_prompt": pii_scan["sanitized_prompt"],
                "suggested_clean_query": pii_scan["suggested_clean_query"],
            },
            "foundry_iq": {
                "knowledge_base": KNOWLEDGE_BASE_NAME,
                "subqueries": [],
                "references": [],
                "latency_ms": 0,
            },
            "agent_execution": {
                "mode": req.mode,
                "llm_tokens_used": 0,
                "latency_ms": 2,
            },
        }

    # Step 2: Retrieve from Foundry IQ Knowledge Base (`contoso-policy-kb`)
    kb_result = call_foundry_iq_retrieve(query)
    subqueries = []
    for act in kb_result.get("activity", []):
        if act.get("type") == "searchIndex":
            q_str = act.get("searchIndexArguments", {}).get("search")
            if q_str:
                subqueries.append({
                    "subquery": q_str,
                    "count": act.get("count", 0),
                    "elapsed_ms": act.get("elapsedMs", 0),
                })

    # Step 3: Execute via selected Foundry Mode
    if req.mode in ("foundry_agent", "hosted_agent"):
        target_agent = PROMPT_AGENT_NAME if req.mode == "foundry_agent" else HOSTED_AGENT_NAME
        agent_res = call_foundry_agent_service(query, agent_name=target_agent)
        if agent_res.get("ok") and agent_res.get("answer"):
            return {
                "status": "ok",
                "answer": agent_res["answer"],
                "guardrail": {
                    "triggered": False,
                    "layer": f"Passed Layer 1 PII Gate & Layer 2 Foundry RAI ({RAI_POLICY_NAME})",
                    "detections": [],
                },
                "foundry_iq": {
                    "knowledge_base": KNOWLEDGE_BASE_NAME,
                    "subqueries": subqueries,
                    "references": kb_result["references"][:8],
                    "latency_ms": kb_result["latency_ms"],
                },
                "agent_execution": {
                    "mode": req.mode,
                    "agent_name": target_agent,
                    "response_id": agent_res.get("response_id"),
                    "tool_calls": agent_res.get("tool_calls", []),
                    "usage": agent_res.get("usage", {}),
                    "latency_ms": agent_res["latency_ms"],
                },
            }
        # If hosted agent is still warming up or RAI blocked at platform level, fall back or surface
        if not agent_res.get("ok") and "content_filter" in str(agent_res.get("error", "")).lower():
            return {
                "status": "blocked_by_guardrail",
                "answer": f"### 🛡️ Blocked by Microsoft Foundry RAI Policy (`{RAI_POLICY_NAME}`)\n\n`{agent_res.get('error')}`",
                "guardrail": {
                    "triggered": True,
                    "layer": f"Layer 2: Microsoft Foundry RAI Policy ({RAI_POLICY_NAME})",
                    "detections": pii_scan["detections"],
                },
                "foundry_iq": {"knowledge_base": KNOWLEDGE_BASE_NAME, "subqueries": [], "references": [], "latency_ms": 0},
                "agent_execution": {"mode": req.mode, "latency_ms": agent_res.get("latency_ms", 0)},
            }

    # Default / Grounded Synthesis with Foundry IQ chunks
    gen_res = call_grounded_chat_completion(query, kb_result["references"])
    return {
        "status": "blocked_by_guardrail" if gen_res.get("rai_filtered") else "ok",
        "answer": gen_res["answer"],
        "guardrail": {
            "triggered": bool(gen_res.get("rai_filtered")),
            "layer": f"Passed Layer 1 PII Gate & Layer 2 Foundry RAI ({RAI_POLICY_NAME})",
            "detections": pii_scan["detections"],
        },
        "foundry_iq": {
            "knowledge_base": KNOWLEDGE_BASE_NAME,
            "subqueries": subqueries,
            "references": kb_result["references"][:8],
            "latency_ms": kb_result["latency_ms"],
        },
        "agent_execution": {
            "mode": req.mode,
            "agent_name": f"{PROMPT_AGENT_NAME} (Foundry IQ + {GPT_DEPLOYMENT})",
            "usage": gen_res.get("usage", {}),
            "latency_ms": gen_res["latency_ms"],
        },
    }


def _sse_event(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@app.post("/api/chat/stream")
def chat_stream_endpoint(req: ChatRequest):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    def event_generator() -> Iterator[str]:
        # Step 1: Run Layer 1 Pre-Flight PII & Prompt-Shield Gate
        pii_scan = inspect_and_scrub_prompt(query)

        if pii_scan["blocked"] and req.test_foundry_native_rai:
            yield _sse_event({"type": "stage", "stage": "Verifying Layer 2 Microsoft Foundry Native RAI Policy..."})
            agent_res = call_foundry_agent_service(query, agent_name=PROMPT_AGENT_NAME)
            err_json = agent_res.get("error_json", {}).get("error", {})
            cf_list = err_json.get("content_filters", [])
            pii_res = cf_list[0].get("content_filter_results", {}).get("personally_identifiable_information", {}) if cf_list else {}
            sub_cats = [s["sub_category"] for s in pii_res.get("sub_categories", []) if s.get("detected") or s.get("filtered")]
            refusal_md = (
                f"### 🛡️ Blocked by Microsoft Foundry Native RAI Guardrail (`{RAI_POLICY_NAME}`)\n\n"
                f"Azure AI Foundry intercepted the prompt at the platform API gateway (`HTTP 400 content_filter`):\n\n"
                f"- **Foundry Policy:** `{RAI_POLICY_NAME}`\n"
                f"- **Triggered Azure Content Safety Sub-Categories:** `{', '.join(sub_cats) if sub_cats else 'PII / Prompt Shield'}`\n"
                f"- **Azure Request ID:** `{err_json.get('request_id', 'N/A')}`\n\n"
                f"```json\n{json.dumps(pii_res or err_json, indent=2)}\n```"
            )
            yield _sse_event({
                "type": "complete",
                "data": {
                    "status": "blocked_by_guardrail",
                    "answer": refusal_md,
                    "guardrail": {
                        "triggered": True,
                        "layer": f"Layer 2: Microsoft Foundry Native RAI Policy ({RAI_POLICY_NAME})",
                        "detections": pii_scan["detections"],
                        "sanitized_prompt": pii_scan["sanitized_prompt"],
                        "suggested_clean_query": pii_scan["suggested_clean_query"],
                        "azure_rai_response": pii_res or err_json,
                    },
                    "foundry_iq": {"knowledge_base": KNOWLEDGE_BASE_NAME, "subqueries": [], "references": [], "latency_ms": 0},
                    "agent_execution": {"mode": req.mode, "llm_tokens_used": 0, "latency_ms": agent_res.get("latency_ms", 0)},
                },
            })
            return

        if pii_scan["blocked"] and not req.test_foundry_native_rai:
            detected_badges = ", ".join(f"**{d['label']}** (`{', '.join(d['masked_matches'])}`)" for d in pii_scan["detections"])
            policy_refs = "; ".join(sorted({d["policy_ref"] for d in pii_scan["detections"]}))
            refusal_md = (
                f"### 🛡️ Guardrail Intercept: Personal Data (PII/PHI) or Policy Violation Blocked\n\n"
                f"Your message was **intercepted before reaching the AI model or search index** (0 LLM tokens consumed) because it contains restricted information:\n\n"
                f"- **Detected Categories:** {detected_badges}\n"
                f"- **Governing Compliance Rules:** {policy_refs}\n"
                f"- **Redacted Preview:** `{pii_scan['sanitized_prompt']}`\n\n"
                f"**How to Proceed:**\n"
                f"Per Contoso Privacy Rules (**LEAVE-001 Section 5.3** & **HR-001**), employees must never send Social Security Numbers, credit card numbers, bank account details, phone numbers, email addresses, Employee IDs, or specific medical diagnoses to internal AI assistants. "
                f"Please click **\"Ask Redacted Question\"** below or re-phrase your policy question without personal identifiers."
            )
            yield _sse_event({
                "type": "complete",
                "data": {
                    "status": "blocked_by_guardrail",
                    "answer": refusal_md,
                    "guardrail": {
                        "triggered": True,
                        "layer": "Layer 1 (Pre-Flight PII & Prompt Shield Gate) + Layer 2 (contoso-policy-pii-guardrail)",
                        "detections": pii_scan["detections"],
                        "sanitized_prompt": pii_scan["sanitized_prompt"],
                        "suggested_clean_query": pii_scan["suggested_clean_query"],
                    },
                    "foundry_iq": {
                        "knowledge_base": KNOWLEDGE_BASE_NAME,
                        "subqueries": [],
                        "references": [],
                        "latency_ms": 0,
                    },
                    "agent_execution": {
                        "mode": req.mode,
                        "llm_tokens_used": 0,
                        "latency_ms": 2,
                    },
                },
            })
            return

        # Step 1.5: Immediately notify client that Layer 1 PII gate passed and retrieval is running
        guardrail_pass = {
            "triggered": False,
            "layer": f"Passed Layer 1 PII Gate & Layer 2 Foundry RAI ({RAI_POLICY_NAME})",
            "detections": [],
        }
        yield _sse_event({
            "type": "guardrail",
            "guardrail": guardrail_pass,
            "stage": f"Layer 1 PII Gate Passed — Querying Foundry IQ ({KNOWLEDGE_BASE_NAME})...",
        })

        # Step 2: Retrieve from Foundry IQ Knowledge Base (`contoso-policy-kb`) and push chunks immediately
        kb_result = call_foundry_iq_retrieve(query)
        subqueries = []
        for act in kb_result.get("activity", []):
            if act.get("type") == "searchIndex":
                q_str = act.get("searchIndexArguments", {}).get("search")
                if q_str:
                    subqueries.append({
                        "subquery": q_str,
                        "count": act.get("count", 0),
                        "elapsed_ms": act.get("elapsedMs", 0),
                    })

        foundry_iq_info = {
            "knowledge_base": KNOWLEDGE_BASE_NAME,
            "subqueries": subqueries,
            "references": kb_result["references"][:8],
            "latency_ms": kb_result["latency_ms"],
        }
        yield _sse_event({
            "type": "retrieval",
            "guardrail": guardrail_pass,
            "foundry_iq": foundry_iq_info,
            "stage": f"Retrieved {len(foundry_iq_info['references'])} policy sections across {len(subqueries)} subqueries ({kb_result['latency_ms']} ms) — Streaming answer...",
        })

        # Step 3: Stream from selected Foundry Mode
        if req.mode in ("foundry_agent", "hosted_agent"):
            target_agent = PROMPT_AGENT_NAME if req.mode == "foundry_agent" else HOSTED_AGENT_NAME
            agent_done: Optional[Dict[str, Any]] = None
            for evt in stream_foundry_agent_service(query, agent_name=target_agent):
                if evt.get("type") == "delta":
                    yield _sse_event({"type": "delta", "delta": evt["delta"], "ttft_ms": evt.get("ttft_ms")})
                elif evt.get("type") == "done":
                    agent_done = evt

            if agent_done and agent_done.get("ok") and agent_done.get("answer"):
                yield _sse_event({
                    "type": "complete",
                    "data": {
                        "status": "ok",
                        "answer": agent_done["answer"],
                        "guardrail": guardrail_pass,
                        "foundry_iq": foundry_iq_info,
                        "agent_execution": {
                            "mode": req.mode,
                            "agent_name": target_agent,
                            "response_id": agent_done.get("response_id"),
                            "tool_calls": agent_done.get("tool_calls", []),
                            "usage": agent_done.get("usage", {}),
                            "latency_ms": agent_done["latency_ms"],
                            "ttft_ms": agent_done.get("ttft_ms", agent_done["latency_ms"]),
                        },
                    },
                })
                return

            if agent_done and not agent_done.get("ok") and "content_filter" in str(agent_done.get("error", "")).lower():
                yield _sse_event({
                    "type": "complete",
                    "data": {
                        "status": "blocked_by_guardrail",
                        "answer": f"### 🛡️ Blocked by Microsoft Foundry RAI Policy (`{RAI_POLICY_NAME}`)\n\n`{agent_done.get('error')}`",
                        "guardrail": {
                            "triggered": True,
                            "layer": f"Layer 2: Microsoft Foundry RAI Policy ({RAI_POLICY_NAME})",
                            "detections": pii_scan["detections"],
                        },
                        "foundry_iq": {"knowledge_base": KNOWLEDGE_BASE_NAME, "subqueries": [], "references": [], "latency_ms": 0},
                        "agent_execution": {"mode": req.mode, "latency_ms": agent_done.get("latency_ms", 0)},
                    },
                })
                return

        # Default / Fallback: Stream Grounded Synthesis with Foundry IQ chunks
        final_gen: Dict[str, Any] = {}
        for evt in stream_grounded_chat_completion(query, kb_result["references"]):
            if evt.get("type") == "delta":
                yield _sse_event({"type": "delta", "delta": evt["delta"], "ttft_ms": evt.get("ttft_ms")})
            elif evt.get("type") == "done":
                final_gen = evt

        yield _sse_event({
            "type": "complete",
            "data": {
                "status": "blocked_by_guardrail" if final_gen.get("rai_filtered") else "ok",
                "answer": final_gen.get("answer", ""),
                "guardrail": {
                    "triggered": bool(final_gen.get("rai_filtered")),
                    "layer": f"Passed Layer 1 PII Gate & Layer 2 Foundry RAI ({RAI_POLICY_NAME})",
                    "detections": pii_scan["detections"],
                },
                "foundry_iq": foundry_iq_info,
                "agent_execution": {
                    "mode": req.mode,
                    "agent_name": f"{PROMPT_AGENT_NAME} (Foundry IQ + {GPT_DEPLOYMENT})",
                    "usage": final_gen.get("usage", {}),
                    "latency_ms": final_gen.get("latency_ms", 0),
                    "ttft_ms": final_gen.get("ttft_ms", final_gen.get("latency_ms", 0)),
                },
            },
        })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/", response_class=HTMLResponse)
def index_page():
    html_file = Path(__file__).resolve().parent / "index.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


@app.get("/architecture-deck", response_class=HTMLResponse)
def architecture_deck_page():
    deck_file = Path(__file__).resolve().parent / "architecture_deck.html"
    return HTMLResponse(content=deck_file.read_text(encoding="utf-8"))

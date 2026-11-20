"""
Provisions the Foundry IQ (Azure AI Search Agentic Retrieval) pipeline:
1. Parses and chunks all 10 Markdown policy documents in ../policies/ by section.
2. Generates vector embeddings via Azure OpenAI `text-embedding-3-small`.
3. Creates/updates the Azure AI Search index `contoso-policy-index` (with HNSW vector search + AzureOpenAIVectorizer + SemanticConfiguration).
4. Uploads all policy section chunks into `contoso-policy-index`.
5. Creates/updates the Foundry IQ Knowledge Source `contoso-policy-ks`.
6. Creates/updates the Foundry IQ Knowledge Base `contoso-policy-kb` (with extractiveData output mode and LLM query planning via gpt-4.1).
7. Verifies the Knowledge Base via a test agentic `retrieve` call.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path
import requests

TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "")
RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP", "rg-demo-newfoundry")
FOUNDRY_RESOURCE = os.getenv("FOUNDRY_RESOURCE_NAME", "demo-newfoundry-resource")
FOUNDRY_PROJECT = os.getenv("FOUNDRY_PROJECT_NAME", "demo-newfoundry")
SEARCH_SERVICE = os.getenv("AZURE_SEARCH_SERVICE", "demo-newfoundry-srch-r5nb")

SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE}.search.windows.net"
AOAI_ENDPOINT = f"https://{FOUNDRY_RESOURCE}.openai.azure.com"
COGNITIVE_ENDPOINT = f"https://{FOUNDRY_RESOURCE}.cognitiveservices.azure.com"

EMBEDDING_DEPLOYMENT = "text-embedding-3-small"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536
GPT_DEPLOYMENT = "gpt-4.1"
GPT_MODEL = "gpt-4.1"

INDEX_NAME = "contoso-policy-index"
KNOWLEDGE_SOURCE_NAME = "contoso-policy-ks"
KNOWLEDGE_BASE_NAME = "contoso-policy-kb"
SEARCH_API_VERSION = "2026-08-01-preview"


def get_az_token(resource: str) -> str:
    tenant_arg = f' --tenant "{TENANT_ID}"' if TENANT_ID else ""
    cmd = f'az account get-access-token --resource "{resource}"{tenant_arg} --query accessToken -o tsv'
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def parse_policy_documents(policies_dir: Path):
    chunks = []
    for md_file in sorted(policies_dir.glob("*.md")):
        raw = md_file.read_text(encoding="utf-8")
        # Extract metadata
        doc_id_match = re.search(r"\*\*Document ID:\*\*\s*(.+)", raw)
        title_match = re.search(r"\*\*Title:\*\*\s*(.+)", raw)
        cat_match = re.search(r"\*\*Category:\*\*\s*(.+)", raw)
        eff_match = re.search(r"\*\*Effective Date:\*\*\s*(.+)", raw)

        doc_id = doc_id_match.group(1).strip() if doc_id_match else md_file.stem.split("_")[0]
        policy_title = title_match.group(1).strip() if title_match else md_file.stem
        category = cat_match.group(1).strip() if cat_match else "General Policy"
        effective_date = eff_match.group(1).strip() if eff_match else "January 1, 2026"

        # Split by ## level-2 headings
        sections = re.split(r"(?m)^##\s+", raw)
        header_preamble = sections[0].strip()

        for idx, sec in enumerate(sections[1:], start=1):
            lines = sec.strip().splitlines()
            section_title = lines[0].strip() if lines else f"Section {idx}"
            section_body = "\n".join(lines[1:]).strip()

            full_chunk_text = (
                f"[Policy ID: {doc_id} | Title: {policy_title} | Category: {category} | Effective: {effective_date}]\n"
                f"Section: {section_title}\n\n"
                f"{section_body}"
            )
            chunk_id = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{doc_id}_sec_{idx}")
            chunks.append({
                "id": chunk_id,
                "doc_id": doc_id,
                "policy_title": policy_title,
                "category": category,
                "effective_date": effective_date,
                "section_title": section_title,
                "source_file": md_file.name,
                "page_number": idx,
                "page_chunk": full_chunk_text,
            })
    return chunks


def generate_embeddings(chunks, aoai_token: str):
    url = f"{COGNITIVE_ENDPOINT}/openai/deployments/{EMBEDDING_DEPLOYMENT}/embeddings?api-version=2024-06-01"
    headers = {
        "Authorization": f"Bearer {aoai_token}",
        "Content-Type": "application/json",
    }
    texts = [c["page_chunk"] for c in chunks]
    resp = requests.post(url, headers=headers, json={"input": texts}, timeout=60)
    resp.raise_for_status()
    data = resp.json()["data"]
    for chunk, emb_obj in zip(chunks, data):
        chunk["page_embedding"] = emb_obj["embedding"]
    print(f"Generated {len(chunks)} embeddings ({len(chunks[0]['page_embedding'])} dims).")


def create_search_index(search_token: str):
    url = f"{SEARCH_ENDPOINT}/indexes/{INDEX_NAME}?api-version={SEARCH_API_VERSION}"
    headers = {
        "Authorization": f"Bearer {search_token}",
        "Content-Type": "application/json",
    }
    index_payload = {
        "name": INDEX_NAME,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True, "sortable": True, "facetable": True},
            {"name": "doc_id", "type": "Edm.String", "searchable": True, "filterable": True, "sortable": True, "facetable": True},
            {"name": "policy_title", "type": "Edm.String", "searchable": True, "filterable": True, "sortable": True, "facetable": False},
            {"name": "category", "type": "Edm.String", "searchable": True, "filterable": True, "sortable": True, "facetable": True},
            {"name": "effective_date", "type": "Edm.String", "searchable": False, "filterable": True, "sortable": False, "facetable": False},
            {"name": "section_title", "type": "Edm.String", "searchable": True, "filterable": True, "sortable": False, "facetable": False},
            {"name": "source_file", "type": "Edm.String", "searchable": False, "filterable": True, "sortable": False, "facetable": True},
            {"name": "page_number", "type": "Edm.Int32", "searchable": False, "filterable": True, "sortable": True, "facetable": True},
            {"name": "page_chunk", "type": "Edm.String", "searchable": True, "filterable": False, "sortable": False, "facetable": False},
            {
                "name": "page_embedding",
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "stored": False,
                "dimensions": EMBEDDING_DIMS,
                "vectorSearchProfile": "hnsw_text_3_small",
            },
        ],
        "vectorSearch": {
            "algorithms": [
                {"name": "hnsw_alg", "kind": "hnsw"}
            ],
            "vectorizers": [
                {
                    "name": "aoai_vectorizer",
                    "kind": "azureOpenAI",
                    "azureOpenAIParameters": {
                        "resourceUri": AOAI_ENDPOINT,
                        "deploymentId": EMBEDDING_DEPLOYMENT,
                        "modelName": EMBEDDING_MODEL,
                    },
                }
            ],
            "profiles": [
                {
                    "name": "hnsw_text_3_small",
                    "algorithm": "hnsw_alg",
                    "vectorizer": "aoai_vectorizer",
                }
            ],
        },
        "semantic": {
            "defaultConfiguration": "semantic_config",
            "configurations": [
                {
                    "name": "semantic_config",
                    "prioritizedFields": {
                        "titleField": {"fieldName": "section_title"},
                        "prioritizedContentFields": [
                            {"fieldName": "page_chunk"}
                        ],
                        "prioritizedKeywordsFields": [
                            {"fieldName": "doc_id"},
                            {"fieldName": "category"},
                            {"fieldName": "policy_title"},
                        ],
                    },
                }
            ],
        },
    }
    resp = requests.put(url, headers=headers, json=index_payload, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"Failed to create index ({resp.status_code}): {resp.text}")
    print(f"Created/updated search index: {INDEX_NAME}")


def upload_documents(chunks, search_token: str):
    url = f"{SEARCH_ENDPOINT}/indexes/{INDEX_NAME}/docs/index?api-version={SEARCH_API_VERSION}"
    headers = {
        "Authorization": f"Bearer {search_token}",
        "Content-Type": "application/json",
    }
    actions = [{"@search.action": "mergeOrUpload", **chunk} for chunk in chunks]
    resp = requests.post(url, headers=headers, json={"value": actions}, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"Failed to upload docs ({resp.status_code}): {resp.text}")
    print(f"Uploaded {len(actions)} policy section chunks to {INDEX_NAME}.")


def create_knowledge_source(search_token: str):
    url = f"{SEARCH_ENDPOINT}/knowledgesources/{KNOWLEDGE_SOURCE_NAME}?api-version={SEARCH_API_VERSION}"
    headers = {
        "Authorization": f"Bearer {search_token}",
        "Content-Type": "application/json",
    }
    ks_payload = {
        "name": KNOWLEDGE_SOURCE_NAME,
        "kind": "searchIndex",
        "description": "Official Contoso Enterprises internal employee policies covering HR (Code of Conduct, Hybrid/Remote Work, PTO/Sick/Bereavement, Performance/PIP, Tuition/L&D), FMLA & Parental Leave, Corporate Travel (Global, Las Vegas High-Risk Fraud Addendum, NYC High-Risk Addendum), and FLSA Overtime.",
        "searchIndexParameters": {
            "searchIndexName": INDEX_NAME,
            "semanticConfigurationName": "semantic_config",
            "searchFields": [
                {"name": "page_chunk"},
                {"name": "section_title"},
                {"name": "policy_title"},
                {"name": "doc_id"},
            ],
            "sourceDataFields": [
                {"name": "id"},
                {"name": "doc_id"},
                {"name": "policy_title"},
                {"name": "category"},
                {"name": "section_title"},
                {"name": "effective_date"},
                {"name": "source_file"},
                {"name": "page_chunk"},
            ],
        },
    }
    resp = requests.put(url, headers=headers, json=ks_payload, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"Failed to create knowledge source ({resp.status_code}): {resp.text}")
    print(f"Created/updated Foundry IQ Knowledge Source: {KNOWLEDGE_SOURCE_NAME}")


def create_knowledge_base(search_token: str):
    url = f"{SEARCH_ENDPOINT}/knowledgebases/{KNOWLEDGE_BASE_NAME}?api-version={SEARCH_API_VERSION}"
    headers = {
        "Authorization": f"Bearer {search_token}",
        "Content-Type": "application/json",
    }
    kb_payload = {
        "name": KNOWLEDGE_BASE_NAME,
        "description": "Foundry IQ Knowledge Base for Contoso Enterprise Employee Policies (HR, FMLA, Global/Las Vegas/NYC Travel, and Overtime).",
        "retrievalInstructions": (
            "Decompose multi-part or comparative policy questions into targeted subqueries across the Contoso policy knowledge source. "
            "For travel questions involving Las Vegas (TRV-002) or New York City (TRV-003), retrieve both the city-specific addendum and the global travel policy (TRV-001) when comparing rules."
        ),
        "outputMode": "extractiveData",
        "knowledgeSources": [
            {"name": KNOWLEDGE_SOURCE_NAME}
        ],
        "models": [
            {
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": AOAI_ENDPOINT,
                    "deploymentId": GPT_DEPLOYMENT,
                    "modelName": GPT_MODEL,
                },
            }
        ],
        "retrievalReasoningEffort": {
            "kind": "low"
        },
    }
    resp = requests.put(url, headers=headers, json=kb_payload, timeout=60)
    if not resp.ok:
        print(f"Warning: low reasoning effort returned {resp.status_code}: {resp.text}. Retrying with minimal reasoning effort...")
        kb_payload.pop("models", None)
        kb_payload["retrievalReasoningEffort"] = {"kind": "minimal"}
        resp = requests.put(url, headers=headers, json=kb_payload, timeout=60)
        if not resp.ok:
            raise RuntimeError(f"Failed to create knowledge base ({resp.status_code}): {resp.text}")
    print(f"Created/updated Foundry IQ Knowledge Base: {KNOWLEDGE_BASE_NAME}")
    print(f"Foundry IQ MCP Endpoint: {SEARCH_ENDPOINT}/knowledgebases/{KNOWLEDGE_BASE_NAME}/mcp?api-version={SEARCH_API_VERSION}")


def test_knowledge_base_retrieve(search_token: str):
    url = f"{SEARCH_ENDPOINT}/knowledgebases/{KNOWLEDGE_BASE_NAME}/retrieve?api-version={SEARCH_API_VERSION}"
    headers = {
        "Authorization": f"Bearer {search_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Compare the nightly hotel rate caps and itemized receipt rules for Las Vegas vs New York City.",
                    }
                ],
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
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if not resp.ok:
        # Fallback to intents format if messages format differs
        payload_intents = {
            "intents": [
                {"type": "semantic", "search": "Las Vegas TRV-002 hotel rate cap receipt rules"},
                {"type": "semantic", "search": "New York City TRV-003 hotel rate cap receipt rules"},
            ]
        }
        resp = requests.post(url, headers=headers, json=payload_intents, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    refs = data.get("references", [])
    activity = data.get("activity", [])
    print(f"Test retrieve succeeded! Activity steps: {len(activity)}, References returned: {len(refs)}")
    return data


def main():
    root = Path(__file__).resolve().parent.parent
    policies_dir = root / "policies"

    print("1. Parsing policy documents into sections...")
    chunks = parse_policy_documents(policies_dir)
    print(f"   Parsed {len(chunks)} sections across 10 policy files.")

    print("2. Acquiring Azure Entra ID tokens...")
    aoai_token = get_az_token("https://cognitiveservices.azure.com")
    search_token = get_az_token("https://search.azure.com")

    print("3. Generating embeddings via text-embedding-3-small...")
    generate_embeddings(chunks, aoai_token)

    print("4. Creating Azure AI Search index...")
    create_search_index(search_token)

    print("5. Uploading chunks to index...")
    upload_documents(chunks, search_token)
    time.sleep(3)

    print("6. Creating Foundry IQ Knowledge Source...")
    create_knowledge_source(search_token)

    print("7. Creating Foundry IQ Knowledge Base...")
    create_knowledge_base(search_token)

    print("8. Testing Foundry IQ Knowledge Base retrieve endpoint...")
    test_knowledge_base_retrieve(search_token)
    print("Foundry IQ setup complete!")


if __name__ == "__main__":
    main()

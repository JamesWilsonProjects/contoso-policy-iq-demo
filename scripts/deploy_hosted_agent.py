"""
Packages and deploys `contoso-policy-hosted-agent` (Foundry Code-Based Hosted Agent)
to `demo-newfoundry` via the Microsoft Foundry Agents REST API (`multipart/form-data`),
attaches the `contoso-policy-pii-guardrail` RAI policy, and assigns RBAC roles to its
managed identity so it can query Foundry IQ (`contoso-policy-kb`) and `gpt-4.1`.
"""
import hashlib
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path
import requests

TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "")
RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP", "rg-demo-newfoundry")
FOUNDRY_RESOURCE = os.getenv("FOUNDRY_RESOURCE_NAME", "demo-newfoundry-resource")
FOUNDRY_PROJECT = os.getenv("FOUNDRY_PROJECT_NAME", "demo-newfoundry")
SEARCH_SERVICE = os.getenv("AZURE_SEARCH_SERVICE", "demo-newfoundry-srch-r5nb")

PROJECT_ENDPOINT = f"https://{FOUNDRY_RESOURCE}.services.ai.azure.com/api/projects/{FOUNDRY_PROJECT}"
HOSTED_AGENT_NAME = "contoso-policy-hosted-agent"
RAI_POLICY_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.CognitiveServices/accounts/{FOUNDRY_RESOURCE}"
    f"/raiPolicies/contoso-policy-pii-guardrail"
)


def get_az_token(resource: str) -> str:
    tenant_arg = f' --tenant "{TENANT_ID}"' if TENANT_ID else ""
    cmd = f'az account get-access-token --resource "{resource}"{tenant_arg} --query accessToken -o tsv'
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def build_zip(hosted_dir: Path, zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(hosted_dir / "main.py", "main.py")
        zf.write(hosted_dir / "requirements.txt", "requirements.txt")
    sha256_hex = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return sha256_hex


def deploy_hosted_agent():
    root = Path(__file__).resolve().parent.parent
    hosted_dir = root / "hosted_agent"
    zip_path = hosted_dir / f"{HOSTED_AGENT_NAME}.zip"
    sha256_hex = build_zip(hosted_dir, zip_path)
    print(f"Built {zip_path.name} (SHA-256: {sha256_hex})")

    metadata = {
        "description": "Contoso Enterprise Policy Hosted Agent (Python 3.13 code deployment with pre-flight PII guardrail + Foundry IQ retrieval).",
        "definition": {
            "kind": "hosted",
            "protocol_versions": [
                {"protocol": "responses", "version": "2.0.0"}
            ],
            "cpu": "1",
            "memory": "2Gi",
            "rai_config": {
                "rai_policy_name": RAI_POLICY_ID
            },
            "code_configuration": {
                "runtime": "python_3_13",
                "entry_point": ["python", "main.py"],
                "dependency_resolution": "remote_build",
            },
            "environment_variables": {
                "FOUNDRY_PROJECT_ENDPOINT": PROJECT_ENDPOINT,
                "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-4.1",
                "AZURE_SEARCH_ENDPOINT": f"https://{SEARCH_SERVICE}.search.windows.net",
                "KNOWLEDGE_BASE_NAME": "contoso-policy-kb",
                "KNOWLEDGE_SOURCE_NAME": "contoso-policy-ks",
                "PYTHONNOUSERSITE": "1",
            },
        },
    }

    token = get_az_token("https://ai.azure.com")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "x-ms-agent-name": HOSTED_AGENT_NAME,
        "x-ms-code-zip-sha256": sha256_hex,
    }

    files = {
        "metadata": ("metadata.json", json.dumps(metadata), "application/json"),
        "code": (f"{HOSTED_AGENT_NAME}.zip", zip_path.read_bytes(), "application/zip"),
    }

    url = f"{PROJECT_ENDPOINT}/agents?api-version=v1"
    resp = requests.post(url, headers=headers, files=files, timeout=120)
    if resp.status_code in (400, 409) and "already exists" in resp.text.lower():
        headers.pop("x-ms-agent-name", None)
        url = f"{PROJECT_ENDPOINT}/agents/{HOSTED_AGENT_NAME}?api-version=v1"
        resp = requests.post(url, headers=headers, files=files, timeout=120)

    if not resp.ok:
        raise RuntimeError(f"Failed to deploy hosted agent ({resp.status_code}): {resp.text}")

    data = resp.json()
    latest = data.get("versions", {}).get("latest", data)
    version = latest.get("version", "1")
    status = latest.get("status", "creating")
    principal_id = (
        data.get("instance_identity", {}).get("principal_id")
        or latest.get("instance_identity", {}).get("principal_id")
    )
    print(f"Deployed {HOSTED_AGENT_NAME} version {version} (initial status: {status}, principal_id: {principal_id})")

    if principal_id:
        print(f"Granting Search Index Data Reader and Cognitive Services User to hosted agent identity {principal_id}...")
        subprocess.run(
            f'az role assignment create --subscription "{SUBSCRIPTION_ID}" --assignee-object-id "{principal_id}" '
            f'--assignee-principal-type ServicePrincipal --role "1407120a-92aa-4202-b7e9-c0e197c71c8f" '
            f'--scope "/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.Search/searchServices/{SEARCH_SERVICE}" -o none',
            shell=True,
        )
        subprocess.run(
            f'az role assignment create --subscription "{SUBSCRIPTION_ID}" --assignee-object-id "{principal_id}" '
            f'--assignee-principal-type ServicePrincipal --role "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd" '
            f'--scope "/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/{FOUNDRY_RESOURCE}" -o none',
            shell=True,
        )
        subprocess.run(
            f'az role assignment create --subscription "{SUBSCRIPTION_ID}" --assignee-object-id "{principal_id}" '
            f'--assignee-principal-type ServicePrincipal --role "a97b65f3-24c7-4388-baec-2e87135dc908" '
            f'--scope "/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/{FOUNDRY_RESOURCE}" -o none',
            shell=True,
        )

    # Poll for active
    for attempt in range(36):
        time.sleep(5)
        token = get_az_token("https://ai.azure.com")
        v_url = f"{PROJECT_ENDPOINT}/agents/{HOSTED_AGENT_NAME}/versions/{version}?api-version=v1"
        v_resp = requests.get(v_url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if v_resp.ok:
            v_data = v_resp.json()
            v_status = v_data.get("status")
            print(f"  [{attempt + 1}/36] Hosted agent version {version} status: {v_status}")
            if v_status == "active":
                print(f"Hosted agent {HOSTED_AGENT_NAME}:{version} is ACTIVE!")
                break
            if v_status == "failed":
                print(f"Hosted agent build details: {json.dumps(v_data.get('error'), indent=2)}")
                break


if __name__ == "__main__":
    deploy_hosted_agent()

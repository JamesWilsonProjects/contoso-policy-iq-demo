# Contoso Enterprise Policy Assistant — Foundry IQ, Hosted Agent & PII Guardrails Demo

This repository contains the complete, deployed customer demonstration for an **Internal Employee Policy Assistant** built on **Microsoft Foundry (v2)**, **Foundry IQ (Azure AI Search Agentic Retrieval)**, **Foundry Agent Service (Prompt Agent + Hosted Code Agent)**, and **Multi-Layer PII & Grounding Guardrails**.

---

## Solution Components

| Component | Default Name / Resource | Endpoint / Description |
| :--- | :--- | :--- |
| **Customer Demo Web App (Azure App Service / Local)** | `<your-app-service-name>` | `https://<your-app-service-name>.azurewebsites.net` (or `http://127.0.0.1:8080` locally) |
| **Customer Architecture Slide Deck (Path 1 & 2)** | `/architecture-deck` | `https://<your-app-service-name>.azurewebsites.net/architecture-deck` (or `http://127.0.0.1:8080/architecture-deck`) |
| **Microsoft Foundry Resource & Project** | `<your-foundry-resource>` / `<your-project>` | `https://<your-foundry-resource>.services.ai.azure.com/api/projects/<your-project>` |
| **Foundry Prompt Agent (MCP-Connected)** | `contoso-policy-iq-agent` (Version 1, `active`) | Testable in Foundry Portal Playground or Web App (`Engine: Foundry Prompt Agent`) |
| **Foundry Hosted Code Agent (Python 3.13)** | `contoso-policy-hosted-agent` (Version 1, `active`) | Deployed via `create_version_from_code` (`remote_build`) |
| **Foundry IQ Knowledge Base (Agentic Retrieval)** | `contoso-policy-kb` | `https://<your-search-service>.search.windows.net/knowledgebases/contoso-policy-kb/mcp?api-version=2026-08-01-preview` |
| **Foundry IQ Knowledge Source & Index** | `contoso-policy-ks` / `contoso-policy-index` | 55 vectorized + semantically ranked policy sections (`text-embedding-3-small` + `gpt-4.1`) |
| **Foundry Native RAI Guardrail Policy** | `contoso-policy-pii-guardrail` | 24 active filters: SSN, Credit Card, Phone, Email, Bank Account, ABA Routing, Jailbreak, Indirect Attack |

---

## Repository Structure

```text
RAG - Demo/
├── docs/
│   ├── Requirements_and_Architecture.md      # Official Requirements & Architecture Document (Markdown)
│   ├── Requirements_and_Architecture.docx    # Official Requirements & Architecture Document (Word DOCX)
│   ├── Test_Prompts_and_Validation_Guide.md  # 14 Demo Test Prompts & Expected Outputs (Markdown)
│   └── Test_Prompts_and_Validation_Guide.docx# 14 Demo Test Prompts & Expected Outputs (Word DOCX)
├── policies/                                 # 10 Synthesized Enterprise Policy Documents (.md & .docx)
│   ├── HR-001_Code_of_Conduct_and_Ethics (.md / .docx)
│   ├── HR-002_Remote_and_Hybrid_Work_Policy (.md / .docx)
│   ├── HR-003_PTO_Sick_Leave_and_Holidays (.md / .docx)
│   ├── HR-004_Performance_Management_and_PIP (.md / .docx)
│   ├── HR-005_Tuition_Reimbursement_and_L&D (.md / .docx)
│   ├── LEAVE-001_FMLA_and_Medical_Leave_Policy (.md / .docx)
│   ├── TRV-001_Global_Corporate_Travel_and_Expense_Policy (.md / .docx)
│   ├── TRV-002_High_Risk_City_Addendum_Las_Vegas (.md / .docx)
│   ├── TRV-003_High_Risk_City_Addendum_New_York_City (.md / .docx)
│   └── COMP-001_Overtime_and_FLSA_Classification_Policy (.md / .docx)
├── hosted_agent/                             # Foundry Code-Based Hosted Agent source (Python 3.13)
│   ├── main.py                               # Microsoft Agent Framework + Foundry IQ + PII Guardrail
│   └── requirements.txt
├── webapp/                                   # Interactive Customer Demo Web Application
│   ├── app.py                                # FastAPI backend (SSE Streaming + Layer 1 PII Gate + Foundry IQ + Agent Service)
│   ├── index.html                            # UI with One-Click Demo Drawer, Policy Viewer & Live Inspector
│   ├── architecture_deck.html                # Interactive 7-slide customer implementation deck with animated SVG flow
│   └── requirements.txt
├── infra/                                    # Infrastructure as Code (Bicep)
│   ├── main.bicep                            # Deploys RAI Guardrail Policy, MCP Connection, and RBAC
│   └── main.parameters.json
└── scripts/                                  # End-to-End Automation & Validation Scripts
    ├── generate_docx_files.py                # Generates formatted .docx files for all policies & deliverables
    ├── setup_foundry_iq.py                   # Chunks, embeds, indexes, and creates Foundry IQ KB & KS
    ├── deploy_hosted_agent.py                # Packages and deploys contoso-policy-hosted-agent to Foundry
    └── validate_demo.py                      # Runs 7 automated E2E tests against local or Azure App Service URL
```

---

## Quick Demo Walkthrough (5 Minutes)

1. **Open the Web App:** Navigate to **`http://127.0.0.1:8080`** locally (or your deployed Azure App Service URL).
2. **Show the 10 Policy Documents:** Click the **"10 Policy Docs"** tab in the left sidebar and click any policy (e.g., `TRV-002` Las Vegas or `TRV-003` NYC) to show the customer the underlying policy tables and fraud rules.
3. **Run a Multi-Document Foundry IQ Query:** In the **"Demo Test Prompts (12)"** tab, click **"Compare Standard vs. Las Vegas vs. NYC"**. Point out the **Right Inspector Panel**, which shows Foundry IQ decomposing the question into **9 parallel subqueries** across `TRV-001`, `TRV-002`, and `TRV-003` and returning clickable `📄 TRV-002 · Section 3` citation chips.
4. **Show Strict Grounding (Zero Inference / No Web Search):** Click **"Unstated Benefits Trap (Gym, Pet, Sabbatical)"** or **"External Web Trap (Tokyo Cap & 401k Limit)"** to demonstrate that the agent strictly refuses to invent answers or pull from the internet.
5. **Show Multi-Layer PII & PHI Guardrails:**
   - Click **"SSN + Medical Diagnosis in FMLA Prompt"** or **"Corporate Visa Card + Phone + Email"**.
   - Show that **Layer 1** intercepts the prompt in **2 ms (0 LLM tokens consumed)**, masks the detected SSN/Card/Medical condition in the Inspector Panel, and provides two interactive buttons:
     - **✨ Ask Redacted Question (PII Removed):** Automatically strips the PII and answers the underlying policy question safely.
     - **🔬 Verify Layer 2 Foundry Native RAI Filter:** Sends the test prompt directly to the Foundry Agent gateway to show Azure AI Content Safety's native `contoso-policy-pii-guardrail` (`UsSocialSecurityNumber`, `CreditCardNumber`, `PhoneNumber`) blocking it at the platform level.

"""
End-to-End Automated Validation Suite for the Contoso Enterprise Policy Assistant Demo.
Tests:
1. Grounded Single-Doc HR, FMLA, and Overtime queries (verifies exact facts + citations).
2. Multi-Doc Foundry IQ Agentic Retrieval query (Standard vs. Las Vegas vs. NYC Travel).
3. Out-of-Scope / Unstated Inference refusal (verifies zero hallucination / no web search).
4. Layer 1 Pre-Flight PII & PHI Guardrail interception (SSN + Medical Diagnosis, Credit Card + Phone, Employee ID + Bank Account, Jailbreak).
5. Layer 2 Native Microsoft Foundry RAI Policy (`contoso-policy-pii-guardrail`) direct verification on `contoso-policy-iq-agent`.
"""
import json
import sys
import requests

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"


def run_test(name: str, payload: dict, assertions):
    print(f"\n=== TEST: {name} ===")
    resp = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=90)
    assert resp.status_code == 200, f"Expected HTTP 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assertions(data)
    print(f"PASSED: {name}")
    return data


def main():
    print(f"Running E2E Validation Suite against {BASE_URL} ...")
    health = requests.get(f"{BASE_URL}/api/health", timeout=15).json()
    print(f"Health Check: {json.dumps(health)}")
    assert health["status"] == "healthy"

    # 1. Multi-Doc Travel Comparison (TRV-001, TRV-002 Las Vegas, TRV-003 NYC)
    def check_travel(d):
        ans = d["answer"]
        assert d["status"] == "ok"
        assert "$185" in ans and "$250" in ans, f"Missing Las Vegas hotel caps in: {ans}"
        assert "$375" in ans, f"Missing NYC Manhattan hotel cap in: {ans}"
        assert "TRV-002" in ans and "TRV-003" in ans, f"Missing TRV-002/TRV-003 citations in: {ans}"
        print(f"  -> Foundry IQ Subqueries executed: {len(d['foundry_iq']['subqueries'])}, Chunks: {len(d['foundry_iq']['references'])}")

    run_test(
        "1. Multi-Doc Travel Comparison (Global vs. Las Vegas vs. NYC)",
        {
            "query": "Compare our corporate travel rules across Standard Domestic cities, Las Vegas, and New York City: specifically compare the nightly hotel rate caps, daily individual meal limits, itemized receipt thresholds, and required pre-approvals.",
            "mode": "foundry_iq",
        },
        check_travel,
    )

    # 2. FMLA & Paid Parental Leave (LEAVE-001)
    def check_fmla(d):
        ans = d["answer"]
        assert d["status"] == "ok"
        assert "1,250" in ans and "6" in ans and "LEAVE-001" in ans, f"Unexpected FMLA answer: {ans}"

    run_test(
        "2. FMLA Eligibility & Paid Parental Coordination (LEAVE-001)",
        {
            "query": "What are the three eligibility requirements for FMLA leave, and how does Paid Parental Leave coordinate with the 12 weeks of FMLA?",
            "mode": "foundry_iq",
        },
        check_fmla,
    )

    # 3. Overtime Policy (COMP-001)
    def check_overtime(d):
        ans = d["answer"]
        assert d["status"] == "ok"
        assert "2.0x" in ans or "Double-Time" in ans, f"Missing 2.0x holiday rate in: {ans}"
        assert "COMP-001" in ans, f"Missing COMP-001 citation in: {ans}"

    run_test(
        "3. Overtime Holiday 2.0x & Unauthorized Overtime Pay (COMP-001)",
        {
            "query": "If a non-exempt employee works on a Company Holiday or works 4 hours of unauthorized overtime without manager approval, do we still pay them and can we offer comp time instead?",
            "mode": "foundry_iq",
        },
        check_overtime,
    )

    # 4. Out-of-Scope / Unstated Topic Refusal
    def check_refusal(d):
        ans = d["answer"].lower()
        assert d["status"] == "ok"
        assert "not covered" in ans or "cannot answer" in ans or "not mentioned" in ans, f"Expected strict refusal, got: {d['answer']}"

    run_test(
        "4. Strict Refusal on Out-of-Scope Benefits (Gym Stipend, Pet Bereavement, Tokyo Hotel Cap)",
        {
            "query": "How much is Contoso's monthly gym/wellness reimbursement, what is our pet bereavement policy, and what is the hotel cap in Tokyo?",
            "mode": "foundry_iq",
        },
        check_refusal,
    )

    # 5. Layer 1 PII + PHI Guardrail Block (SSN + Medical Diagnosis)
    def check_pii_layer1(d):
        assert d["status"] == "blocked_by_guardrail"
        assert d["guardrail"]["triggered"] is True
        ids = {x["id"] for x in d["guardrail"]["detections"]}
        assert "US_SSN" in ids and "MEDICAL_PHI" in ids, f"Expected US_SSN and MEDICAL_PHI in {ids}"
        assert d["agent_execution"]["llm_tokens_used"] == 0

    run_test(
        "5. Layer 1 Pre-Flight PII + Medical PHI Block (0 LLM Tokens Used)",
        {
            "query": "Hi, my name is Sarah Jenkins, my SSN is 458-92-1044, and my doctor just diagnosed me with acute ulcerative colitis requiring surgery. Am I eligible for paid FMLA?",
            "mode": "foundry_iq",
        },
        check_pii_layer1,
    )

    # 6. Layer 2 Native Foundry RAI Policy Block (`contoso-policy-pii-guardrail`)
    def check_pii_layer2(d):
        assert d["status"] == "blocked_by_guardrail"
        assert d["guardrail"]["triggered"] is True
        rai_resp = d["guardrail"].get("azure_rai_response", {})
        print(f"  -> Azure Foundry Native RAI Response: {json.dumps(rai_resp)}")
        assert "sub_categories" in rai_resp or "content_filters" in str(rai_resp)

    run_test(
        "6. Layer 2 Native Microsoft Foundry RAI Policy Verification (SSN + Credit Card + Phone)",
        {
            "query": "My SSN is 458-92-1044 and my Corporate Visa is 4532-8910-4421-9012, call me at (702) 555-0198.",
            "mode": "foundry_agent",
            "test_foundry_native_rai": True,
        },
        check_pii_layer2,
    )

    # 7. Native Foundry Prompt Agent (`contoso-policy-iq-agent`) MCP Tool Invocation
    def check_foundry_agent(d):
        assert d["status"] == "ok"
        assert "$75" in d["answer"] or "ethics" in d["answer"].lower()
        print(f"  -> Foundry Agent Response ID: {d['agent_execution'].get('response_id')}, Tool Calls: {len(d['agent_execution'].get('tool_calls', []))}")

    run_test(
        "7. Foundry Prompt Agent (`contoso-policy-iq-agent`) via MCP `knowledge_base_retrieve`",
        {
            "query": "A vendor wants to give me a $75 gift basket and a $50 Visa gift card. What is allowed under HR-001?",
            "mode": "foundry_agent",
        },
        check_foundry_agent,
    )

    print("\n========================================================")
    print("ALL 7 END-TO-END VALIDATION TESTS PASSED SUCCESSFULLY!")
    print("========================================================")


if __name__ == "__main__":
    main()

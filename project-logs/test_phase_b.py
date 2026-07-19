#!/usr/bin/env python3
"""
Phase B validation — asks a cold question, then repeats a close variant,
and checks whether retrieve_memory / store_memory action groups fire as
expected. Uses boto3 directly since aws-cli/2.34.53 lacks
`bedrock-agent-runtime invoke-agent` in this environment.
"""
import sys, json, uuid, time, boto3, urllib3
urllib3.disable_warnings()

AGENT_ID = "JIEOIRGZVJ"
ALIAS_ID = "XN0Z1NPGS8"
REGION = "ap-southeast-2"

client = boto3.client("bedrock-agent-runtime", region_name=REGION, verify=False)


def ask(question):
    resp = client.invoke_agent(
        agentId=AGENT_ID,
        agentAliasId=ALIAS_ID,
        sessionId=str(uuid.uuid4()),
        inputText=question,
        enableTrace=True,
    )
    full_text = ""
    action_calls = []
    for event in resp["completion"]:
        if "chunk" in event:
            full_text += event["chunk"]["bytes"].decode("utf-8")
        if "trace" in event:
            tr = event["trace"].get("trace", {})
            orch = tr.get("orchestrationTrace", {})
            if "invocationInput" in orch:
                ai = orch["invocationInput"].get("actionGroupInvocationInput", {})
                if ai:
                    action_calls.append(ai.get("actionGroupName"))
    return full_text, action_calls


def main():
    print("=== Test 1: cold question (first time asked) ===")
    q1 = "How many providers are primary care physicians?"
    text1, calls1 = ask(q1)
    print(f"Q: {q1}")
    print(f"Action calls: {calls1}")
    print(f"A: {text1[:400]}")
    print()

    print("Waiting 8s for async-ish memory write to land...")
    time.sleep(8)

    print("=== Test 2: near-identical repeat (should hit memory) ===")
    q2 = "How many PCPs are there?"
    text2, calls2 = ask(q2)
    print(f"Q: {q2}")
    print(f"Action calls: {calls2}")
    print(f"A: {text2[:400]}")
    print()

    print("=== Validation ===")
    print(f"retrieve_memory called on Q1: {'retrieve_memory' in calls1}")
    print(f"store_memory called on Q1:    {'store_memory' in calls1}")
    print(f"retrieve_memory called on Q2: {'retrieve_memory' in calls2}")


if __name__ == "__main__":
    main()

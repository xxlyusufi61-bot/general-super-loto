import json
import os
import sys
import urllib.request
import urllib.error


# ==========================================
# GENERAL AI CORE
# Version: 0.1.0
# ==========================================

MODEL = "z-ai/glm-5.3-flash"

PROJECT_RULES = """
You are part of GENERAL, a Super Loto research project.

BINDING RULES:
1. Never use a hidden target draw for training or discovery.
2. Preserve chronological, leakage-free evaluation.
3. Respect archive integrity and verify data before analysis.
4. Investigate A->B->C, A->T and MOD5->T methods.
5. Use walk-forward evaluation and cross-validation.
6. Never claim a hypothesis is proven without test results.
7. Never silently change established project rules.
8. Ask the user before changing binding rules.
9. Do not invent archive data, test results or findings.
10. Lottery outcomes are uncertain; do not promise predictions.

The system must distinguish hypotheses from verified findings.
"""

AGENTS = {
    "research": {
        "name": "Research",
        "role": """
Investigate mathematical hypotheses and possible patterns.
Explore A->B->C, A->T, MOD5->T and alternative transformations.
Propose experiments without claiming unverified discoveries.
"""
    },

    "debug": {
        "name": "Debug",
        "role": """
Investigate code errors, logic errors and inconsistent results.
Explain likely causes and propose reproducible fixes.
Never claim code was executed unless execution evidence exists.
"""
    },

    "validation": {
        "name": "Validation",
        "role": """
Check archive integrity, chronology, target isolation and leakage.
Review walk-forward testing, cross-validation and baselines.
Identify missing evidence and invalid evaluation methods.
"""
    },

    "crossover": {
        "name": "Crossover",
        "role": """
Investigate combining independently evaluated methods.
Compare method stability and test combinations chronologically.
Reject unsupported improvements and avoid overfitting.
"""
    }
}


def ask_nvidia(messages):
    api_key = os.environ.get("NVIDIA_API_KEY")

    if not api_key:
        raise RuntimeError(
            "NVIDIA_API_KEY environment variable is missing."
        )

    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": 1200,
        "temperature": 0.2,
        "stream": False
    }

    request = urllib.request.Request(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            result = json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as error:
        raise RuntimeError(
            "NVIDIA API HTTP error: " + str(error.code)
        ) from None

    choices = result.get("choices", [])

    if not choices:
        raise RuntimeError("NVIDIA API returned no choices.")

    message = choices[0].get("message") or {}

    answer = (
        message.get("content")
        or message.get("reasoning_content")
        or message.get("reasoning")
    )

    if not answer:
        raise RuntimeError(
            "The model returned no readable text."
        )

    return answer


def run_agent(agent_id, task):
    if agent_id not in AGENTS:
        raise ValueError("Unknown agent: " + agent_id)

    agent = AGENTS[agent_id]

    messages = [
        {
            "role": "system",
            "content": (
                PROJECT_RULES
                + "\n\nYour agent name: "
                + agent["name"]
                + "\nYour responsibilities:\n"
                + agent["role"]
            )
        },
        {
            "role": "user",
            "content": task
        }
    ]

    print("GENERAL | Agent:", agent["name"])
    print("GENERAL | Sending task to NVIDIA AI...")

    answer = ask_nvidia(messages)

    print("\n--- AGENT REPORT ---")
    print(answer)
    print("--- END OF REPORT ---")

    return answer


def main():
    if len(sys.argv) < 3:
        print("GENERAL AI Core 0.1.0")
        print()
        print("Usage:")
        print(
            'python general_core.py research "Your research task"'
        )
        print()
        print("Available agents:")
        for agent_id, agent in AGENTS.items():
            print("-", agent_id, ":", agent["name"])
        return

    agent_id = sys.argv[1].lower()
    task = " ".join(sys.argv[2:]).strip()

    if not task:
        print("ERROR: Task cannot be empty.")
        return

    try:
        run_agent(agent_id, task)
    except Exception as error:
        print("GENERAL ERROR:", str(error))
        sys.exit(1)


if __name__ == "__main__":
    main()

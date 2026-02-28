"""
Simple story-based test corpus for the Answerable? diamond.

We use a short detective story as our "knowledge base" (the offline-processed
documents).  This lets us test all four approaches without needing Valeo's
internal systems.

Story: "The Missing Notebook" — a 5-paragraph mini mystery.
"""

STORY = {
    "title": "The Missing Notebook",
    "paragraphs": [
        # paragraph 0
        (
            "Detective Layla arrived at the Cairo library at 9 AM on a Tuesday. "
            "The head librarian, Mr. Farouk, reported that a rare notebook "
            "containing ancient Arabic poetry had vanished overnight from the "
            "locked rare-books room on the third floor."
        ),
        # paragraph 1
        (
            "Security footage showed three visitors entering the library the "
            "previous evening: a university professor named Dr. Hana, a young "
            "student named Refai, and a tourist named Haya.  The rare-books room "
            "was accessible only with a magnetic key card."
        ),
        # paragraph 2
        (
            "Dr. Hana had borrowed a key card from Mr. Farouk at 6 PM and "
            "returned it at 7 PM.  Refai was seen in the main reading hall the "
            "entire evening and never approached the third floor.  Haya's hotel "
            "key card was found near the rare-books room door."
        ),
        # paragraph 3
        (
            "The notebook was eventually discovered inside a blue backpack "
            "left in the library café.  The backpack had a university logo and "
            "a name tag that read 'Dr. H. Shalaby'."
        ),
        # paragraph 4
        (
            "Detective Layla concluded that Dr. Hana had accidentally taken the "
            "notebook when she packed her bag after her research session.  "
            "There was no theft — just an honest mistake.  The notebook was "
            "returned to Mr. Farouk by noon."
        ),
    ],
}

# ---------------------------------------------------------------------------
# Test cases
# Each case has:
#   query          — the user's question
#   relevant_chunks — which paragraphs contain the answer
#   expected       — "answerable" or "unanswerable" given those chunks
#   answer         — the correct answer (for evaluation)
# ---------------------------------------------------------------------------

TEST_CASES = [
    {
        "id": "TC-01",
        "description": "Direct fact — clearly answerable from one chunk",
        "query": "What time did Detective Layla arrive at the library?",
        "relevant_chunks": [STORY["paragraphs"][0]],
        "expected": "answerable",
        "answer": "9 AM on a Tuesday.",
    },
    {
        "id": "TC-02",
        "description": "Multi-hop — requires combining two paragraphs",
        "query": "Who had access to the rare-books room and was near its door?",
        "relevant_chunks": [STORY["paragraphs"][1], STORY["paragraphs"][2]],
        "expected": "answerable",
        "answer": "Dr. Hana (had a key card) and Haya (key card found near the door).",
    },
    {
        "id": "TC-03",
        "description": "Unanswerable — answer not in provided chunks",
        # NOTE: keyword overlap will FAIL this case because the words 'ancient',
        # 'arabic', 'poetry', 'notebook' all appear in the chunk — but the
        # specific *title* is never given.  This is a known limitation of
        # keyword overlap (it checks word presence, not answer presence).
        # Semantic similarity and LLM-based approaches handle this better.
        "query": "What was the author of the ancient Arabic poetry in the notebook?",
        "relevant_chunks": [STORY["paragraphs"][0]],  # only mentions the notebook exists
        "expected": "unanswerable",
        "answer": "Not mentioned in the story.",
        "known_failure_approaches": ["Keyword Overlap Heuristic"],
    },
    {
        "id": "TC-04",
        "description": "Unanswerable with irrelevant chunks",
        "query": "How did Haya enter the library without a key card?",
        "relevant_chunks": [STORY["paragraphs"][3]],  # talks about the backpack, not Haya's entry
        "expected": "unanswerable",
        "answer": "The story does not explain how Haya entered.",
    },
    {
        "id": "TC-05",
        "description": "Answerable — conclusion paragraph",
        "query": "Was the notebook stolen?",
        "relevant_chunks": [STORY["paragraphs"][4]],
        "expected": "answerable",
        "answer": "No — it was an honest mistake by Dr. Hana.",
    },
    {
        "id": "TC-06",
        "description": "Escalation test — starts unanswerable, becomes answerable after LongRAG",
        # Query uses content words that appear in paragraph 3 but NOT in paragraph 0.
        "query": "Which backpack contained the notebook and whose name tag was on it?",
        "relevant_chunks": [STORY["paragraphs"][0]],  # initial RAG: only intro — no backpack info
        "escalated_chunks": [STORY["paragraphs"][0], STORY["paragraphs"][3]],  # after LongRAG
        "expected": "unanswerable",          # with initial chunks only
        "expected_after_escalation": "answerable",
        "answer": "A blue backpack with a name tag reading 'Dr. H. Shalaby'.",
    },
]

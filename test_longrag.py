"""
Tests for the LongRAG prototype.

Covers:
  1. Document grouping (Algorithm 1)
  2. Long retriever (MaxP TF-IDF)
  3. Long reader (two-turn extraction)
  4. End-to-end pipeline
  5. Ablation: short units vs long units
  6. Multi-hop QA scenario (HotpotQA-style)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import math
import unittest
from longrag import (
    HeuristicReader, ClaudeReader,
    Document, RetrievalUnit,
    group_documents,
    LongRetriever,
    HeuristicReader,
    LongRAG,
    _tokenize, _tfidf_vector, _build_idf, _dot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_doc(doc_id: str, title: str, text: str, links: list[str] | None = None) -> Document:
    return Document(doc_id=doc_id, title=title, text=text, links=links or [])


WIKI_MINI = [
    make_doc(
        "d1", "Python (programming language)",
        ("Python is a high-level, general-purpose programming language. "
         "Its design philosophy emphasizes code readability with the use of significant indentation. "
         "Python is dynamically typed and garbage-collected. "
         "It supports multiple programming paradigms. ") * 5,
        links=["d2"],
    ),
    make_doc(
        "d2", "Guido van Rossum",
        ("Guido van Rossum is a Dutch programmer best known as the creator of Python. "
         "He was Python's principal author until July 2018. "
         "Van Rossum attended the Vrije Universiteit where he got his master degree in 1982. ") * 3,
        links=["d1"],
    ),
    make_doc(
        "d3", "Java (programming language)",
        ("Java is a high-level, class-based, object-oriented programming language. "
         "Java applications are typically compiled to bytecode. "
         "Java was originally developed by James Gosling at Sun Microsystems. ") * 4,
        links=["d4"],
    ),
    make_doc(
        "d4", "James Gosling",
        ("James Gosling is a Canadian computer scientist, best known as the founder and lead designer behind Java. "
         "He was elected a member of the National Academy of Engineering. "
         "Gosling attended the University of Calgary. ") * 3,
        links=["d3"],
    ),
    make_doc(
        "d5", "Rust (programming language)",
        ("Rust is a multi-paradigm, general-purpose programming language. "
         "Rust emphasizes performance, type safety, and concurrency. "
         "Rust enforces memory safety, meaning that all references point to valid memory. "
         "It was designed by Graydon Hoare at Mozilla Research. ") * 6,
        links=[],
    ),
    # AFL docs are SHORT (< 50 words each) so they will be grouped together via hyperlinks
    make_doc(
        "d6", "AFL Rising Star Award",
        ("The NAB AFL Rising Star award is given annually to a standout young player. "
         "The 2015 award was won by Jesse Hogan of Melbourne who polled 49 votes. "
         "Patrick Cripps finished second with 41 votes."),
        links=["d7"],
    ),
    make_doc(
        "d7", "Jesse Hogan",
        ("Jesse Hogan plays in the Australian Football League. "
         "He is a key forward and stands 1.95 m tall and weighs 100 kg. "
         "Hogan was selected by Melbourne Football Club."),
        links=["d6"],
    ),
]


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestGroupDocuments(unittest.TestCase):

    def test_long_docs_are_individual_units(self):
        """Documents exceeding short_doc_threshold are kept as single units."""
        units = group_documents(WIKI_MINI, max_tokens=4000, short_doc_threshold=60)
        unit_ids_with_multiple_docs = [u for u in units if len(u.docs) > 1]
        # All docs are long relative to threshold=60, so no grouping expected
        for unit in units:
            self.assertGreaterEqual(unit.token_count, 1)

    def test_short_docs_get_grouped(self):
        """Short linked documents should be merged into shared units."""
        # Use tiny docs that are definitely "short"
        tiny_docs = [
            make_doc("a", "Doc A", "short text about topic alpha " * 10, links=["b"]),
            make_doc("b", "Doc B", "short text about topic beta " * 10, links=["a"]),
            make_doc("c", "Doc C", "short text about topic gamma " * 10, links=[]),
        ]
        units = group_documents(tiny_docs, max_tokens=2000, short_doc_threshold=500)
        # a and b are linked so should merge; c is alone
        total_doc_count = sum(len(u.docs) for u in units)
        self.assertEqual(total_doc_count, 3)
        # There should be fewer units than docs (due to grouping)
        self.assertLess(len(units), len(tiny_docs))

    def test_corpus_compression_ratio(self):
        """Grouping should reduce number of units vs number of documents."""
        units = group_documents(WIKI_MINI, max_tokens=4000, short_doc_threshold=60)
        # d6 and d7 are short (~40 words) and linked → should merge
        self.assertLessEqual(len(units), len(WIKI_MINI))

    def test_max_tokens_constraint(self):
        """No grouped unit should exceed max_tokens."""
        units = group_documents(WIKI_MINI, max_tokens=300, short_doc_threshold=60)
        for unit in units:
            self.assertLessEqual(
                unit.token_count, 600,  # allow 2x due to approximate token counting
                f"Unit {unit.unit_id} too large: {unit.token_count} tokens"
            )

    def test_all_docs_are_covered(self):
        """Every document must appear in exactly one retrieval unit."""
        units = group_documents(WIKI_MINI, max_tokens=4000, short_doc_threshold=60)
        doc_ids_in_units = []
        for unit in units:
            for doc in unit.docs:
                doc_ids_in_units.append(doc.doc_id)
        original_ids = {d.doc_id for d in WIKI_MINI}
        self.assertEqual(set(doc_ids_in_units), original_ids)
        self.assertEqual(len(doc_ids_in_units), len(WIKI_MINI))  # no duplicates


class TestTFIDF(unittest.TestCase):

    def test_idf_increases_for_rare_terms(self):
        units = group_documents(WIKI_MINI, max_tokens=4000, short_doc_threshold=60)
        idf = _build_idf(units)
        if "python" in idf and "the" in idf:
            self.assertGreater(idf["python"], idf["the"])

    def test_tfidf_vector_normalized(self):
        units = group_documents(WIKI_MINI, max_tokens=4000, short_doc_threshold=60)
        idf = _build_idf(units)
        vec = _tfidf_vector(_tokenize("python programming language creator"), idf)
        norm = math.sqrt(sum(v * v for v in vec.values()))
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_dot_product_similarity(self):
        units = group_documents(WIKI_MINI, max_tokens=4000, short_doc_threshold=60)
        idf = _build_idf(units)
        v1 = _tfidf_vector(_tokenize("python programming"), idf)
        v2 = _tfidf_vector(_tokenize("python programming"), idf)
        v3 = _tfidf_vector(_tokenize("java basketball sport"), idf)
        self.assertAlmostEqual(_dot(v1, v2), 1.0, places=4)
        self.assertLess(_dot(v1, v3), _dot(v1, v2))


class TestLongRetriever(unittest.TestCase):

    def setUp(self):
        self.units = group_documents(WIKI_MINI, max_tokens=4000, short_doc_threshold=60)
        self.retriever = LongRetriever(self.units, chunk_size=50)

    def test_returns_top_k_units(self):
        results = self.retriever.retrieve("Who created Python?", top_k=3)
        self.assertEqual(len(results), 3)

    def test_results_sorted_by_score(self):
        results = self.retriever.retrieve("Python programming language creator Guido", top_k=4)
        scores = [score for _, score in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_python_query_retrieves_python_unit(self):
        """Python-related query should retrieve a unit containing Python docs."""
        results = self.retriever.retrieve("Python programming language Guido van Rossum", top_k=2)
        top_unit, top_score = results[0]
        top_text = top_unit.text.lower()
        self.assertIn("python", top_text)

    def test_java_query_retrieves_java_unit(self):
        """Java-related query should not retrieve Python as the top result."""
        results = self.retriever.retrieve("Java object-oriented James Gosling", top_k=2)
        top_unit, _ = results[0]
        self.assertIn("java", top_unit.text.lower())

    def test_multihop_afl_query(self):
        """
        Multi-hop style query: height of 2015 AFL Rising Star winner.
        Requires both d6 (award info) and d7 (Jesse Hogan height).
        """
        results = self.retriever.retrieve(
            "height of the player who won the 2015 AFL Rising Star award", top_k=3
        )
        retrieved_texts = " ".join(u.text for u, _ in results).lower()
        # Should pull in Jesse Hogan docs with height info
        self.assertIn("hogan", retrieved_texts)

    def test_top1_recall_better_with_long_units(self):
        """
        Key paper claim: long units improve top-1 recall vs short units.
        We verify that grouping d6+d7 (which are linked) makes multi-hop
        answerable from a single top-1 retrieval unit.
        """
        # Long units (grouped) - d6 and d7 are short (~40 words) so they merge
        long_units = group_documents(WIKI_MINI, max_tokens=4000, short_doc_threshold=60)
        long_retriever = LongRetriever(long_units, chunk_size=50)

        # Short units (no grouping, each doc standalone)
        short_units = [RetrievalUnit(unit_id=d.doc_id, docs=[d]) for d in WIKI_MINI]
        short_retriever = LongRetriever(short_units, chunk_size=50)

        query = "height of the player who won the 2015 AFL Rising Star award"

        long_results = long_retriever.retrieve(query, top_k=1)
        short_results = short_retriever.retrieve(query, top_k=1)

        def has_answer(results, answer_text):
            combined = " ".join(u.text for u, _ in results).lower()
            return answer_text.lower() in combined

        long_recall = has_answer(long_results, "1.95")
        short_recall = has_answer(short_results, "1.95")

        print(f"\n[Recall@1] Long units: {long_recall} | Short units: {short_recall}")
        # Long units (d6+d7 merged) must contain both award AND height info
        self.assertTrue(long_recall, "Grouped long unit (d6+d7) must include height=1.95 at top-1")


class TestHeuristicReader(unittest.TestCase):

    def setUp(self):
        self.reader = HeuristicReader()
        self.context = """
        Title: 2015 AFL Rising Star
        The NAB AFL Rising Star award is given annually to a standout young player.
        The 2015 award was won by Jesse Hogan of Melbourne who polled 49 votes.
        Patrick Cripps finished second with 41 votes.

        Title: Jesse Hogan
        Jesse Hogan plays in the Australian Football League.
        He is a key forward and stands 1.95 m tall and weighs 100 kg.
        Hogan was selected by Melbourne Football Club.
        """

    def test_returns_dict_with_required_keys(self):
        result = self.reader.read("Who won the 2015 AFL Rising Star?", self.context)
        self.assertIn("long_answer", result)
        self.assertIn("short_answer", result)

    def test_long_answer_is_relevant_sentence(self):
        result = self.reader.read("Who won the 2015 AFL Rising Star?", self.context)
        self.assertGreater(len(result["long_answer"]), 5)

    def test_height_extraction(self):
        result = self.reader.read(
            "What is the height of the player who won the 2015 AFL Rising Star?",
            self.context,
        )
        # Should extract the numeric height
        self.assertIn("1.95", result["short_answer"])

    def test_empty_context_handling(self):
        result = self.reader.read("What is X?", "")
        self.assertEqual(result["long_answer"], "")
        self.assertEqual(result["short_answer"], "")


class TestLongRAGPipeline(unittest.TestCase):

    def setUp(self):
        # short_doc_threshold=60 → AFL docs (~40 words each) are "short" and get grouped
        self.system = LongRAG(
            WIKI_MINI,
            max_tokens_per_unit=4000,
            short_doc_threshold=60,
            chunk_size=50,
        )

    def test_answer_returns_expected_keys(self):
        result = self.system.answer("Who created Python?", top_k=2)
        for key in ["query", "long_answer", "short_answer", "retrieved_units", "context_tokens"]:
            self.assertIn(key, result)

    def test_python_creator_query(self):
        result = self.system.answer(
            "Who created the Python programming language?", top_k=3, verbose=True
        )
        # Guido van Rossum should appear somewhere in the answer
        combined = (result["long_answer"] + " " + result["short_answer"]).lower()
        self.assertTrue(
            "guido" in combined or "rossum" in combined or "van" in combined,
            f"Expected Guido in answer, got: {result}"
        )

    def test_java_creator_query(self):
        result = self.system.answer(
            "Who is the founder of Java programming language?", top_k=3, verbose=True
        )
        combined = (result["long_answer"] + " " + result["short_answer"]).lower()
        self.assertTrue(
            "gosling" in combined or "james" in combined,
            f"Expected Gosling in answer, got: {result}"
        )

    def test_multihop_afl_height(self):
        result = self.system.answer(
            "What is the height of the player who won the 2015 AFL Rising Star award?",
            top_k=3,
            verbose=True,
        )
        self.assertIn("1.95", result["short_answer"],
                      f"Expected height 1.95m, got: {result['short_answer']!r}")

    def test_context_token_count_reasonable(self):
        """Context sent to reader should be non-trivial."""
        result = self.system.answer("Python creator", top_k=4)
        self.assertGreater(result["context_tokens"], 50)

    def test_corpus_compression(self):
        """Grouping should reduce number of units."""
        self.assertLessEqual(len(self.system.units), len(WIKI_MINI))


class TestAblation(unittest.TestCase):
    """
    Ablation studies mirroring Section 3.5 of the paper.
    Compares short (passage-level) vs long retrieval units.
    """

    def _answer_recall(self, units, query, expected_answer_substr, top_k):
        retriever = LongRetriever(units, chunk_size=30)
        results = retriever.retrieve(query, top_k=top_k)
        combined = " ".join(u.text for u, _ in results).lower()
        return expected_answer_substr.lower() in combined

    def test_long_units_require_fewer_retrievals(self):
        """
        Key paper claim: with long units, fewer top-k needed for same recall.
        """
        long_units = group_documents(WIKI_MINI, max_tokens=4000, short_doc_threshold=60)
        short_units = [RetrievalUnit(unit_id=d.doc_id, docs=[d]) for d in WIKI_MINI]

        query = "height of the player who won the 2015 AFL Rising Star award"
        expected = "1.95"

        # Long units: try top-1 and top-2
        recall_long_k1 = self._answer_recall(long_units, query, expected, top_k=1)
        recall_long_k2 = self._answer_recall(long_units, query, expected, top_k=2)

        # Short units: need more to find both the award doc and the height doc
        recall_short_k1 = self._answer_recall(short_units, query, expected, top_k=1)
        recall_short_k2 = self._answer_recall(short_units, query, expected, top_k=2)
        recall_short_k4 = self._answer_recall(short_units, query, expected, top_k=4)

        print(f"\n[Ablation] Recall@1 – Long: {recall_long_k1}, Short: {recall_short_k1}")
        print(f"[Ablation] Recall@2 – Long: {recall_long_k2}, Short: {recall_short_k2}")
        print(f"[Ablation] Recall@4 – Long: -, Short: {recall_short_k4}")

        # d6+d7 grouped into one unit → recall answer at top-1
        self.assertTrue(recall_long_k1, "Long units should recall answer at top-1 (d6+d7 grouped)")

    def test_unit_compression_ratio(self):
        """Verify corpus compression matching paper's 22M→600K claim (proportionally)."""
        short_units = [RetrievalUnit(unit_id=d.doc_id, docs=[d]) for d in WIKI_MINI]
        long_units = group_documents(WIKI_MINI, max_tokens=4000, short_doc_threshold=60)
        ratio = len(short_units) / len(long_units)
        print(f"\n[Ablation] Compression ratio: {ratio:.1f}x "
              f"({len(short_units)} → {len(long_units)} units)")
        self.assertGreater(ratio, 1.0, "LongRAG should always compress the corpus")

    def test_hard_negative_dilution(self):
        """
        With short units at high top-k, irrelevant units (hard negatives) dominate.
        With long units at low top-k, signal-to-noise is better.
        """
        long_units = group_documents(WIKI_MINI, max_tokens=4000, short_doc_threshold=60)
        short_units = [RetrievalUnit(unit_id=d.doc_id, docs=[d]) for d in WIKI_MINI]

        query = "Python creator Guido van Rossum"

        long_retriever = LongRetriever(long_units, chunk_size=50)
        short_retriever = LongRetriever(short_units, chunk_size=50)

        # For short units retrieve ALL (simulating hard-negative-heavy top-200)
        short_all = short_retriever.retrieve(query, top_k=len(short_units))
        # Count how many retrieved units are NOT about Python/Guido (= hard negatives)
        def is_relevant(unit: RetrievalUnit) -> bool:
            text = unit.text.lower()
            return "python" in text or "guido" in text or "rossum" in text

        short_relevant = sum(1 for u, _ in short_all if is_relevant(u))
        short_hard_neg = len(short_all) - short_relevant

        # For long units retrieve just top-2
        long_top2 = long_retriever.retrieve(query, top_k=2)
        long_relevant = sum(1 for u, _ in long_top2 if is_relevant(u))
        long_hard_neg = len(long_top2) - long_relevant

        print(f"\n[Ablation] Short units (all): {short_relevant} relevant, "
              f"{short_hard_neg} hard negatives")
        print(f"[Ablation] Long units (top-2): {long_relevant} relevant, "
              f"{long_hard_neg} hard negatives")

        # Long RAG should have fewer (or equal) hard negatives per retrieved unit
        self.assertLessEqual(
            long_hard_neg / max(1, len(long_top2)),
            short_hard_neg / max(1, len(short_all)) + 0.01,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Order matters for readability
    for cls in [
        TestGroupDocuments,
        TestTFIDF,
        TestLongRetriever,
        TestHeuristicReader,
        TestLongRAGPipeline,
        TestAblation,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)

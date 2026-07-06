# tests/test_evidence_first.py
"""Demo + regression test for the Evidence-First Retrieval Pipeline.

Motivating scenario (from the thesis): a CrashLoopBackOff event and a failed
readiness probe are the decisive signals, but they are buried among thousands of
routine log lines. Under "collect-all" prompting those signals are diluted and
lost in the middle. This test asserts the pipeline surfaces them at the edges
and collapses the routine noise.

Runs on the deterministic lexical fallback — NO model download required:
    python tests/test_evidence_first.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.schemas import EvidenceBundle, FaultSignature, SIGNAL
from app.rerank.cross_encoder_reranker import Reranker
from app.pipelines.evidence_first_pipeline import distill_evidence

POD = "checkoutservice-7d9f8c6b45-abcde"

BACKOFF_MSG = (
    f"30s   Warning   BackOff   pod/{POD}   "
    f"Back-off restarting failed container checkout in pod {POD}"
)
PROBE_MSG = (
    f"45s   Warning   Unhealthy   pod/{POD}   "
    f"Readiness probe failed: HTTP probe failed with statuscode: 500"
)


def _build_noisy_bundle(routine_lines: int = 3000) -> EvidenceBundle:
    # events.txt: two decisive Warnings buried among routine Normal events.
    events = ["LAST SEEN   TYPE     REASON    OBJECT     MESSAGE"]
    for i in range(routine_lines // 2):
        events.append(
            f"5d   Normal   Pulled   pod/frontend-{i:05d}   "
            f"Successfully pulled image in 1.2s"
        )
    events.append(BACKOFF_MSG)          # decisive signal #1
    events.append(PROBE_MSG)            # decisive signal #2
    for i in range(routine_lines // 2):
        events.append(
            f"5d   Normal   Started   pod/frontend-{i:05d}   "
            f"Started container server"
        )

    # pod logs: mostly routine 200 OK, one error line deep in the middle.
    logs = []
    for i in range(routine_lines):
        logs.append(f'level=info msg="handled GET /health 200 OK" seq={i}')
    logs.insert(
        routine_lines // 2,
        'level=error msg="readiness check failed: dial tcp 10.0.3.9:7070: '
        'connect: connection refused"',
    )

    pods = (
        "NAME                                 READY   STATUS             RESTARTS   AGE\n"
        "frontend-6b9c4d8f7-11111             1/1     Running            0          5d\n"
        f"{POD}     0/1     CrashLoopBackOff   7          12m\n"
        "cartservice-5f6d7c8b9-22222          1/1     Running            0          5d\n"
    )

    return EvidenceBundle(
        fault=FaultSignature(
            cluster="edge1",
            namespace="default",
            pod=POD,
            fault_type="Application_Fault",
            fault_object=f"pod/{POD}",
            root_cause_hint="crashloopbackoff",
            summary=f"[edge1] crashloopbackoff detected on pod/{POD}",
        ),
        sources={
            "pods-all.txt": pods,
            "events.txt": "\n".join(events),
            f"{POD}-logs.txt": "\n".join(logs),
        },
    )


def run_pipeline(routine_lines: int = 3000):
    bundle = _build_noisy_bundle(routine_lines)
    result = distill_evidence(bundle, reranker=Reranker(use_model=False))
    return bundle, result


# ── assertions (also runnable under pytest) ──────────────────────────────────
def test_signals_surface_at_edges():
    _, result = run_pipeline()
    texts = [s.text for s in result.verbatim]

    # both decisive signals survive as verbatim evidence
    assert any("Back-off restarting" in t for t in texts), "BackOff signal missing"
    assert any("Readiness probe failed" in t for t in texts), "probe signal missing"

    # strongest first AND second-strongest last are both decisive signals
    assert result.verbatim[0].signal_class == SIGNAL, "top snippet is not a signal"
    assert result.verbatim[-1].signal_class == SIGNAL, "edge (last) snippet is not a signal"


def test_routine_noise_is_compressed():
    _, result = run_pipeline()
    verbatim_texts = "\n".join(s.text for s in result.verbatim)

    # routine 200-OK spam must NOT be verbatim
    assert "200 OK" not in verbatim_texts, "routine log line leaked into verbatim"

    # it should appear collapsed with an N× multiplier in the compressed section
    assert any("×" in c for c in result.compressed), "no collapsed clusters produced"


def test_large_token_reduction():
    bundle, result = run_pipeline()
    raw = result.stats["raw_chars"]
    distilled = result.stats["distilled_chars"]
    assert distilled < raw, "distilled block is not smaller than raw"
    assert result.stats["reduction_ratio"] > 0.8, (
        f"expected >80% reduction, got {result.stats['reduction_ratio']:.2%}"
    )


def test_confidence_gate():
    _, result = run_pipeline()
    # a clear crashloop with named object should be high confidence
    assert result.low_confidence is False


def _report():
    bundle, result = run_pipeline()
    s = result.stats
    print("=" * 72)
    print("Evidence-First Retrieval Pipeline — demo run")
    print("=" * 72)
    print(f"raw sources chars      : {s['raw_chars']:,}")
    print(f"total snippets chunked : {s['total_snippets']:,}")
    print(f"candidates after top-k : {s['candidates']:,}")
    print(f"signal snippets found  : {s['signal_snippets']}")
    print(f"verbatim kept          : {s['verbatim_snippets']}")
    print(f"routine collapsed into : {s['compressed_clusters_out']} clusters "
          f"(from {s['compressed_lines_in']:,} lines)")
    print(f"distilled block chars  : {s['distilled_chars']:,}")
    print(f"context reduction      : {s['reduction_ratio']:.2%}")
    print(f"top rerank score       : {s['top_score']}")
    print(f"low confidence         : {result.low_confidence}")
    print("-" * 72)
    print("DISTILLED PROMPT BLOCK:")
    print("-" * 72)
    print(result.prompt_block)


if __name__ == "__main__":
    test_signals_surface_at_edges()
    test_routine_noise_is_compressed()
    test_large_token_reduction()
    test_confidence_gate()
    _report()
    print("\nAll assertions passed ✅")

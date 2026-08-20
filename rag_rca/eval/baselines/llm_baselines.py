# eval/baselines/llm_baselines.py
"""LLM RCA baselines for the comparison, all calling the same kagent RCA agent so
only the *method* differs:

  yours_raw        : thesis system, collect-all evidence prompt (no pipeline).
  yours_distilled  : thesis system WITH the Evidence-First pipeline.
  rcacopilot       : RCACopilot (Chen et al., EuroSys'24) technique -
                     retrieve similar historical incidents (leave-one-out) as
                     few-shot exemplars, then predict category + root cause.
  rcagent          : RCAgent (Wang et al., CIKM'24) technique - self-consistency
                     over N independent tool-augmented RCA samples, aggregated.

These are faithful re-implementations of each paper's *technique* (not the
authors' original systems) evaluated on a common bench. See thesis threats-to-
validity. Category taxonomy comes from the dataset.
"""
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))  # rag_rca/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))          # eval/

from app.pipelines.evidence_first_pipeline import distill_evidence
from app.rerank.cross_encoder_reranker import Reranker
from benchmark_pipeline import build_raw_prompt
from e2e_ablation import rca_base_prompt, _post, RCA_AGENT_URL, est_tokens

CATEGORIES = ["Admission", "Scheduling", "Startup", "Runtime", "Service Routing", "Performance"]
_RERANKER = Reranker(use_model=False)


# ---------------------------------------------------------------- shared
def _evidence_text(bundle):
    return "\n\n".join(f"### {name}\n{txt}" for name, txt in bundle.sources.items())


def _evidence_digest(bundle, max_chars=1400):
    """Compact evidence summary for few-shot exemplars (events + pod status)."""
    parts = []
    for key in ("events.txt", "pods.txt", "ns_pods.txt", "pod_describe.txt"):
        if key in bundle.sources:
            parts.append(f"[{key}]\n" + bundle.sources[key][:700])
    return "\n".join(parts)[:max_chars]


# ---------------------------------------------------------------- yours
def run_yours_raw(bundle, timeout=300):
    prompt = rca_base_prompt(bundle.fault, "## Kubernetes Evidence\n" + build_raw_prompt(bundle))
    text, lat, _ = _post(RCA_AGENT_URL, prompt, timeout)
    return {"text": text, "latency_s": round(lat, 1), "prompt_tokens": est_tokens(prompt)}


def run_yours_distilled(bundle, timeout=300):
    block = distill_evidence(bundle, reranker=_RERANKER).prompt_block
    prompt = rca_base_prompt(bundle.fault, block)
    text, lat, _ = _post(RCA_AGENT_URL, prompt, timeout)
    return {"text": text, "latency_s": round(lat, 1), "prompt_tokens": est_tokens(prompt)}


# ---------------------------------------------------------------- RCACopilot
def retrieve_similar(target_iid, corpus, k=2):
    """Leave-one-out TF-IDF cosine retrieval of the k most similar incidents.

    corpus: list of (iid, bundle, gt). Returns list of (iid, bundle, gt, score).
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    ids = [iid for iid, _, _ in corpus]
    docs = [_evidence_text(b) for _, b, _ in corpus]
    tfidf = TfidfVectorizer(max_features=4000, stop_words="english").fit_transform(docs)
    ti = ids.index(target_iid)
    sims = cosine_similarity(tfidf[ti], tfidf).ravel()
    order = [j for j in sims.argsort()[::-1] if j != ti][:k]
    return [(corpus[j][0], corpus[j][1], corpus[j][2], float(sims[j])) for j in order]


def rcacopilot_prompt(bundle, examples):
    """RCACopilot-style prompt: few-shot exemplars (retrieved similar incidents +
    their known category & root cause) then the target incident's evidence."""
    shots = []
    for iid, b, gt, score in examples:
        shots.append(
            f"--- Example incident (similarity {score:.2f}) ---\n"
            f"Evidence:\n{_evidence_digest(b)}\n"
            f"Category: {gt['category']}\n"
            f"Root cause: {gt['gold_text']}\n")
    shots_block = "\n".join(shots) if shots else "(no similar past incidents retrieved)"
    return f"""You are an on-call diagnosis assistant. Use the retrieved similar PAST
incidents (with known category and root cause) as guidance, then diagnose the
NEW incident from its evidence.

## Retrieved similar past incidents
{shots_block}

## Incident to diagnose
Cluster: {bundle.fault.cluster}   Namespace: {bundle.fault.namespace}

## Evidence
{_evidence_text(bundle)}

## Task
1. Predict the incident CATEGORY (one of: {', '.join(CATEGORIES)}).
2. State the PRIMARY root cause (the specific object and why it failed), citing evidence.
3. Give remediation steps.

Write markdown. Be specific about the failing object.
"""


def run_rcacopilot(target_iid, bundle, corpus, k=2, timeout=300):
    examples = retrieve_similar(target_iid, corpus, k=k)
    prompt = rcacopilot_prompt(bundle, examples)
    text, lat, _ = _post(RCA_AGENT_URL, prompt, timeout)
    return {"text": text, "latency_s": round(lat, 1), "prompt_tokens": est_tokens(prompt),
            "retrieved": [e[0] for e in examples]}


# ---------------------------------------------------------------- RCAgent
def run_rcagent(bundle, n=3, timeout=300):
    """RCAgent-style self-consistency: N independent tool-augmented RCA samples on
    the incident evidence, then aggregate to the most consistent root cause.

    Uses the raw evidence (the agent reasons/acts over it), matching RCAgent's
    tool-augmented reasoning; the novel bit reproduced here is the self-consistency
    aggregation over N trajectories.
    """
    base = rca_base_prompt(bundle.fault, "## Kubernetes Evidence\n" + build_raw_prompt(bundle))
    samples, lat_tot = [], 0.0
    for i in range(n):
        # vary the sample so trajectories differ (self-consistency needs diversity)
        p = base + f"\n\n(Investigation pass {i+1}: reason step by step and commit to a single primary root cause.)"
        try:
            text, lat, _ = _post(RCA_AGENT_URL, p, timeout)
            samples.append(text); lat_tot += lat
        except Exception as e:
            samples.append(f"[sample {i+1} failed: {str(e)[:120]}]")
    consolidated = _aggregate_consistency(bundle, samples, timeout)
    return {"text": consolidated["text"], "latency_s": round(lat_tot, 1),
            "prompt_tokens": est_tokens(base) * n, "n_samples": n,
            "samples": samples, "consolidation_latency_s": consolidated["latency_s"]}


def _aggregate_consistency(bundle, samples, timeout):
    """Aggregate N trajectories into the most consistent conclusion (RCAgent's
    self-consistency). We ask the agent to pick the root cause the samples agree
    on most, which is the majority-vote over free-text trajectories."""
    joined = "\n\n".join(f"### Sample {i+1}\n{s}" for i, s in enumerate(samples))
    prompt = f"""You produced {len(samples)} independent root-cause analyses of the same
incident. Apply self-consistency: identify the PRIMARY root cause that the
majority of samples agree on, discard outliers, and write one consolidated
analysis (primary root cause + evidence + remediation).

Namespace: {bundle.fault.namespace}

{joined}

Write the consolidated markdown analysis (be specific about the failing object).
"""
    text, lat, _ = _post(RCA_AGENT_URL, prompt, timeout)
    return {"text": text, "latency_s": round(lat, 1)}

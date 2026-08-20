# C5 — EFCA Implementation Details (for the paper)

Drop-in table + prose so §III-D is reproducible. All values are the actual
defaults in `rag_rca/app/config.py` and the stage modules.

## LaTeX table

```latex
\begin{table}[!t]
\caption{Evidence-First Context Assembly (EFCA) Implementation Details}
\label{tab:efca-impl}
\centering
\small
\begin{tabular}{p{2.5cm} p{5.2cm}}
\hline
\textbf{Stage / Parameter} & \textbf{Setting} \\
\hline
\multicolumn{2}{l}{\textit{Stage 1 — Top-$k$ retrieval}} \\
Vector store        & Qdrant (collection \texttt{rca\_chunks}) \\
Embedding model     & BAAI/bge-m3 \\
Evidence sources    & events, container logs, manifests, \texttt{describe}/kubectl outputs \\
Scoping             & incident pod, namespace, node \\
Candidates / source & 12 (after lexical prefilter; SIGNAL snippets always kept) \\
Vector top-$k$      & 4 per query \\
\hline
\multicolumn{2}{l}{\textit{Stage 2 — Task-aware reranking}} \\
Cross-encoder       & BAAI/bge-reranker-v2-m3 (optional; \texttt{USE\_RERANKER\_MODEL}) \\
Default scorer      & deterministic lexical$+$signal fallback (no GPU) \\
Score (fallback)    & $0.35\,\ell + 0.25\,\mathbf{1}[\text{target}] + 0.15\,\hat{h} + b(\text{class})$, clipped to $[0,1]$ \\
\quad $\ell$        & query--snippet lexical overlap (length-normalised) \\
\quad $\mathbf{1}[\text{target}]$ & snippet names the faulting object \\
\quad $\hat{h}$     & overlap with root-cause hint / fault type ($/3$) \\
\quad $b(\text{class})$ & signal prior: SIGNAL $0.45$, CONTEXT $0.10$, ROUTINE $0$ \\
Score (neural)      & model score $+\,0.15\,b(\text{class})$ \\
\hline
\multicolumn{2}{l}{\textit{Stage 3 — Context compression}} \\
Method              & template-collapse of near-identical lines \\
Verbatim budget     & top 8 snippets kept verbatim \\
SIGNAL / ROUTINE    & SIGNAL always verbatim; ROUTINE always collapsed \\
CONTEXT             & fills remaining budget, dedup by template \\
Samples / cluster   & 2 representatives per collapsed cluster \\
Effective prompt size & $\approx$0.7--1.0k tokens (Table~\ref{tab:exp4}); governed by snippet counts (top-8 verbatim $+$ collapsed context), not a hard token cap \\
\hline
\multicolumn{2}{l}{\textit{Stage 4 — Edge placement}} \\
Policy              & strongest snippet first, 2nd-strongest last \\
\hline
\multicolumn{2}{l}{\textit{Stage 5 — On-demand expansion}} \\
Confidence signal   & top reranked score $s_{(1)}$ \\
Expansion trigger   & $s_{(1)} < 0.35 \Rightarrow$ flag low-confidence \\
Expansion action    & agent requests raw timeline via \texttt{get\_raw\_timeline} MCP tool \\
\hline
\end{tabular}
\end{table}
```

## Prose (append to §III-D)

> EFCA is configured as follows. Candidate snippets are embedded with **BAAI/bge-m3**
> and indexed in **Qdrant**; retrieval is scoped to the incident's pod, namespace, and
> node, keeping up to **12 candidates per source** after a lexical prefilter (decisive
> SIGNAL snippets are retained unconditionally). Reranking pairs bge-m3 with the
> **bge-reranker-v2-m3** cross-encoder; when the neural reranker is disabled, a
> deterministic lexical-plus-signal scorer is used
> ($0.35\,\ell + 0.25\,\mathbf{1}[\text{target}] + 0.15\,\hat h + b$, with signal prior
> $b\in\{0.45,0.10,0\}$ for SIGNAL/CONTEXT/ROUTINE), so the pipeline runs without a GPU.
> Compression keeps the **top-8** snippets verbatim and collapses near-identical routine
> lines into `N× <template>` summaries (**2 representatives** per cluster), with SIGNAL
> lines never compressed. The resulting prompt is $\approx$0.7--1.0k tokens
> (Table~\ref{tab:exp4}); its size is governed by these snippet counts rather than a hard
> token cap. Edge placement positions the strongest snippet first and the
> second-strongest last. Finally, if the top reranked score falls **below 0.35** the
> incident is flagged low-confidence and the agent requests the raw timeline via the
> `get_raw_timeline` MCP tool.

## Reranker decision: OPTION 2 (neural cross-encoder enabled)

Resolved: `USE_RERANKER_MODEL = True` with **BAAI/bge-reranker-v2-m3** on GPU, and the
EFCA experiments (Exp. 4 / C6 ablation) are re-run so the "task-aware cross-encoder
reranking" claim matches the released artifact. The lexical+signal scorer remains the
documented no-GPU fallback. Once the re-run lands, replace the reranking numbers in
Table~\ref{tab:exp4} with the neural-reranker results.

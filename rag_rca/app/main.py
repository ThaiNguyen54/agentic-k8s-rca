from app.pipelines.ingest_pipeline import run_ingest
from app.pipelines.query_pipeline import run_query

if __name__ == "__main__":
    total_chunks = run_ingest()
    print(f"Indexed chunks: {total_chunks}")

    result = run_query(
        question="Which config files define interface addresses or referenced config objects?",
        source_type="config_yaml",
    )
    print(result["prompt"])
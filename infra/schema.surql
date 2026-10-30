-- Mycelium schema (SurrealDB). Idempotent: safe to re-apply.
DEFINE NAMESPACE IF NOT EXISTS mycelium;
USE NS mycelium;
DEFINE DATABASE IF NOT EXISTS main;
USE NS mycelium DB main;

DEFINE TABLE IF NOT EXISTS chunk SCHEMALESS;
DEFINE TABLE IF NOT EXISTS source SCHEMALESS;
DEFINE TABLE IF NOT EXISTS question SCHEMALESS;
DEFINE TABLE IF NOT EXISTS reading SCHEMALESS;
DEFINE TABLE IF NOT EXISTS observation SCHEMALESS;
DEFINE TABLE IF NOT EXISTS ledger SCHEMALESS;
DEFINE TABLE IF NOT EXISTS question_shape SCHEMALESS;
DEFINE TABLE IF NOT EXISTS candidate SCHEMALESS;
DEFINE TABLE IF NOT EXISTS lesson SCHEMALESS;
DEFINE TABLE IF NOT EXISTS correction SCHEMALESS;
DEFINE TABLE IF NOT EXISTS config SCHEMALESS;

-- chunk defaults
DEFINE FIELD IF NOT EXISTS alpha ON chunk DEFAULT 1;
DEFINE FIELD IF NOT EXISTS beta ON chunk DEFAULT 1;
DEFINE FIELD IF NOT EXISTS cited_count ON chunk DEFAULT 0;
DEFINE FIELD IF NOT EXISTS retrieved_count ON chunk DEFAULT 0;
DEFINE FIELD IF NOT EXISTS alpha ON source DEFAULT 1;
DEFINE FIELD IF NOT EXISTS beta ON source DEFAULT 1;

DEFINE INDEX IF NOT EXISTS chunk_emb ON chunk FIELDS embedding HNSW DIMENSION 1024 DIST COSINE;
DEFINE INDEX IF NOT EXISTS ledger_ts ON ledger FIELDS ts;
DEFINE INDEX IF NOT EXISTS question_split ON question FIELDS split;

-- config:main mirrors core.contracts.CONFIG
UPSERT config:main CONTENT {"beta_evidence_confirm": 1.0, "beta_evidence_correct": 0.0, "decay": 0.95, "prior_rate_correct": 0.3, "prior_rate_uncorrected": 0.1, "prior_floor": 0.05, "commit": 0.8, "margin": 0.2, "k_runs": 3, "held_out_n": 10, "spoken_max_words": 60, "candidate_spoken_max_words": 120, "slot_token_ratio": 1.25, "retrieval_fitness_floor": 0.5, "knn_k": 8, "bootstrap_n": 1000, "seed": 42};

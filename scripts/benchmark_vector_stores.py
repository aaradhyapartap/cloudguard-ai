#!/usr/bin/env python3
"""Vector store benchmark script: pgvector vs. Amazon S3 Vectors.

Prepares an opt-in benchmark comparing vector store implementations behind the
common VectorStore domain port on latency, recall@k, and operational characteristics.

Modes:
- `synthetic_fixture` (default): Uses deterministic in-memory fixtures. Offline,
  requires zero credentials and incurs zero cloud costs. Simulates cosine distance
  and filter matching.
- `local_pgvector`: Runs against local PostgreSQL with pgvector (requires RUN_DB_TESTS=1).
- `live_cloud`: Opt-in live benchmark against live Aurora Serverless v2 and Amazon
  S3 Vectors. Requires explicit `--confirm-live-benchmark` flag; never runs automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.adapters.aws.s3_vector_store import S3VectorStore
from app.adapters.mock.vector_store import InMemoryVectorStore
from app.models.ai import VectorMatch, VectorRecord
from app.models.enums import ConfidentialityLevel
from app.ports.vector_store import VectorStore

DEMO_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def _deterministic_embedding(text: str) -> list[float]:
    """Generate a reproducible 1024-dim embedding from text without external model calls."""
    dims = 1024
    vec: list[float] = []
    for i in range(dims):
        h = hashlib.sha256(f"{text}:{i}".encode()).digest()
        val = (int.from_bytes(h[:4], "big") / 2**32) * 2.0 - 1.0
        vec.append(round(val, 6))
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [round(x / norm, 6) for x in vec]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute standard cosine similarity between two float vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


class SyntheticBenchmarkFixture:
    """Creates benchmark test records with per-run unique IDs."""

    @staticmethod
    def create_records(
        count: int = 100,
        run_id: UUID | None = None,
    ) -> list[VectorRecord]:
        benchmark_run_id = run_id or uuid4()
        num_docs = (count + 9) // 10 if count > 0 else 0
        doc_ids = [uuid4() for _ in range(num_docs)]

        records: list[VectorRecord] = []
        for i in range(count):
            doc_idx = i // 10
            doc_id = doc_ids[doc_idx]
            chunk_id = uuid4()
            content = f"Compliance control {i}: Policy statement on access control and security."
            records.append(
                VectorRecord(
                    chunk_id=str(chunk_id),
                    document_id=str(doc_id),
                    organization_id=str(DEMO_ORG_ID),
                    embedding=_deterministic_embedding(content),
                    content=content,
                    metadata={
                        "confidentiality_level": (
                            ConfidentialityLevel.INTERNAL.value
                            if i % 2 == 0
                            else ConfidentialityLevel.CONFIDENTIAL.value
                        ),
                        "section": f"{doc_idx}.{i % 10}",
                        "run_id": str(benchmark_run_id),
                    },
                )
            )
        return records


class MockFixtureS3VectorsClient:
    """Simulates S3 Vectors query_vectors by evaluating true cosine similarity and filters."""

    def __init__(self, records: list[VectorRecord]) -> None:
        self._records = records

    def get_index(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "index": {
                "indexArn": "arn:aws:s3vectors:us-east-1:123456789012:bucket/bench-bucket/index/bench-index",
                "indexName": "bench-index",
                "vectorBucketName": "bench-bucket",
                "dimension": 1024,
                "distanceMetric": "cosine",
                "metadataConfiguration": {"nonFilterableMetadataKeys": []},
            }
        }

    def put_vectors(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def query_vectors(self, **kwargs: Any) -> dict[str, Any]:
        query_vector = kwargs.get("queryVector", {}).get("float32", [])
        top_k = kwargs.get("topK", 10)
        filter_spec = kwargs.get("filter", {})

        # Parse filter requirements
        allowed_org: str | None = None
        allowed_clearances: list[str] | None = None
        allowed_docs: list[str] | None = None
        and_conditions = filter_spec.get("$and", [filter_spec])
        for cond in and_conditions:
            if "organization_id" in cond:
                allowed_org = cond["organization_id"].get("$eq")
            if "confidentiality_level" in cond:
                allowed_clearances = cond["confidentiality_level"].get("$in")
            if "document_id" in cond:
                allowed_docs = cond["document_id"].get("$in")

        scored: list[tuple[float, VectorRecord]] = []
        for rec in self._records:
            if allowed_org and str(rec.organization_id) != allowed_org:
                continue
            rec_clearance = rec.metadata.get("confidentiality_level", "internal")
            if allowed_clearances and rec_clearance not in allowed_clearances:
                continue
            if allowed_docs and str(rec.document_id) not in allowed_docs:
                continue

            sim = _cosine_similarity(query_vector, rec.embedding)
            # Distance in cosine space is 1.0 - sim
            dist = max(0.0, 1.0 - sim)
            scored.append((dist, rec))

        scored.sort(key=lambda x: x[0])

        items: list[dict[str, Any]] = []
        for dist, r in scored[:top_k]:
            items.append({
                "key": f"{r.organization_id}#{r.document_id}#{r.chunk_id}",
                "distance": dist,
                "metadata": {
                    "organization_id": str(r.organization_id),
                    "document_id": str(r.document_id),
                    "chunk_id": str(r.chunk_id),
                    "confidentiality_level": r.metadata.get("confidentiality_level", "internal"),
                    "content": r.content,
                },
            })
        return {"vectors": items}

    def list_vectors(self, **kwargs: Any) -> dict[str, Any]:
        return {"vectors": []}

    def delete_vectors(self, **kwargs: Any) -> dict[str, Any]:
        return {}


async def benchmark_store(
    name: str,
    store: VectorStore,
    queries: list[list[float]],
    top_k: int = 10,
    document_ids: list[UUID] | None = None,
) -> dict[str, Any]:
    """Execute queries against a VectorStore implementation and record latency metrics."""
    latencies_ms: list[float] = []
    results: list[list[VectorMatch]] = []

    for query_vec in queries:
        start = time.perf_counter()
        matches = await store.search(
            embedding=query_vec,
            organization_id=DEMO_ORG_ID,
            confidentiality_levels=(
                ConfidentialityLevel.INTERNAL,
                ConfidentialityLevel.CONFIDENTIAL,
            ),
            top_k=top_k,
            document_ids=document_ids,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        latencies_ms.append(elapsed_ms)
        results.append(matches)

    latencies_ms.sort()
    n = len(latencies_ms)
    p50 = latencies_ms[int(n * 0.50)] if n else 0.0
    p90 = latencies_ms[int(n * 0.90)] if n else 0.0
    p95 = latencies_ms[int(n * 0.95)] if n else 0.0
    mean_lat = statistics.mean(latencies_ms) if latencies_ms else 0.0

    return {
        "store_name": name,
        "query_count": n,
        "latency_ms": {
            "p50": round(p50, 2),
            "p90": round(p90, 2),
            "p95": round(p95, 2),
            "mean": round(mean_lat, 2),
            "min": round(min(latencies_ms), 2) if latencies_ms else 0.0,
            "max": round(max(latencies_ms), 2) if latencies_ms else 0.0,
        },
        "results": results,
    }


def compute_relative_recall(
    baseline_results: list[list[VectorMatch]],
    target_results: list[list[VectorMatch]],
    k: int = 10,
) -> float:
    """Compute top-k set overlap recall against baseline results."""
    if not baseline_results or not target_results:
        return 0.0

    overlaps: list[float] = []
    for base_matches, target_matches in zip(baseline_results, target_results, strict=False):
        base_ids = {m.chunk_id for m in base_matches[:k]}
        target_ids = {m.chunk_id for m in target_matches[:k]}
        if not base_ids:
            continue
        overlap = len(base_ids & target_ids) / len(base_ids)
        overlaps.append(overlap)

    return round(statistics.mean(overlaps), 4) if overlaps else 0.0


async def run_benchmark(args: argparse.Namespace) -> tuple[int, dict[str, Any] | None]:
    print("=" * 70)
    print("CloudGuard AI: Vector Store Benchmark (Phase 11)")
    print(f"Mode: {args.mode}")
    print(f"Target Stores: {args.stores}")
    print(f"Top-K: {args.top_k}, Query Count: {args.num_queries}")
    print("=" * 70)

    # Validate store inputs
    valid_stores = {"pgvector", "s3_vectors"}
    raw_stores = [s.strip().lower() for s in (args.stores or "").split(",") if s.strip()]
    if not raw_stores:
        print("[ERROR] --stores must specify at least one valid store ('pgvector', 's3_vectors').")
        return 1, None
    for s in raw_stores:
        if s not in valid_stores:
            print(f"[ERROR] Unknown store '{s}'. Allowed: {', '.join(sorted(valid_stores))}")
            return 1, None
    stores_to_run = raw_stores

    # Validate mode and store compatibility
    if args.mode == "synthetic_fixture":
        pass
    elif args.mode == "local_pgvector":
        for s in stores_to_run:
            if s != "pgvector":
                print(
                    f"\n[ERROR] Store '{s}' is not supported in 'local_pgvector' mode. "
                    f"Only 'pgvector' is supported locally without AWS infrastructure."
                )
                return 1, None
    elif args.mode == "live_cloud":
        for s in stores_to_run:
            if s == "pgvector":
                aurora_cluster = os.getenv("AWS_AURORA_CLUSTER_ARN")
                aurora_secret = os.getenv("AWS_AURORA_SECRET_ARN")
                if not (aurora_cluster and aurora_secret):
                    print(
                        "\n[ERROR] Store 'pgvector' in 'live_cloud' mode requires "
                        "AWS_AURORA_CLUSTER_ARN and AWS_AURORA_SECRET_ARN."
                    )
                    return 1, None
            elif s == "s3_vectors":
                bucket = args.s3_bucket or os.getenv("AWS_S3_VECTOR_BUCKET_NAME")
                index = args.s3_index or os.getenv("AWS_S3_VECTOR_INDEX_NAME")
                if not (bucket and index):
                    print(
                        "\n[ERROR] Store 's3_vectors' in 'live_cloud' mode requires "
                        "--s3-bucket/--s3-index or AWS_S3_VECTOR_BUCKET_NAME/AWS_S3_VECTOR_INDEX_NAME."
                    )
                    return 1, None

    # Validate numeric parameters
    if args.num_queries <= 0 or args.num_queries > 1000:
        print(f"[ERROR] --num-queries must be between 1 and 1000, got {args.num_queries}.")
        return 1, None
    if args.top_k <= 0 or args.top_k > 100:
        print(f"[ERROR] --top-k must be between 1 and 100, got {args.top_k}.")
        return 1, None

    if args.mode == "live_cloud" and not args.confirm_live_benchmark:
        print("\n[ERROR] Live cloud benchmark requires explicit --confirm-live-benchmark.")
        print("To protect against unintended AWS charges, live benchmark execution is gated.")
        print("Run with: python scripts/benchmark_vector_stores.py --mode live_cloud --confirm-live-benchmark\n")
        return 1, None

    records = SyntheticBenchmarkFixture.create_records(count=100)
    query_texts = [f"Search query test {i} for access control policy" for i in range(args.num_queries)]
    queries = [_deterministic_embedding(t) for t in query_texts]

    benchmark_reports: dict[str, Any] = {}

    if args.mode == "synthetic_fixture":
        print("\n" + "!" * 70)
        print("[NOTICE] Synthetic Fixture Mode: Latencies reflect in-memory Python calculations.")
        print("They do NOT reflect real database or cloud network latency.")
        print("!" * 70)

        synth_doc_ids = list({UUID(r.document_id) for r in records})

        # 1. pgvector in-memory baseline
        mock_pgvector = InMemoryVectorStore()
        await mock_pgvector.upsert(records)

        # 2. S3 Vectors mock store using true cosine similarity
        mock_client = MockFixtureS3VectorsClient(records)
        mock_s3 = S3VectorStore(
            vector_bucket_name="bench-bucket",
            index_name="bench-index",
            client=mock_client,
        )

        if "pgvector" in stores_to_run:
            print("Benchmarking pgvector (synthetic fixture)...")
            pg_res = await benchmark_store(
                "pgvector",
                mock_pgvector,
                queries,
                top_k=args.top_k,
                document_ids=synth_doc_ids,
            )
            benchmark_reports["pgvector"] = pg_res

        if "s3_vectors" in stores_to_run:
            print("Benchmarking s3_vectors (synthetic fixture)...")
            s3_res = await benchmark_store(
                "s3_vectors",
                mock_s3,
                queries,
                top_k=args.top_k,
                document_ids=synth_doc_ids,
            )
            benchmark_reports["s3_vectors"] = s3_res

    elif args.mode == "local_pgvector":
        if os.getenv("RUN_DB_TESTS") != "1":
            print("\n[ERROR] local_pgvector mode requires running PostgreSQL and RUN_DB_TESTS=1.")
            print("To run local pgvector benchmark:")
            print("  $env:RUN_DB_TESTS=\"1\"; $env:DB_USER=\"cloudguard_app\"; $env:DB_PASSWORD=\"cloudguard_app\"; python scripts/benchmark_vector_stores.py --mode local_pgvector\n")
            return 1, None

        from app.adapters.local.vector_store import SQLAlchemyVectorStore
        from app.models.enums import DocumentType, ProcessingStatus
        from app.repositories.database import tenant_session
        from app.repositories.tables import Document, DocumentChunk, User
        from sqlalchemy import delete, select

        print("\n[INFO] Connecting to local PostgreSQL with pgvector...")
        pg_store = SQLAlchemyVectorStore()

        committed_docs: list[UUID] = []
        committed_chunks: list[UUID] = []

        try:
            print(f"Creating isolated owned fixture for tenant {DEMO_ORG_ID}...")
            async with tenant_session(DEMO_ORG_ID) as session:
                user_res = await session.execute(
                    select(User.id).where(User.organization_id == DEMO_ORG_ID).limit(1)
                )
                uploader_id = user_res.scalar_one_or_none()
                if not uploader_id:
                    print(f"\n[ERROR] No user found in tenant {DEMO_ORG_ID} to own benchmark documents.")
                    return 1, None

                doc_records_by_id: dict[UUID, list[VectorRecord]] = {}
                for r in records:
                    doc_records_by_id.setdefault(UUID(r.document_id), []).append(r)

                target_doc_ids = list(doc_records_by_id.keys())
                target_chunk_ids = [UUID(r.chunk_id) for r in records]

                # Check for existing ID collisions before attempting insert
                existing_docs_res = await session.execute(
                    select(Document.id).where(
                        Document.organization_id == DEMO_ORG_ID,
                        Document.id.in_(target_doc_ids),
                    )
                )
                existing_docs = list(existing_docs_res.scalars().all())

                existing_chunks_res = await session.execute(
                    select(DocumentChunk.id).where(
                        DocumentChunk.organization_id == DEMO_ORG_ID,
                        DocumentChunk.id.in_(target_chunk_ids),
                    )
                )
                existing_chunks = list(existing_chunks_res.scalars().all())

                if existing_docs or existing_chunks:
                    print(
                        f"\n[ERROR] Benchmark ID collision detected: "
                        f"{len(existing_docs)} docs, {len(existing_chunks)} chunks already exist. "
                        f"Aborting insertion to protect existing database records."
                    )
                    return 1, None

                for doc_id, chunk_recs in doc_records_by_id.items():
                    doc = Document(
                        id=doc_id,
                        organization_id=DEMO_ORG_ID,
                        uploader_id=uploader_id,
                        filename=f"benchmark_doc_{doc_id}.txt",
                        storage_key=f"benchmark/{doc_id}.txt",
                        content_type="text/plain",
                        confidentiality_level=ConfidentialityLevel.INTERNAL,
                        document_type=DocumentType.POLICY,
                        processing_status=ProcessingStatus.READY,
                    )
                    session.add(doc)

                    for idx, cr in enumerate(chunk_recs):
                        chunk_id = UUID(cr.chunk_id)
                        chunk = DocumentChunk(
                            id=chunk_id,
                            organization_id=DEMO_ORG_ID,
                            document_id=doc_id,
                            chunk_index=idx,
                            content=cr.content,
                            token_count=10,
                            chunk_metadata=cr.metadata,
                            embedding=None,
                        )
                        session.add(chunk)

                # Commit insertion
                await session.commit()

                # ONLY track ownership AFTER successful commit
                committed_docs.extend(target_doc_ids)
                committed_chunks.extend(target_chunk_ids)

            print(f"Upserting embeddings for {len(records)} chunks...")
            async with tenant_session(DEMO_ORG_ID):
                written = await pg_store.upsert(records)

            if written == 0:
                print(f"\n[ERROR] Seeding failed: wrote 0 of {len(records)} records.")
                return 1, None
            if written != len(records):
                print(f"\n[ERROR] Seeding incomplete: wrote {written} of {len(records)} records.")
                return 1, None

            print("Benchmarking local pgvector against PostgreSQL...")
            async with tenant_session(DEMO_ORG_ID):
                # Restrict search strictly to this run's owned documents
                pg_res = await benchmark_store(
                    "pgvector",
                    pg_store,
                    queries,
                    top_k=args.top_k,
                    document_ids=committed_docs,
                )
                benchmark_reports["pgvector"] = pg_res

            # Verify results are non-empty and contain expected chunk IDs
            for result_set in pg_res["results"]:
                if not result_set:
                    print("\n[ERROR] Expected results are absent: search returned 0 results.")
                    return 1, None
            first_results = pg_res["results"][0]
            expected_chunk_ids = {r.chunk_id for r in records}
            found_chunk_ids = {m.chunk_id for m in first_results}
            if not (expected_chunk_ids & found_chunk_ids):
                print("\n[ERROR] Expected results are absent: search returned no seeded chunks.")
                return 1, None

        finally:
            if committed_chunks or committed_docs:
                print("Cleaning up only owned benchmark records from PostgreSQL...")
                async with tenant_session(DEMO_ORG_ID) as session:
                    if committed_chunks:
                        await session.execute(
                            delete(DocumentChunk).where(
                                DocumentChunk.organization_id == DEMO_ORG_ID,
                                DocumentChunk.id.in_(committed_chunks),
                            )
                        )
                    if committed_docs:
                        await session.execute(
                            delete(Document).where(
                                Document.organization_id == DEMO_ORG_ID,
                                Document.id.in_(committed_docs),
                            )
                        )
                    await session.commit()
                print("Cleanup completed successfully.")
            else:
                print("No committed benchmark records to clean up.")

    elif args.mode == "live_cloud":
        print("\n[INFO] Live cloud benchmark configured. Initializing cloud adapters...")
        if "s3_vectors" in stores_to_run:
            s3_store = S3VectorStore(
                vector_bucket_name=args.s3_bucket or os.getenv("AWS_S3_VECTOR_BUCKET_NAME") or "cloudguard-vectors-dev",
                index_name=args.s3_index or os.getenv("AWS_S3_VECTOR_INDEX_NAME") or "cloudguard-index-dev",
            )
            s3_res = await benchmark_store("s3_vectors", s3_store, queries, top_k=args.top_k)
            benchmark_reports["s3_vectors"] = s3_res
        if "pgvector" in stores_to_run:
            from app.adapters.aws.vector_store import AuroraDataAPIVectorStore
            cluster_arn = os.environ["AWS_AURORA_CLUSTER_ARN"]
            secret_arn = os.environ["AWS_AURORA_SECRET_ARN"]
            aurora_store = AuroraDataAPIVectorStore(
                resource_arn=cluster_arn,
                secret_arn=secret_arn,
                database=os.getenv("DB_NAME", "cloudguard"),
                region=os.getenv("AWS_REGION", "us-east-1"),
            )
            pg_res = await benchmark_store("pgvector", aurora_store, queries, top_k=args.top_k)
            benchmark_reports["pgvector"] = pg_res

    # Output formatted report
    print("\n" + "=" * 70)
    print(f"{'Store Name':<15} | {'p50 (ms)':<10} | {'p90 (ms)':<10} | {'p95 (ms)':<10} | {'Mean (ms)':<10}")
    print("-" * 70)
    for name, rep in benchmark_reports.items():
        lat = rep["latency_ms"]
        print(f"{name:<15} | {lat['p50']:<10} | {lat['p90']:<10} | {lat['p95']:<10} | {lat['mean']:<10}")
    print("=" * 70)

    # Calculate relative recall if both stores are present
    recall: float | None = None
    if "pgvector" in benchmark_reports and "s3_vectors" in benchmark_reports:
        recall = compute_relative_recall(
            benchmark_reports["pgvector"]["results"],
            benchmark_reports["s3_vectors"]["results"],
            k=args.top_k,
        )
        print(f"\nRelative Recall@k (s3_vectors vs. pgvector baseline): {recall * 100:.2f}%")

    print("\nEstimated Cost Model:")
    print("  Cost is unmeasured in offline/fixture mode.")
    print("  Real query inference and storage costs are tracked via Phase 10 telemetry.")

    clean_report = {
        "mode": args.mode,
        "is_simulation": args.mode == "synthetic_fixture",
        "query_count": args.num_queries,
        "top_k": args.top_k,
        "stores": {
            k: {
                "latency_ms": v["latency_ms"],
                "query_count": v["query_count"],
            }
            for k, v in benchmark_reports.items()
        },
        "relative_recall_at_k": recall,
        "estimated_cost_per_million_usd": None,
        "cost_note": (
            "Cost is unmeasured in offline/fixture mode. "
            "Real cost tracking is measured at query runtime via Phase 10 operational telemetry."
        ),
    }

    return 0, clean_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Vector store benchmark runner")
    parser.add_argument(
        "--mode",
        choices=["synthetic_fixture", "local_pgvector", "live_cloud"],
        default="synthetic_fixture",
        help="Benchmark execution mode (default: synthetic_fixture)",
    )
    parser.add_argument(
        "--stores",
        default="pgvector,s3_vectors",
        help="Comma-separated store list to benchmark (default: pgvector,s3_vectors)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Top-K vector results (default: 10)",
    )
    parser.add_argument(
        "--num-queries",
        type=int,
        default=20,
        help="Number of query evaluations (default: 20)",
    )
    parser.add_argument(
        "--confirm-live-benchmark",
        action="store_true",
        help="Explicit confirmation required to run live cloud benchmark",
    )
    parser.add_argument(
        "--s3-bucket",
        default=None,
        help="S3 Vector bucket name for live cloud mode",
    )
    parser.add_argument(
        "--s3-index",
        default=None,
        help="S3 Vector index name for live cloud mode",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="File path to persist benchmark results JSON",
    )
    args = parser.parse_args()
    code, report = asyncio.run(run_benchmark(args))
    if code == 0 and args.output_json and report is not None:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nBenchmark JSON saved to: {args.output_json}")
    sys.exit(code)


if __name__ == "__main__":
    main()

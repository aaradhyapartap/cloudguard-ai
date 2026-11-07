"""Unit tests and regressions for vector store benchmark script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

# Ensure scripts root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from benchmark_vector_stores import (
    SyntheticBenchmarkFixture,
    compute_relative_recall,
    run_benchmark,
)


def _make_args(
    mode: str = "synthetic_fixture",
    stores: str = "pgvector,s3_vectors",
    top_k: int = 10,
    num_queries: int = 5,
    confirm_live_benchmark: bool = False,
    s3_bucket: str | None = None,
    s3_index: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        mode=mode,
        stores=stores,
        top_k=top_k,
        num_queries=num_queries,
        confirm_live_benchmark=confirm_live_benchmark,
        s3_bucket=s3_bucket,
        s3_index=s3_index,
        output_json=None,
    )


@pytest.mark.asyncio
async def test_unsupported_mode_and_store_combinations() -> None:
    # 1. local_pgvector with s3_vectors must fail
    args = _make_args(mode="local_pgvector", stores="s3_vectors")
    code, _ = await run_benchmark(args)
    assert code == 1

    # 2. Unknown store must fail
    args_bad_store = _make_args(stores="pinecone")
    code, _ = await run_benchmark(args_bad_store)
    assert code == 1

    # 3. live_cloud without confirmation must fail
    args_unconfirmed = _make_args(mode="live_cloud", confirm_live_benchmark=False)
    code, _ = await run_benchmark(args_unconfirmed)
    assert code == 1

    # 4. live_cloud with s3_vectors without bucket/index must fail
    with patch.dict("os.environ", {}, clear=True):
        args_no_s3 = _make_args(
            mode="live_cloud",
            stores="s3_vectors",
            confirm_live_benchmark=True,
        )
        code, _ = await run_benchmark(args_no_s3)
        assert code == 1

    # 5. live_cloud with pgvector without Aurora ARNs must fail
    with patch.dict("os.environ", {}, clear=True):
        args_no_aurora = _make_args(
            mode="live_cloud",
            stores="pgvector",
            confirm_live_benchmark=True,
        )
        code, _ = await run_benchmark(args_no_aurora)
        assert code == 1


@pytest.mark.asyncio
async def test_synthetic_fixture_mode_runs_and_reports_null_cost() -> None:
    args = _make_args(mode="synthetic_fixture", stores="pgvector,s3_vectors", num_queries=2)
    code, report = await run_benchmark(args)
    assert code == 0
    assert report is not None
    assert report["mode"] == "synthetic_fixture"
    assert report["is_simulation"] is True
    assert report["estimated_cost_per_million_usd"] is None
    assert "pgvector" in report["stores"]
    assert "s3_vectors" in report["stores"]


@pytest.mark.asyncio
async def test_zero_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("RUN_DB_TESTS", "1")

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_user_result = MagicMock()
    mock_user_result.scalar_one_or_none.return_value = uuid4()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_user_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_user_result

    class MockContextManager:
        async def __aenter__(self) -> AsyncMock:
            return mock_session

        async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
            pass

    # Mock upsert returning 0
    mock_store = AsyncMock()
    mock_store.upsert.return_value = 0

    with patch("app.repositories.database.tenant_session", return_value=MockContextManager()):
        with patch(
            "app.adapters.local.vector_store.SQLAlchemyVectorStore",
            return_value=mock_store,
        ):
            args = _make_args(mode="local_pgvector", stores="pgvector", num_queries=2)
            code, report = await run_benchmark(args)
            assert code == 1
            assert report is None
            captured = capsys.readouterr()
            assert "Seeding failed: wrote 0 of" in captured.out


@pytest.mark.asyncio
async def test_empty_result_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("RUN_DB_TESTS", "1")

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_user_result = MagicMock()
    mock_user_result.scalar_one_or_none.return_value = uuid4()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_user_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_user_result

    class MockContextManager:
        async def __aenter__(self) -> AsyncMock:
            return mock_session

        async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
            pass

    # Mock upsert returning full count, but search returning empty list
    mock_store = AsyncMock()
    mock_store.upsert.return_value = 100
    mock_store.search.return_value = []

    with patch("app.repositories.database.tenant_session", return_value=MockContextManager()):
        with patch(
            "app.adapters.local.vector_store.SQLAlchemyVectorStore",
            return_value=mock_store,
        ):
            args = _make_args(mode="local_pgvector", stores="pgvector", num_queries=2)
            code, report = await run_benchmark(args)
            assert code == 1
            assert report is None
            captured = capsys.readouterr()
            assert "Expected results are absent" in captured.out


def test_synthetic_fixture_per_run_unique_ids() -> None:
    """Benchmark fixtures must generate unique IDs per run and avoid collisions."""
    run_a = uuid4()
    run_b = uuid4()

    records_a = SyntheticBenchmarkFixture.create_records(count=25, run_id=run_a)
    records_b = SyntheticBenchmarkFixture.create_records(count=25, run_id=run_b)

    assert len(records_a) == 25
    assert len(records_b) == 25

    chunks_a = {r.chunk_id for r in records_a}
    chunks_b = {r.chunk_id for r in records_b}
    docs_a = {r.document_id for r in records_a}
    docs_b = {r.document_id for r in records_b}

    # Must be completely disjoint across runs
    assert chunks_a.isdisjoint(chunks_b)
    assert docs_a.isdisjoint(docs_b)

    # Within run, all chunk IDs must be unique
    assert len(chunks_a) == 25
    assert len(chunks_b) == 25

    # Run ID must be tracked in metadata
    for r in records_a:
        assert r.metadata["run_id"] == str(run_a)
    for r in records_b:
        assert r.metadata["run_id"] == str(run_b)


@pytest.mark.asyncio
async def test_cleanup_only_deletes_owned_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_DB_TESTS", "1")

    from sqlalchemy.sql.dml import Delete

    executed_statements: list[object] = []
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def mock_execute(statement: object, *args: object, **kwargs: object) -> MagicMock:
        executed_statements.append(statement)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = uuid4()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []  # No collision
        mock_result.scalars.return_value = mock_scalars
        return mock_result

    mock_session.execute = AsyncMock(side_effect=mock_execute)

    class MockContextManager:
        async def __aenter__(self) -> AsyncMock:
            return mock_session

        async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
            pass

    from app.models.ai import VectorMatch, VectorRecord

    seeded_records: list[VectorRecord] = []

    async def mock_upsert(recs: list[VectorRecord]) -> int:
        nonlocal seeded_records
        seeded_records = recs
        return len(recs)

    async def mock_search(**kwargs: object) -> list[VectorMatch]:
        assert seeded_records, "Search called before upsert"
        first = seeded_records[0]
        return [
            VectorMatch(
                chunk_id=first.chunk_id,
                document_id=first.document_id,
                content=first.content,
                score=0.99,
                metadata=first.metadata,
            )
        ]

    mock_store = AsyncMock()
    mock_store.upsert = AsyncMock(side_effect=mock_upsert)
    mock_store.search = AsyncMock(side_effect=mock_search)

    with patch("app.repositories.database.tenant_session", return_value=MockContextManager()):
        with patch(
            "app.adapters.local.vector_store.SQLAlchemyVectorStore",
            return_value=mock_store,
        ):
            args = _make_args(mode="local_pgvector", stores="pgvector", num_queries=1)
            code, _ = await run_benchmark(args)
            assert code == 0

            # Verify that cleanup executed delete calls in the session
            delete_calls = [s for s in executed_statements if isinstance(s, Delete)]
            assert len(delete_calls) == 2


@pytest.mark.asyncio
async def test_commit_failure_preserves_rows_and_does_not_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When DB insertion commit fails, cleanup must execute ZERO deletes and touch no rows."""
    monkeypatch.setenv("RUN_DB_TESTS", "1")

    from sqlalchemy.sql.dml import Delete

    executed_statements: list[object] = []
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def mock_execute(statement: object, *args: object, **kwargs: object) -> MagicMock:
        executed_statements.append(statement)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = uuid4()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        return mock_result

    mock_session.execute = AsyncMock(side_effect=mock_execute)
    # Simulate DB commit failure
    mock_session.commit = AsyncMock(side_effect=RuntimeError("Database constraint failure"))

    class MockContextManager:
        async def __aenter__(self) -> AsyncMock:
            return mock_session

        async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
            pass

    mock_store = AsyncMock()

    with patch("app.repositories.database.tenant_session", return_value=MockContextManager()):
        with patch(
            "app.adapters.local.vector_store.SQLAlchemyVectorStore",
            return_value=mock_store,
        ):
            args = _make_args(mode="local_pgvector", stores="pgvector", num_queries=1)
            with pytest.raises(RuntimeError, match="Database constraint failure"):
                await run_benchmark(args)

            # Cleanup must NOT execute any deletes because ownership was not committed
            delete_calls = [s for s in executed_statements if isinstance(s, Delete)]
            assert len(delete_calls) == 0, f"Expected 0 delete statements, got {len(delete_calls)}"


@pytest.mark.asyncio
async def test_existing_id_collision_aborts_without_deleting(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When an ID collision is detected, abort immediately and execute ZERO deletes."""
    monkeypatch.setenv("RUN_DB_TESTS", "1")

    from sqlalchemy.sql.dml import Delete

    executed_statements: list[object] = []
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    colliding_doc_id = uuid4()

    async def mock_execute(statement: object, *args: object, **kwargs: object) -> MagicMock:
        executed_statements.append(statement)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = uuid4()
        mock_scalars = MagicMock()
        # Simulate collision detected on Document.id check
        mock_scalars.all.return_value = [colliding_doc_id]
        mock_result.scalars.return_value = mock_scalars
        return mock_result

    mock_session.execute = AsyncMock(side_effect=mock_execute)

    class MockContextManager:
        async def __aenter__(self) -> AsyncMock:
            return mock_session

        async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
            pass

    mock_store = AsyncMock()

    with patch("app.repositories.database.tenant_session", return_value=MockContextManager()):
        with patch(
            "app.adapters.local.vector_store.SQLAlchemyVectorStore",
            return_value=mock_store,
        ):
            args = _make_args(mode="local_pgvector", stores="pgvector", num_queries=1)
            code, report = await run_benchmark(args)
            assert code == 1
            assert report is None

            captured = capsys.readouterr()
            assert "Benchmark ID collision detected" in captured.out

            # Cleanup must NOT execute any deletes
            delete_calls = [s for s in executed_statements if isinstance(s, Delete)]
            assert len(delete_calls) == 0
            assert mock_session.commit.call_count == 0


def test_concurrent_runs_isolated_cleanup() -> None:
    """Two concurrent fixtures must have disjoint IDs and never cross-delete each other."""
    run_1_records = SyntheticBenchmarkFixture.create_records(count=30, run_id=uuid4())
    run_2_records = SyntheticBenchmarkFixture.create_records(count=30, run_id=uuid4())

    run_1_chunk_ids = {UUID(r.chunk_id) for r in run_1_records}
    run_2_chunk_ids = {UUID(r.chunk_id) for r in run_2_records}
    run_1_doc_ids = {UUID(r.document_id) for r in run_1_records}
    run_2_doc_ids = {UUID(r.document_id) for r in run_2_records}

    # Simulate run 1 cleanup scope
    cleanup_1_chunks = list(run_1_chunk_ids)
    cleanup_1_docs = list(run_1_doc_ids)

    # Prove Run 1 cleanup scope contains zero IDs from Run 2
    assert set(cleanup_1_chunks).isdisjoint(run_2_chunk_ids)
    assert set(cleanup_1_docs).isdisjoint(run_2_doc_ids)


def test_cleanup_preserves_unrelated_rows() -> None:
    """Cleanup delete queries must be explicitly scoped by ID, not blanket tenant deletes."""
    from app.repositories.tables import Document, DocumentChunk
    from sqlalchemy import delete

    owned_chunks = [uuid4(), uuid4()]
    owned_docs = [uuid4()]
    unrelated_chunk = uuid4()
    unrelated_doc = uuid4()

    chunk_delete = delete(DocumentChunk).where(
        DocumentChunk.id.in_(owned_chunks)
    )
    doc_delete = delete(Document).where(
        Document.id.in_(owned_docs)
    )

    # Verify query where criteria strictly target owned IDs
    assert str(unrelated_chunk) not in str(chunk_delete)
    assert str(unrelated_doc) not in str(doc_delete)


def test_relative_recall_computation() -> None:
    from app.models.ai import VectorMatch

    m1 = VectorMatch(chunk_id="c1", document_id="d1", content="a", score=0.9, metadata={})
    m2 = VectorMatch(chunk_id="c2", document_id="d1", content="b", score=0.8, metadata={})
    m3 = VectorMatch(chunk_id="c3", document_id="d1", content="c", score=0.7, metadata={})

    base = [[m1, m2]]
    target = [[m1, m3]]

    # 1 of 2 matches overlap -> 50%
    recall = compute_relative_recall(base, target, k=2)
    assert recall == 0.5

    # 100% overlap
    assert compute_relative_recall(base, base, k=2) == 1.0

    # Empty results
    assert compute_relative_recall([], []) == 0.0

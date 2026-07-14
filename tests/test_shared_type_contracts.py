"""Runtime-visible annotations for cross-module service boundaries."""

from typing import Optional, get_type_hints

from src import catalog_sync, contribution_sync, inventory_sync, sync_all, tag_service
from src.contracts import (
    CatalogApplyCallback,
    DeltaSyncResult,
    MessageReporter,
    ProgressCallback,
    SyncAllResult,
    TagCandidate,
    TagMetadata,
)


def test_catalog_client_uses_shared_progress_and_apply_contracts():
    constructor = get_type_hints(catalog_sync.CatalogClient.__init__)
    delta_pull = get_type_hints(catalog_sync.CatalogClient._pull_delta)

    assert constructor["progress_callback"] == Optional[ProgressCallback]
    assert delta_pull["apply_row"] == CatalogApplyCallback
    assert delta_pull["return"] is DeltaSyncResult


def test_sync_orchestrators_share_callback_and_result_contracts():
    sync_all_hints = get_type_hints(sync_all.main_run_sync_all)
    inventory_reporter = get_type_hints(inventory_sync._progress_reporter)
    contribution_reporter = get_type_hints(contribution_sync._progress_reporter)

    assert sync_all_hints["progress_callback"] == Optional[ProgressCallback]
    assert sync_all_hints["return"] is SyncAllResult
    assert inventory_reporter["return"] == MessageReporter
    assert contribution_reporter["return"] == MessageReporter


def test_tag_service_exposes_candidate_and_metadata_contracts():
    identify = get_type_hints(tag_service.identify_file)
    read_current = get_type_hints(tag_service.read_current_tags)
    write = get_type_hints(tag_service.write_tags)

    assert identify["return"] == Optional[TagCandidate]
    assert read_current["return"] is TagMetadata
    assert write["meta"] is TagMetadata

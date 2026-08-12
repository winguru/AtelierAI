from backend.routers.collections import _summarize_civitai_batch_results


def test_batch_summary_only_confirms_gallery_backed_images():
    requested = list(range(1, 10))
    results = [
        {"image_id": 1, "images_added": 1, "image_db_id": 101, "error": None},
        {"image_id": 2, "images_recovered": 1, "image_db_id": 102, "error": None},
        {
            "image_id": 3,
            "images_skipped": 1,
            "skip_reason": "existing_source_url",
            "existing_image_id": 103,
            "error": None,
        },
        {
            "image_id": 4,
            "images_skipped": 1,
            "skip_reason": "placeholder_source_url",
            "existing_image_id": 104,
            "error": None,
        },
        {
            "image_id": 5,
            "images_skipped": 1,
            "skip_reason": "tombstoned_source_url",
            "existing_image_id": 105,
            "error": None,
        },
        {
            "image_id": 6,
            "images_skipped": 1,
            "skip_reason": "remote_not_found",
            "skip_message": "Image was removed upstream.",
            "image_db_id": 4785,
            "placeholder_created": True,
            "placeholder_image_id": 4785,
            "error": None,
        },
        {"image_id": 7, "error": "Network timeout", "images_added": 0},
        {"image_id": 8, "cancelled": True, "error": None, "images_added": 0},
        # Edge case: image_db_id set but images_added=0 and no skip reason
        # (should NOT be treated as imported — no real file was added)
        {"image_id": 9, "image_db_id": 109, "images_added": 0, "error": None},
    ]

    summary = _summarize_civitai_batch_results(requested, results)

    assert summary["imported_ids"] == [1, 2]
    assert summary["existing_ids"] == [3]
    assert summary["failed_ids"] == [4, 5, 6, 7, 8, 9]
    assert summary["imported"] == 2
    assert summary["existing"] == 1
    assert summary["failed"] == 6
    assert summary["skipped"] == 7


def test_batch_summary_marks_missing_results_failed():
    summary = _summarize_civitai_batch_results(
        [10, 11],
        [{"image_id": 10, "images_added": 1, "image_db_id": 110}],
    )

    assert summary["imported_ids"] == [10]
    assert summary["failed_ids"] == [11]
    assert summary["failures"][-1] == {
        "image_id": 11,
        "reason": "No import result returned.",
    }


def test_batch_summary_counts_cached_import_as_imported():
    summary = _summarize_civitai_batch_results(
        [20],
        [
            {
                "image_id": 20,
                "image_db_id": 200,
                "images_added": 1,
                "images_skipped": 0,
                "skip_reason": "cached_import",
                "error": None,
                "cancelled": False,
            }
        ],
    )

    assert summary["imported_ids"] == [20]
    assert summary["failed_ids"] == []
    assert summary["imported"] == 1

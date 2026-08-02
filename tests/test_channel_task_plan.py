from zf.web.channel_task_plan import normalize_channel_task_submit_payload


def test_channel_task_plan_missing_authority_points_to_trusted_entry() -> None:
    payload, details, error = normalize_channel_task_submit_payload({
        "title": "Deliver the confirmed PRD",
    })

    assert payload == {}
    assert details == {}
    assert "missing authority field(s)" in error
    assert "Channel Details" in error
    assert "Create Task from PRD" in error

"""Explicit disposition of verified length failures, without replay or repair."""

POLICY = "retain-length-failure-v1"


def review_identity(state, key):
    attempt = state["attempts"][key]
    return {
        "policy": POLICY,
        "protocol_sha256": state["protocol_sha256"],
        "request_key": key,
        "attempt_id": attempt["attempt_id"],
        "request_sha256": attempt["request_sha256"],
        "manifest_sha256": attempt["manifest_sha256"],
        "charged_usd": attempt["charged_usd"],
    }


def require_reviewable(attempt, receipt):
    """Caller must first verify the receipt and its accounting against the ledger."""
    choices = receipt["response"].get("choices", [])
    if (
        attempt["state"] != "invalid"
        or attempt["outcome"]["error"] != "incomplete_response"
        or attempt["outcome"]["cost_usd"] is None
        or not attempt.get("tracking_verified")
        or not attempt.get("tracking", {}).get("zenml_run_id")
        or not attempt.get("tracking", {}).get("mlflow_run_id")
        or len(choices) != 1
        or choices[0].get("finish_reason") != "length"
    ):
        raise ValueError(
            "Only a verified length failure with known cost and verified tracking can be reviewed"
        )


def validate_reviews(state, receipts):
    for key, review in state.get("failure_reviews", {}).items():
        if key not in receipts:
            raise ValueError("Failure review has no verified receipt")
        require_reviewable(state["attempts"][key], receipts[key])
        if (
            {k: v for k, v in review.items() if k != "reviewed_at"}
            != review_identity(state, key)
            or not isinstance(review.get("reviewed_at"), str)
            or not review["reviewed_at"]
        ):
            raise ValueError("Failure review differs from its verified attempt")

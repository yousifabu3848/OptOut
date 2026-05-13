"""Manual opt-out handler — opens the broker's opt-out page and guides the user.

Not yet implemented in the MVP.
"""

from __future__ import annotations

from .. import SubmissionFailed


def run(broker, profile, submission, *, _config=None):  # type: ignore[no-untyped-def]
    raise SubmissionFailed(
        f"The 'manual' method handler is not yet implemented. "
        f"To opt out of {broker.name}, visit {broker.opt_out_url or broker.domain} "
        f"and follow the opt-out process manually."
    )

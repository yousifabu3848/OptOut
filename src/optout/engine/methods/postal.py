"""Postal opt-out handler — generates a PDF letter for the user to mail.

Not yet implemented in the MVP. Listed in the method registry so that
postal-method brokers load and validate correctly; submissions will be
marked failed with an explanatory message until this is built.
"""

from __future__ import annotations

from .. import SubmissionFailed


def run(broker, profile, submission, *, _config=None):  # type: ignore[no-untyped-def]
    raise SubmissionFailed(
        f"The 'postal' method handler is not yet implemented. "
        f"To opt out of {broker.name}, visit {broker.opt_out_postal_address or 'their website'} "
        f"and submit a written request manually."
    )

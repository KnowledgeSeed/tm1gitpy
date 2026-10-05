from typing import Any
from urllib.parse import unquote


def element_reference_id_from_payload(payload: Any) -> str:
    """Return the tm1git-style element reference id of a subset element payload.

    ``@id`` is the on-disk tm1git form and is returned as-is. ``@odata.id`` is
    the TM1 REST form, a URL whose object names are percent-encoded, so it is
    decoded to match what TM1 Git writes.
    """
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        element_id = payload.get("@id")
        if isinstance(element_id, str) and element_id:
            return element_id
        odata_id = payload.get("@odata.id")
        if isinstance(odata_id, str):
            return unquote(odata_id)
    raise ValueError(f"Unable to resolve subset element reference id from payload: {payload!r}")

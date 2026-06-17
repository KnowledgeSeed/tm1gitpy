"""JSON serialization helpers matching tm1git-style layout (near model types)."""

from __future__ import annotations

import io
import json
from typing import AbstractSet, Any, Optional, TextIO

# Keys (at any object depth) that use ``"key" : value`` instead of ``"key":value``.
MDX_VIEW_JSON_SPACED_COLON_KEYS: frozenset[str] = frozenset(
    {
        "FormatString",
        "Meta",
        "Aliases",
        "ContextSets",
        "ExpandAboves",
    }
)


def _key_name_for_style(key: Any) -> str:
    """JSON object keys are strings in Python; normalize for set lookup."""
    if isinstance(key, str):
        return key
    return str(key)


def _colon_after_key(key: Any, spaced_colon_keys: AbstractSet[str]) -> str:
    """Return ``json.dumps(key) + ':'`` or ``json.dumps(key) + ' : '`` per *spaced_colon_keys*."""
    kj = json.dumps(key, ensure_ascii=False)
    if _key_name_for_style(key) in spaced_colon_keys:
        return kj + " : "
    return kj + ":"


def dump_as_tm1git(
    x: Any,
    fp: TextIO,
    level: int = 0,
    *,
    spaced_colon_keys: Optional[AbstractSet[str]] = None,
) -> None:
    """
    Write *x* (dict, list, or JSON-serializable scalar) to *fp* in a tm1git-like indented form.

    *spaced_colon_keys*: names of object keys (string keys, any nesting depth) for which the
    separator is ``" : "`` (space, colon, space) instead of ``":"`` (compact). Values that are
    non-empty dicts/lists still start on the following line after the colon part. Empty ``{}``
    and ``[]`` are written inline (e.g. ``"Titles":[]``).
    """
    spaced: AbstractSet[str] = spaced_colon_keys or frozenset()
    ind = "\t" * level

    if isinstance(x, dict):
        if not x:
            fp.write("{}")
            return
        fp.write("{\n")
        items = list(x.items())
        for i, (k, v) in enumerate(items):
            fp.write(ind + "\t" + _colon_after_key(k, spaced))
            if isinstance(v, dict):
                if not v:
                    fp.write("{}")
                else:
                    fp.write("\n" + "\t" * (level + 1))
                    dump_as_tm1git(v, fp, level + 1, spaced_colon_keys=spaced)
            elif isinstance(v, list):
                if not v:
                    fp.write("[]")
                else:
                    fp.write("\n" + "\t" * (level + 1))
                    dump_as_tm1git(v, fp, level + 1, spaced_colon_keys=spaced)
            else:
                fp.write(json.dumps(v, ensure_ascii=False))
            if i != len(items) - 1:
                fp.write(",")
            fp.write("\n")
        fp.write(ind + "}")

    elif isinstance(x, list):
        if not x:
            fp.write("[]")
            return
        fp.write("[\n")
        for i, v in enumerate(x):
            fp.write(ind + "\t")
            if isinstance(v, dict):
                if not v:
                    fp.write("{}")
                else:
                    dump_as_tm1git(v, fp, level + 1, spaced_colon_keys=spaced)
            elif isinstance(v, list):
                if not v:
                    fp.write("[]")
                else:
                    dump_as_tm1git(v, fp, level + 1, spaced_colon_keys=spaced)
            else:
                fp.write(json.dumps(v, ensure_ascii=False))
            if i != len(x) - 1:
                fp.write(",")
            fp.write("\n")
        fp.write(ind + "]")

    else:
        fp.write(json.dumps(x, ensure_ascii=False))


def dumps_tm1git(
    x: Any,
    *,
    level: int = 0,
    spaced_colon_keys: Optional[AbstractSet[str]] = None,
) -> str:
    """Serialize *x* with :func:`dump_as_tm1git` and return the result as a string."""
    buf = io.StringIO()
    dump_as_tm1git(x, buf, level, spaced_colon_keys=spaced_colon_keys)
    return buf.getvalue()

import io
import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

import TM1py
from TM1py.Services import TM1Service
from requests import Response

from tm1_git_py.model.tm1git_json import MDX_VIEW_JSON_SPACED_COLON_KEYS, dump_as_tm1git


# {
# 	"@type":"MDXView",
# 	"Name":"CsoportosFlatSubsetTechnical",
# 	"MDX@Code.link":"CsoportosFlatSubsetTechnical.mdx"
# }


class MDXView:
    def __init__(
        self,
        name,
        mdx,
        format_string="0.#########",
        meta: Optional[dict] = None,
    ):
        self.type = 'MDXView'
        self.name = name
        self.mdx = mdx
        self.format_string = format_string
        self.meta = meta if meta is not None else {
            "Aliases": {},
            "ContextSets": {},
            "ExpandAboves": {},
        }

    def as_json(self):
        payload: Dict[str, Any] = {
            "@type": self.type,
            "Name": self.name,
            "MDX@Code.link": self.name + ".mdx",
            "FormatString": self.format_string,
            "Meta": self.meta,
        }
        buf = io.StringIO()
        dump_as_tm1git(payload, buf, spaced_colon_keys=MDX_VIEW_JSON_SPACED_COLON_KEYS)
        return buf.getvalue()

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, MDXView):
            return NotImplemented
        if self.name != other.name:
            return False
        if self.format_string != other.format_string:
            return False
        if self.meta != other.meta:
            return False
        remove_newlines = str.maketrans(' ', ' ', '\r\n')
        if self.mdx.translate(remove_newlines) != other.mdx.translate(remove_newlines):
            return False
        return True

    def __hash__(self) -> int:
        return hash((self.name, self.mdx))
    
    def __repr__(self):
        return f"{self.type}('{self.name}')"
    
    def to_dict(self):
        return {
            "Name": self.name,
            "MDX": self.mdx,
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any]
    ) -> "MDXView":

        name = data.get("name") or data.get("Name")
        mdx = data.get("mdx") or data.get("MDX") or ""
        format_string = data.get("format_string") or data.get("FormatString") or "0.#########"
        meta_raw = data.get("Meta")
        meta = dict(meta_raw) if isinstance(meta_raw, dict) else None
        return cls(name=name, mdx=mdx, format_string=format_string, meta=meta)

    @staticmethod
    def uri_for(cube_name: str, view_name: str) -> str:
        return f"Cubes('{cube_name}')/Views('{view_name}')"

    def uri(self, cube_name: str) -> Optional[str]:
        if not cube_name or not self.name:
            return None
        return self.uri_for(cube_name, self.name)

    
# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def _view_context_from_uri(uri: str) -> Tuple[str, str]:
    match = re.search(r"^Cubes\('([^']+)'\)/Views\('([^']+)'\)$", uri or "")
    if not match:
        raise ValueError(f"Invalid mdx view uri format: '{uri}'")
    cube_name, view_name = match.groups()
    return cube_name, view_name


def create_mdxview(tm1_service: TM1Service, mdx_view: MDXView, uri: Optional[str] = None) -> Response:
    cube_name, _ = _view_context_from_uri(uri)
    mdx_view_object = TM1py.MDXView(cube_name=cube_name, view_name=mdx_view.name, MDX=mdx_view.mdx)
    logger.info(f"Creating MDXView: {mdx_view.name} for Cube: {cube_name}.")
    return tm1_service.views.create(mdx_view_object)


def update_mdxview(tm1_service: TM1Service, mdx_view: MDXView, uri: Optional[str] = None) -> Response:
    cube_name, _ = _view_context_from_uri(uri)

    mdx_view_object = tm1_service.views.get_mdx_view(cube_name=cube_name, view_name=mdx_view.name)
    mdx_view_object.mdx = mdx_view.mdx
    logger.info(f"Updating MDXView: {mdx_view.name} for Cube: {cube_name}.")
    return tm1_service.views.update(mdx_view_object)


def delete_mdxview(tm1_service: TM1Service, mdx_view: MDXView, uri: Optional[str] = None) -> Response:
    cube_name, _ = _view_context_from_uri(uri)
    logger.info(f"Deleting View: {mdx_view.name} from Cube: {cube_name}.")
    return tm1_service.views.delete(view_name=mdx_view.name, cube_name=cube_name)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between tm1_git_py and TI processes for CRUD operations
# ------------------------------------------------------------------------------------------------------------

def _escape_ti(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).replace("'", "''")


def build_mdxview_create_ti(mdx_view: MDXView, uri: Optional[str] = None) -> str:
    """
    Generates TI code to create an MDX View.
    Notes:
        Uses 'ViewCreateByMDX' (Available in Planning Analytics / TM1 v11+).
        AsTemp: 0 = Permanent
    """

    cube_name, _ = _view_context_from_uri(uri)

    cube_clean = _escape_ti(cube_name)
    view_clean = _escape_ti(mdx_view.name)
    mdx_clean = _escape_ti(mdx_view.mdx)

    lines = [
        f"# --- Create MDX View: {view_clean} in Cube: {cube_clean} ---",
        f"IF( ViewExists('{cube_clean}', '{view_clean}') = 0 );",
        f"    ViewCreateByMDX('{cube_clean}', '{view_clean}', '{mdx_clean}', 0);",
        "ENDIF;"
    ]

    return "\r\n".join(lines)


def build_mdxview_update_ti(mdx_view: MDXView, uri: Optional[str] = None) -> str:
    """
    Generates TI code to update an MDX View.
    Strategy: Delete existing view -> Recreate with new MDX.
    This ensures type safety (converting Static -> MDX if necessary).
    """
    cube_name, _ = _view_context_from_uri(uri)

    cube_clean = _escape_ti(cube_name)
    view_clean = _escape_ti(mdx_view.name)
    mdx_clean = _escape_ti(mdx_view.mdx)

    lines = [
        f"# --- Update MDX View: {view_clean} in Cube: {cube_clean} ---",
        build_mdxview_delete_ti(mdx_view, uri),
        build_mdxview_create_ti(mdx_view, uri),
    ]

    return "\r\n".join(lines)


def build_mdxview_delete_ti(mdx_view: MDXView, uri: Optional[str] = None) -> str:
    """
    Generates TI code to delete an MDX View.
    """

    cube_name, _ = _view_context_from_uri(uri)

    cube_clean = _escape_ti(cube_name)
    view_clean = _escape_ti(mdx_view.name)

    lines = [
        f"# --- Delete MDX View: {view_clean} in Cube: {cube_clean} ---",
        f"IF( ViewExists('{cube_clean}', '{view_clean}') = 1 );",
        f"    ViewDestroy('{cube_clean}', '{view_clean}');",
        "ENDIF;"
    ]

    return "\r\n".join(lines)

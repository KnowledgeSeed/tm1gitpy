import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

import TM1py
from TM1py.Services import TM1Service
from requests import Response


# {
# 	"@type":"MDXView",
# 	"Name":"CsoportosFlatSubsetTechnical",
# 	"MDX@Code.link":"CsoportosFlatSubsetTechnical.mdx"
# }


class MDXView:
    def __init__(self, name, mdx, source_path: str):
        self.type = 'MDXView'
        self.name = name
        self.mdx = mdx
        self.source_path = source_path

    def as_json(self):
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "MDX@Code.link": self.name + '.mdx'
        }, indent='\t')

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, MDXView):
            return NotImplemented
        if self.name != other.name:
            return False
        remove_newlines = str.maketrans(' ', ' ', '\r\n')
        if self.mdx.translate(remove_newlines) != other.mdx.translate(remove_newlines):
            return False
        return True

    def __hash__(self) -> int:
        return hash((self.name, self.mdx))
    
    def to_dict(self):
        return {
            'name': self.name,
            'mdx': self.mdx
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any],
            *,
            source_path: Optional[str] = None,
            cube_name: Optional[str] = None
    ) -> "MDXView":

        name = data.get("name") or data.get("Name")
        mdx = data.get("mdx") or data.get("MDX") or ""
        resolved_path = source_path
        if resolved_path is None and cube_name and name:
            resolved_path = f"cubes/{cube_name}.views/{name}.json"
        if resolved_path is None:
            raise ValueError("MDXView.from_dict requires a source_path or cube context.")
        return cls(name=name, mdx=mdx, source_path=resolved_path)

    
# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def _view_context_from_path(source_path: str) -> Tuple[str, str]:
    cube_name = re.search(r'/([\w}]*)(.views)', source_path).group(1)
    view_name = re.search(r"/([^/]+)\.json$", source_path).group(1)
    return cube_name, view_name


def create_mdx_view(tm1_service: TM1Service, mdx_view: MDXView) -> Response:
    cube_name, _ = _view_context_from_path(mdx_view.source_path)
    mdx_view_object = TM1py.MDXView(cube_name=cube_name, view_name=mdx_view.name, MDX=mdx_view.mdx)
    logger.info(f"Creating MDXView: {mdx_view.name} for Cube: {cube_name}.")
    return tm1_service.views.create(mdx_view_object)


def update_mdx_view(tm1_service: TM1Service, mdx_view: Dict[str, Any]) -> Response:
    mdx_view_new = mdx_view.get('new')

    cube_name, _ = _view_context_from_path(mdx_view_new.source_path)

    mdx_view_object = tm1_service.views.get_mdx_view(cube_name=cube_name, view_name=mdx_view_new.name)
    mdx_view_object.mdx = mdx_view_new.mdx
    logger.info(f"Updating MDXView: {mdx_view_new.name} for Cube: {cube_name}.")
    return tm1_service.views.update(mdx_view_object)


def delete_mdx_view(tm1_service: TM1Service, mdx_view: MDXView) -> Response:
    cube_name, _ = _view_context_from_path(mdx_view.source_path)
    logger.info(f"Deleting View: {mdx_view.name} from Cube: {cube_name}.")
    return tm1_service.views.delete(mdx_view.name)

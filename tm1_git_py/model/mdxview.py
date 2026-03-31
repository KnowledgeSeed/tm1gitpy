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
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "MDX@Code.link": self.name + '.mdx',
            "FormatString": self.format_string,
            "Meta": self.meta
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
    
    def __repr__(self):
        return f"{self.type}('{self.name}')"
    
    def to_dict(self):
        return {
            'name': self.name,
            'mdx': self.mdx
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any]
    ) -> "MDXView":

        name = data.get("name") or data.get("Name")
        mdx = data.get("mdx") or data.get("MDX") or ""
        return cls(name=name, mdx=mdx)

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

def _view_context_from_path(source_path: str) -> Tuple[str, str]:
    cube_name = re.search(r'/([\w}]*)(.views)', source_path).group(1)
    view_name = re.search(r"/([^/]+)\.json$", source_path).group(1)
    return cube_name, view_name


def create_mdxview(tm1_service: TM1Service, mdx_view: MDXView, source_path: Optional[str] = None) -> Response:
    cube_name, _ = _view_context_from_path(source_path)
    mdx_view_object = TM1py.MDXView(cube_name=cube_name, view_name=mdx_view.name, MDX=mdx_view.mdx)
    logger.info(f"Creating MDXView: {mdx_view.name} for Cube: {cube_name}.")
    return tm1_service.views.create(mdx_view_object)


def update_mdxview(tm1_service: TM1Service, mdx_view: MDXView, source_path: Optional[str] = None) -> Response:
    cube_name, _ = _view_context_from_path(source_path)

    mdx_view_object = tm1_service.views.get_mdx_view(cube_name=cube_name, view_name=mdx_view.name)
    mdx_view_object.mdx = mdx_view.mdx
    logger.info(f"Updating MDXView: {mdx_view.name} for Cube: {cube_name}.")
    return tm1_service.views.update(mdx_view_object)


def delete_mdxview(tm1_service: TM1Service, mdx_view: MDXView, source_path: Optional[str] = None) -> Response:
    cube_name, _ = _view_context_from_path(source_path)
    logger.info(f"Deleting View: {mdx_view.name} from Cube: {cube_name}.")
    return tm1_service.views.delete(view_name=mdx_view.name, cube_name=cube_name)

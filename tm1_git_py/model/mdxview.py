import json
from typing import Any, Dict

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
    
    
# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------


def create_mdx_view(tm1_service: TM1Service, mdx_view: MDXView, cube_name: str) -> Response:
    mdx_view_object = TM1py.MDXView(cube_name=cube_name, view_name=mdx_view.name, MDX=mdx_view.mdx)
    return tm1_service.views.create(mdx_view_object)


def update_mdx_view(tm1_service: TM1Service, mdx_view: MDXView, cube_name: str) -> Response:
    if tm1_service.views.exists(cube_name=cube_name, view_name=mdx_view.name):
        mdx_view_object = tm1_service.views.get_mdx_view(cube_name=cube_name, view_name=mdx_view.name)
        mdx_view_object.mdx = mdx_view.mdx
        return tm1_service.views.update(mdx_view_object)
    else:
        raise ValueError(f"Cannot update view '{mdx_view.name}', view does not exist")


def delete_mdx_view(tm1_service: TM1Service, mdx_view_name: str) -> Response:
    return tm1_service.views.delete(mdx_view_name)
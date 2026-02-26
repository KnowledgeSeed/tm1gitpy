import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

import TM1py
from TM1py.Services import TM1Service
from requests import Response

# {
# 	"@type":"NativeView",
# 	"Name":"TestCube3WithView_view2",
# 	"Columns":
# 	[
# 		{
# 			"Subset":
# 			{
# 				"Hierarchy":
# 				{
# 					"@id":"Dimensions('TestDim2')/Hierarchies('TestDim2')"
# 				},
# 				"Expression":"{[TestDim2].[TestDim2].Members}"
# 			}
# 		}
# 	],
# 	"Rows":
# 	[
# 		{
# 			"Subset":
# 			{
# 				"Hierarchy":
# 				{
# 					"@id":"Dimensions('TestDim1')/Hierarchies('TestDim1')"
# 				},
# 				"Expression":"{[TestDim1].[TestDim1].Members}"
# 			}
# 		}
# 	],
# 	"Titles":[],
# 	"SuppressEmptyColumns":true,
# 	"SuppressEmptyRows":true,
# 	"FormatString":"0.#########"
# }


class NativeView:
    def __init__(self, name, columns, rows, titles, suppress_empty_columns, suppress_empty_rows, format_string, source_path: str):
        self.type = 'NativeView'
        self.name = name
        self.columns = [view_axis_selection_to_dict(item) for item in columns]
        self.rows = [view_axis_selection_to_dict(item) for item in rows]
        self.titles = titles
        self.supress_empty_columns = suppress_empty_columns
        self.supress_empty_rows = suppress_empty_rows
        self.format_string = format_string
        self.source_path = source_path

    def as_json(self):
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "Columns": self.columns,
            "Rows": self.rows,
            "Titles": self.titles,
            "SuppressEmptyColumns": self.supress_empty_columns,
            "SuppressEmptyRows": self.supress_empty_rows,
            "FormatString": self.format_string,
        }, indent='\t')
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, NativeView):
            return NotImplemented
        return (
            self.name == other.name
            and self.columns == other.columns
            and self.rows == other.rows
            and self.titles == other.titles
            and self.supress_empty_columns == other.supress_empty_columns
            and self.supress_empty_rows == other.supress_empty_rows
            and self.format_string == other.format_string
        )

    def __hash__(self) -> int:
        return hash((
            self.name,
            json.dumps(self.columns, sort_keys=True),
            json.dumps(self.rows, sort_keys=True),
            json.dumps(self.titles, sort_keys=True),
            self.supress_empty_columns,
            self.supress_empty_rows,
            self.format_string,
        ))


def view_axis_selection_to_dict(axis_selection) -> Dict[str, Any]:
    if isinstance(axis_selection, dict):
        body = dict(axis_selection)
    else:
        body = dict(axis_selection.body_as_dict)
    subset = body.get("Subset")

    if isinstance(subset, dict):
        subset_dict = dict(subset)
        hierarchy_bind = subset_dict.pop("Hierarchy@odata.bind", None)

        if hierarchy_bind:
            subset_dict["Hierarchy"] = {"@id": hierarchy_bind}

        body["Subset"] = subset_dict

    return body

"""Searchsource:OSF"""
from __future__ import annotations

import typing
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import zope.interface
from dacite import from_dict
from dataclasses_jsonschema import JsonSchemaMixin

import colrev.process.operation
import colrev.loader.load_utils
import colrev.ops.load
import colrev.ops.prep
import colrev.package_manager.interfaces
import colrev.package_manager.package_manager
import colrev.package_manager.package_settings
import colrev.packages.ieee.src.ieee_api
import colrev.record.record
import colrev.record.record_prep
from colrev.constants import ENTRYTYPES
from colrev.constants import Fields
from colrev.constants import SearchSourceHeuristicStatus
from colrev.constants import SearchType

# pylint: disable=unused-argument
# pylint: disable=duplicate-code


@zope.interface.implementer(colrev.package_manager.interfaces.SearchSourceInterface)
@dataclass
class OSFSearchSource(JsonSchemaMixin):
    """OSF"""
    def __init__(self,* ,source_operation: colrev.process.operation.Operation, settings: dict
    ) -> None:
       
        self.search_source = from_dict(data_class=self.settings_class, data=settings)
        self.review_manager = source_operation.review_manager



    def load(self, load_operation: colrev.ops.load.Load) -> dict:
        """Load the records from the SearchSource file"""

        def json_field_mapper(record_dict: dict) -> None:
                """Maps the different entries of the JSON file to endpoints"""
                if "title" in record_dict:
                    record_dict[f"{self.endpoint}.title"] = record_dict.pop("title")
                if "description" in record_dict:
                    record_dict[f"{self.endpoint}.description"] = record_dict.pop("description")
                if "category" in record_dict:
                    record_dict[f"{self.endpoint}.category"] = record_dict.pop("category")
                if "type" in record_dict:
                    record_dict[f"{self.endpoint}.type"] = record_dict.pop(
                        "type"
                    )
                if "tags" in record_dict:
                    record_dict[f"{self.endpoint}.tags"] = record_dict.pop(
                        "tags"
                    )
                if "date_created" in record_dict:
                    record_dict[f"{self.endpoint}.date_created"] = record_dict.pop(
                        "date_created"
                    )
                if "year" in record_dict:
                    record_dict[f"{self.endpoint}.year"] = record_dict.pop(
                        "year"
                    )

                if "id" in record_dict:
                    record_dict[f"{self.endpoint}.id"] = record_dict.pop("id")
                

                record_dict.pop("date_modified", None)
                record_dict.pop("custom_citation", None)
                record_dict.pop("registration", None)
                record_dict.pop("preprint", None)
                record_dict.pop("fork", None)
                record_dict.pop("collection", None)
            
                for key, value in record_dict.items():
                    record_dict[key] = str(value)

        def json_entrytype_setter(record_dict: dict) -> None:
                """Loads the JSON file into the imported_md file"""
                record_dict[Fields.ENTRYTYPE] = ENTRYTYPES.MISC

                records = colrev.loader.load_utils.load(
                filename=self.search_source.filename,
                entrytype_setter=json_entrytype_setter,
                field_mapper=json_field_mapper,
                # Note: uid not always available.
                unique_id_field="INCREMENTAL",
                logger=self.review_manager.logger,
            )
                return records
    
    raise NotImplementedError
    
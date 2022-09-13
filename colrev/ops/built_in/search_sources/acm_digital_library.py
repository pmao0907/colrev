#! /usr/bin/env python
from pathlib import Path

import zope.interface
from dacite import from_dict

import colrev.ops.built_in.database_connectors
import colrev.ops.search
import colrev.process
import colrev.record

# pylint: disable=unused-argument
# pylint: disable=duplicate-code


@zope.interface.implementer(colrev.process.SearchSourceEndpoint)
class ACMDigitalLibrarySearchSource:
    settings_class = colrev.process.DefaultSourceSettings
    # Note : the ID contains the doi
    source_identifier = "https://dl.acm.org/doi/{{ID}}"
    source_identifier_search = "https://dl.acm.org/doi/{{ID}}"
    search_mode = "individual"

    def __init__(self, *, source_operation, settings: dict) -> None:
        self.settings = from_dict(data_class=self.settings_class, data=settings)

    @classmethod
    def heuristic(cls, filename: Path, data: str) -> dict:
        result = {"confidence": 0, "source_identifier": cls.source_identifier}

        # Simple heuristic:
        if "publisher = {Association for Computing Machinery}," in data:
            result["confidence"] = 0.7
            return result
        # We may also check whether the ID=doi=url
        return result

    def run_search(self, search_operation: colrev.ops.search.Search) -> None:
        search_operation.review_manager.logger.info(
            "Automated search not (yet) supported."
        )

    def load_fixes(self, load_operation, source, records):

        return records

    def prepare(self, record: colrev.record.Record) -> colrev.record.Record:
        # TODO (if any)
        return record


if __name__ == "__main__":
    pass

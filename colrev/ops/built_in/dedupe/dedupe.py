#! /usr/bin/env python
"""Default deduplication module for CoLRev"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import bib_dedupe.cluster
import pandas as pd
import zope.interface
from bib_dedupe.bib_dedupe import BibDeduper
from dataclasses_jsonschema import JsonSchemaMixin

import colrev.env.package_manager
import colrev.ops.built_in.dedupe.utils
import colrev.record
from colrev.constants import Fields

if TYPE_CHECKING:
    import colrev.ops.dedupe

# pylint: disable=too-few-public-methods


@zope.interface.implementer(colrev.env.package_manager.DedupePackageEndpointInterface)
@dataclass
class Dedupe(JsonSchemaMixin):
    """Default deduplication"""

    ci_supported: bool = True

    settings_class = colrev.env.package_manager.DefaultSettings

    def __init__(
        self,
        *,
        dedupe_operation: colrev.ops.dedupe.Dedupe,
        settings: dict,
    ):
        self.settings = self.settings_class.load_settings(data=settings)
        self.dedupe_operation = dedupe_operation
        self.review_manager = dedupe_operation.review_manager
        self.bib_deduper = BibDeduper()

    def run_dedupe(self) -> None:
        """Run default dedupe"""

        records = self.review_manager.dataset.load_records_dict()
        records_df = pd.DataFrame.from_dict(records, orient="index")

        records_df = self.dedupe_operation.get_records_for_dedupe(records_df=records_df)

        if 0 == records_df.shape[0]:
            return

        deduplication_pairs = self.bib_deduper.block(records_df)
        matched_df = self.bib_deduper.match(deduplication_pairs)

        if self.dedupe_operation.debug:
            return

        duplicate_id_sets = bib_dedupe.cluster.get_connected_components(matched_df)

        # based on records, get the origin_sets
        origin_sets = [
            [o for rid in dupe_id_set for o in records[rid][Fields.ORIGIN]]
            for dupe_id_set in duplicate_id_sets
        ]

        self.dedupe_operation.apply_merges(
            origin_sets=origin_sets, complete_dedupe=True
        )

        self.review_manager.create_commit(
            msg="Merge duplicate records",
        )

        # TODO : maybes
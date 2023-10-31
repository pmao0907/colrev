#! /usr/bin/env python
"""Deduplication of remaining records in curated metadata repositories"""
from __future__ import annotations

import typing
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import zope.interface
from dataclasses_jsonschema import JsonSchemaMixin

import colrev.env.package_manager
import colrev.exceptions as colrev_exceptions
import colrev.record
from colrev.constants import Colors
from colrev.constants import Fields

if TYPE_CHECKING:
    import colrev.ops.dedupe

# pylint: disable=too-many-arguments
# pylint: disable=too-few-public-methods
# pylint: disable=duplicate-code


@zope.interface.implementer(colrev.env.package_manager.DedupePackageEndpointInterface)
@dataclass
class CurationMissingDedupe(JsonSchemaMixin):
    """Deduplication of remaining records in a curated metadata repository"""

    settings_class = colrev.env.package_manager.DefaultSettings
    ci_supported: bool = False

    def __init__(
        self,
        *,
        dedupe_operation: colrev.ops.dedupe.Dedupe,
        settings: dict,
    ):
        self.settings = self.settings_class.load_settings(data=settings)
        self.review_manager = dedupe_operation.review_manager

        self.__post_md_prepared_states = colrev.record.RecordState.get_post_x_states(
            state=colrev.record.RecordState.md_processed
        )

    def __create_dedupe_source_stats(self) -> None:
        # Note : reload to generate correct statistics

        Path("dedupe").mkdir(exist_ok=True)

        source_origins = [
            str(source.filename).replace("data/search/", "")
            for source in self.review_manager.settings.sources
        ]

        records = self.review_manager.dataset.load_records_dict()
        for source_origin in source_origins:
            selected_records = [
                r
                for r in records.values()
                if any(source_origin in co for co in r[Fields.ORIGIN])
                and r[Fields.STATUS]
                in [
                    colrev.record.RecordState.md_prepared,
                    colrev.record.RecordState.md_needs_manual_preparation,
                    colrev.record.RecordState.md_imported,
                ]
            ]
            records_df = pd.DataFrame.from_records(list(selected_records))
            if records_df.shape[0] == 0:
                self.review_manager.logger.info(
                    f"{Colors.GREEN}Source {source_origin} fully merged{Colors.END}"
                )
            else:
                self.review_manager.logger.info(
                    f"{Colors.ORANGE}Source {source_origin} not fully merged{Colors.END}"
                )
                self.review_manager.logger.info(
                    f"Exporting details to dedupe/{source_origin}.xlsx"
                )

                records_df = records_df[
                    records_df.columns.intersection(
                        [
                            Fields.ID,
                            Fields.STATUS,
                            Fields.JOURNAL,
                            Fields.BOOKTITLE,
                            Fields.YEAR,
                            Fields.VOLUME,
                            Fields.NUMBER,
                            Fields.TITLE,
                            Fields.AUTHOR,
                        ]
                    )
                ]
                keys = list(
                    records_df.columns.intersection(
                        [Fields.YEAR, Fields.VOLUME, Fields.NUMBER]
                    )
                )
                if Fields.YEAR in keys:
                    records_df.year = pd.to_numeric(records_df.year, errors="coerce")
                if Fields.VOLUME in keys:
                    records_df.volume = pd.to_numeric(
                        records_df.volume, errors="coerce"
                    )
                if Fields.NUMBER in keys:
                    records_df.number = pd.to_numeric(
                        records_df.number, errors="coerce"
                    )
                records_df.sort_values(by=keys, inplace=True)
                records_df.to_excel(f"dedupe/{source_origin}.xlsx", index=False)

    def __get_same_toc_recs(
        self, *, record: colrev.record.Record, records: dict
    ) -> list:
        if self.review_manager.force_mode:
            if record.data[Fields.STATUS] in self.__post_md_prepared_states:
                return []
        else:
            # only dedupe md_prepared records
            if record.data[Fields.STATUS] not in [
                colrev.record.RecordState.md_prepared
            ]:
                return []
        if record.data.get(Fields.TITLE, "") == "":
            return []

        try:
            toc_key = record.get_toc_key()
        except colrev_exceptions.NotTOCIdentifiableException:
            return []

        same_toc_recs = []
        for record_candidate in records.values():
            try:
                candidate_toc_key = colrev.record.Record(
                    data=record_candidate
                ).get_toc_key()
            except colrev_exceptions.NotTOCIdentifiableException:
                continue
            if toc_key != candidate_toc_key:
                continue
            if record_candidate[Fields.ID] == record.data[Fields.ID]:
                continue
            if record_candidate[Fields.STATUS] in [
                colrev.record.RecordState.md_prepared,
                colrev.record.RecordState.md_needs_manual_preparation,
                colrev.record.RecordState.md_imported,
            ]:
                continue
            same_toc_recs.append(record_candidate)
        return same_toc_recs

    def __print_same_toc_recs(
        self, *, same_toc_recs: list, record: colrev.record.Record
    ) -> list:
        for same_toc_rec in same_toc_recs:
            same_toc_rec["similarity"] = colrev.record.PrepRecord.get_record_similarity(
                record_a=colrev.record.Record(data=same_toc_rec), record_b=record
            )

        same_toc_recs = sorted(
            same_toc_recs, key=lambda d: d["similarity"], reverse=True
        )
        if len(same_toc_recs) > 20:
            same_toc_recs = same_toc_recs[0:20]

        i = 0
        for i, same_toc_rec in enumerate(same_toc_recs):
            author_title_string = (
                f"{same_toc_rec.get('author', 'NO_AUTHOR')} : "
                + f"{same_toc_rec.get('title', 'NO_TITLE')}"
            )

            if same_toc_rec["similarity"] > 0.8:
                print(f"{i + 1} - {Colors.ORANGE}{author_title_string}{Colors.END}")

            else:
                print(f"{i + 1} - {author_title_string}")
        return same_toc_recs

    def __get_nr_recs_to_merge(self, *, records: dict) -> int:
        if self.review_manager.force_mode:
            self.review_manager.logger.info(
                "Scope: md_prepared, md_needs_manual_preparation, md_imported"
            )
            nr_recs_to_merge = len(
                [
                    x
                    for x in records.values()
                    if x[Fields.STATUS] not in self.__post_md_prepared_states
                ]
            )
        else:
            self.review_manager.logger.info("Scope: md_prepared")
            nr_recs_to_merge = len(
                [
                    x
                    for x in records.values()
                    if x[Fields.STATUS] in [colrev.record.RecordState.md_prepared]
                ]
            )
        return nr_recs_to_merge

    def __process_missing_duplicates(self) -> dict:
        records = self.review_manager.dataset.load_records_dict()

        nr_recs_to_merge = self.__get_nr_recs_to_merge(records=records)

        nr_recs_checked = 0
        results: typing.Dict[str, list] = {
            "decision_list": [],
            "add_records_to_md_processed_list": [],
            "records_to_prepare": [],
        }

        for record_dict in records.values():
            record = colrev.record.Record(data=record_dict)

            same_toc_recs = self.__get_same_toc_recs(record=record, records=records)

            if len(same_toc_recs) == 0:
                print("no same toc records")
                continue

            print("\n\n\n")
            print(Colors.ORANGE)
            record.print_citation_format()
            print(Colors.END)

            same_toc_recs = self.__print_same_toc_recs(
                same_toc_recs=same_toc_recs, record=record
            )
            i = len(same_toc_recs)
            valid_selection = False
            quit_pressed = False
            while not valid_selection:
                ret = input(
                    f"({nr_recs_checked}/{nr_recs_to_merge}) "
                    f"Merge with record [{1}...{i+1} / s / a / p / q]?   "
                )
                if ret == "s":
                    valid_selection = True
                elif ret == "q":
                    quit_pressed = True
                    valid_selection = True
                elif ret == "a":
                    results["add_records_to_md_processed_list"].append(
                        record.data[Fields.ID]
                    )
                    valid_selection = True
                elif ret == "p":
                    results["records_to_prepare"].append(record.data[Fields.ID])
                    valid_selection = True
                elif ret.isdigit():
                    if int(ret) - 1 <= i:
                        rec2 = same_toc_recs[int(ret) - 1]
                        if record.data[Fields.STATUS] < rec2[Fields.STATUS]:
                            results["decision_list"].append(
                                {
                                    "ID1": rec2[Fields.ID],
                                    "ID2": record.data[Fields.ID],
                                    "decision": "duplicate",
                                }
                            )
                        else:
                            results["decision_list"].append(
                                {
                                    "ID1": record.data[Fields.ID],
                                    "ID2": rec2[Fields.ID],
                                    "decision": "duplicate",
                                }
                            )

                        valid_selection = True
            nr_recs_checked += 1
            if quit_pressed:
                break
        return results

    def run_dedupe(self, dedupe_operation: colrev.ops.dedupe.Dedupe) -> None:
        """Run the dedupe procedure for remaining records in curations"""

        # pylint: disable=too-many-branches
        # pylint: disable=too-many-statements

        # export sets of non-merged records
        # (and merged records a different xlsx for easy sort/merge)

        # Note : this script is necessary because the active learning is insufficient:
        # the automated ML-deduplication still has a certain error rate
        # which makes it less useful for curations
        # the active learning labeling presents cases on both sides
        # (likely duplicates and non-duplicates to maximize training quality)
        # For the curation, we are only interested in the duplicate, not the classifier

        print("\n\n")
        print(
            "In the following, "
            "records can be added to the curated (md_processed*) records.\n"
            "Curated records are displayed for the same table-of-content item "
            "(i.e., same year/volume/number)"
        )
        print("\n\n")

        ret = self.__process_missing_duplicates()

        if len(ret["decision_list"]) > 0:
            print("Duplicates identified:")
            print(ret["decision_list"])
            preferred_masterdata_sources = [
                s
                for s in self.review_manager.settings.sources
                if s.endpoint != "colrev.files_dir"
            ]

            dedupe_operation.apply_merges(
                results=ret["decision_list"],
                preferred_masterdata_sources=preferred_masterdata_sources,
            )

        if len(ret["records_to_prepare"]) > 0:
            records = self.review_manager.dataset.load_records_dict()
            for record_id, record_dict in records.items():
                if record_id in ret["records_to_prepare"]:
                    record = colrev.record.Record(data=record_dict)
                    record.set_status(
                        target_state=colrev.record.RecordState.md_needs_manual_preparation
                    )

            self.review_manager.dataset.save_records_dict(records=records)

        if len(ret["decision_list"]) > 0 or len(ret["records_to_prepare"]) > 0:
            self.review_manager.create_commit(
                msg="Merge duplicate records",
            )

        if len(ret["add_records_to_md_processed_list"]) > 0:
            records = self.review_manager.dataset.load_records_dict()
            for record_id, record_dict in records.items():
                if record_id in ret["add_records_to_md_processed_list"]:
                    if record_dict[Fields.STATUS] in [
                        colrev.record.RecordState.md_prepared,
                        colrev.record.RecordState.md_needs_manual_preparation,
                        colrev.record.RecordState.md_imported,
                    ]:
                        record = colrev.record.Record(data=record_dict)
                        record.set_status(
                            target_state=colrev.record.RecordState.md_processed
                        )

            self.review_manager.dataset.save_records_dict(records=records)
            input("Edit records (if any), add to git, and press Enter")

            self.review_manager.create_commit(
                msg="Add non-duplicate records",
            )

        self.__create_dedupe_source_stats()

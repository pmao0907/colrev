#! /usr/bin/env python
"""CoLRev dedupe operation: identify and merge duplicate records."""
from __future__ import annotations

import re
import string
import typing
from collections import Counter
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import List
from typing import Optional
from typing import TYPE_CHECKING

import pandas as pd

import colrev.env.utils
import colrev.exceptions as colrev_exceptions
import colrev.operation
import colrev.record
import colrev.settings
from colrev.constants import Colors
from colrev.constants import ENTRYTYPES
from colrev.constants import Fields

if TYPE_CHECKING:
    import colrev.review_manager

# pylint: disable=too-many-lines


class Dedupe(colrev.operation.Operation):
    """Deduplicate records (entity resolution)"""

    NON_DUPLICATE_FILE_XLSX = Path("non_duplicates_to_validate.xlsx")
    NON_DUPLICATE_FILE_TXT = Path("non_duplicates_to_validate.txt")
    DUPLICATES_TO_VALIDATE = Path("duplicates_to_validate.xlsx")
    SAME_SOURCE_MERGE_FILE = Path("same_source_merges.txt")
    PREVENTED_SAME_SOURCE_MERGE_FILE = Path("prevented_same_source_merges.txt")

    debug = False

    def __init__(
        self,
        *,
        review_manager: colrev.review_manager.ReviewManager,
        notify_state_transition_operation: bool = True,
    ) -> None:
        super().__init__(
            review_manager=review_manager,
            operations_type=colrev.operation.OperationsType.dedupe,
            notify_state_transition_operation=notify_state_transition_operation,
        )

        self.non_dupe_file_xlsx = (
            self.review_manager.dedupe_dir / self.NON_DUPLICATE_FILE_XLSX
        )
        self.non_dupe_file_txt = (
            self.review_manager.dedupe_dir / self.NON_DUPLICATE_FILE_TXT
        )
        self.dupe_file = self.review_manager.dedupe_dir / self.DUPLICATES_TO_VALIDATE

        self.same_source_merge_file = (
            self.review_manager.dedupe_dir / self.SAME_SOURCE_MERGE_FILE
        )
        self.prevented_same_source_merge_file = (
            self.review_manager.dedupe_dir / self.PREVENTED_SAME_SOURCE_MERGE_FILE
        )
        self.review_manager.dedupe_dir.mkdir(exist_ok=True, parents=True)
        self.policy = self.review_manager.settings.dedupe.same_source_merges

    @classmethod
    def __dfs(cls, node: str, graph: dict, visited: dict, component: list) -> None:
        visited[node] = True
        component.append(node)
        for neighbor in graph[node]:
            if not visited[neighbor]:
                cls.__dfs(neighbor, graph, visited, component)

    @classmethod
    def connected_components(cls, origin_sets: list) -> list:
        """
        Find the connected components in a graph.

        Args:
            origin_sets (list): A list of origin sets.

        Returns:
            list: A list of connected components.
        """
        graph = defaultdict(list)

        # Create an adjacency list
        for origin_set in origin_sets:
            for combination in combinations(origin_set, 2):
                graph[combination[0]].append(combination[1])
                graph[combination[1]].append(combination[0])

        visited = {node: False for node in graph}
        components = []

        for node in graph:
            if not visited[node]:
                component: List[str] = []
                cls.__dfs(node, graph, visited, component)
                components.append(sorted(component))

        return components

    @classmethod
    def __pre_process(cls, *, key: str, value: str) -> str | None:
        if key in [Fields.ID, Fields.ENTRYTYPE, Fields.STATUS, Fields.ORIGIN]:
            return value

        value = str(value)
        # Note unidecode may be an alternative to rmdiacritics/remove_accents.
        # It would be important to operate on a per-character basis
        # instead of throwing an exception when processing whole strings
        # value = unidecode(value)
        value = re.sub("  +", " ", value)
        value = re.sub("\n", " ", value)
        value = value.strip().strip('"').strip("'").lower().strip()
        # If data is missing, indicate that by setting the value to `None`
        if not value:
            return None

        if any(
            value == x
            for x in ["no issue", "no volume", "no pages", "no author", "nan", ""]
        ):
            return None

        return value

    @classmethod
    def get_records_for_dedupe(cls, *, records_df: pd.DataFrame) -> pd.DataFrame:
        """Get (pre-processed) records for dedupe"""

        if 0 == records_df.shape[0]:
            return {}

        records_df.replace(to_replace={"UNKNOWN": ""}, inplace=True)
        required_fields = [
            Fields.ENTRYTYPE,
            Fields.JOURNAL,
            Fields.BOOKTITLE,
            Fields.SERIES,
            Fields.VOLUME,
            Fields.NUMBER,
            Fields.PAGES,
            Fields.AUTHOR,
            Fields.ABSTRACT,
            Fields.ISBN,
            Fields.DOI,
        ]
        for required_field in required_fields:
            if required_field not in records_df:
                records_df[required_field] = ""

        records_df = records_df[
            ~(
                records_df[Fields.STATUS].isin(
                    [
                        colrev.record.RecordState.md_imported,
                        colrev.record.RecordState.md_needs_manual_preparation,
                    ]
                )
            )
        ]

        records_df[Fields.YEAR] = records_df[Fields.YEAR].astype(str)
        if Fields.STATUS in records_df:
            # pylint: disable=colrev-direct-status-assign
            records_df[Fields.STATUS] = records_df[Fields.STATUS].astype(str)
        if Fields.ORIGIN in records_df:
            # pylint: disable=unnecessary-lambda
            records_df[Fields.ORIGIN] = records_df[Fields.ORIGIN].apply(
                lambda x: ";".join(x)
            )

        def select_authors(authors: str) -> str:
            """Select first author"""
            authors_list = str(authors).split(" and ")
            authors_str = " ".join(
                [author.split(",")[0] for author in authors_list][:8]
            )
            return colrev.env.utils.remove_accents(input_str=authors_str)

        # pylint: disable=unnecessary-lambda
        records_df[Fields.AUTHOR] = records_df[Fields.AUTHOR].apply(
            lambda x: select_authors(x)
        )

        # TODO : extract and standardize!
        def get_container_title(row: dict) -> str:
            if row[Fields.ENTRYTYPE] == ENTRYTYPES.ARTICLE:
                return row[Fields.JOURNAL]
            if row[Fields.ENTRYTYPE] in [
                ENTRYTYPES.INPROCEEDINGS,
                ENTRYTYPES.BOOK,
                ENTRYTYPES.PROCEEDINGS,
            ]:
                return row[Fields.BOOKTITLE]
            if row[Fields.ENTRYTYPE] == ENTRYTYPES.INBOOK:
                return row[Fields.TITLE]
            return ""

        records_df[Fields.CONTAINER_TITLE] = records_df.apply(
            get_container_title, axis=1
        )

        if Fields.CHAPTER in records_df:
            records_df.loc[
                records_df.ENTRYTYPE == ENTRYTYPES.INBOOK, Fields.TITLE
            ] = records_df.loc[
                records_df.ENTRYTYPE == ENTRYTYPES.INBOOK, Fields.CHAPTER
            ]

        records_df[Fields.TITLE] = (
            records_df[Fields.TITLE]
            .str.replace("&amp;", "and")
            .str.replace(r"[^A-Za-z0-9, ]+", " ", regex=True)
            .str.lower()
        )
        records_df.loc[records_df[Fields.TITLE].isnull(), Fields.TITLE] = ""

        records_df[Fields.JOURNAL] = (
            records_df[Fields.JOURNAL]
            .str.replace(r"[^A-Za-z0-9, ]+", "", regex=True)
            .str.lower()
        )

        # Note: don't include years/nrs because they are not used consistently
        records_df[Fields.BOOKTITLE] = (
            records_df[Fields.BOOKTITLE]
            .str.replace(r"[^A-Za-z, ]+", "", regex=True)
            .str.lower()
            .replace("proceedings of the", "", regex=True)
        )

        # records_df[Fields.SERIES] = (
        #     records_df[Fields.SERIES]
        #     .str.replace(r"[^A-Za-z0-9, ]+", "", regex=True)
        #     .str.lower()
        # )

        records_df.drop(
            labels=list(
                records_df.columns.difference(
                    [
                        Fields.ID,
                        Fields.ENTRYTYPE,
                        Fields.AUTHOR,
                        Fields.TITLE,
                        Fields.YEAR,
                        Fields.JOURNAL,
                        Fields.CONTAINER_TITLE,
                        Fields.VOLUME,
                        Fields.NUMBER,
                        Fields.PAGES,
                        Fields.COLREV_ID,
                        Fields.ORIGIN,
                        Fields.STATUS,
                        Fields.ABSTRACT,
                        Fields.URL,
                        Fields.ISBN,
                        Fields.DOI,
                    ]
                )
            ),
            axis=1,
            inplace=True,
        )
        records_df[
            [
                Fields.AUTHOR,
                Fields.TITLE,
                Fields.JOURNAL,
                Fields.CONTAINER_TITLE,
                Fields.PAGES,
            ]
        ] = records_df[
            [
                Fields.AUTHOR,
                Fields.TITLE,
                Fields.JOURNAL,
                Fields.CONTAINER_TITLE,
                Fields.PAGES,
            ]
        ].astype(
            str
        )
        records_list = records_df.to_dict("records")

        records = {}
        for row in records_list:
            # Note: we need the ID to identify/remove duplicates in the RECORDS_FILE.
            # It is ignored in the field-definitions by the deduper
            clean_row = [
                (k, cls.__pre_process(key=str(k), value=v)) for (k, v) in row.items()
            ]
            records[row[Fields.ID]] = dict(clean_row)

        return pd.DataFrame.from_dict(records, orient="index")

    def __select_primary_merge_record(self, rec_1: dict, rec_2: dict) -> list:
        # pylint: disable=too-many-branches

        # Heuristic

        # 1. if both records are prepared (or the same status),
        # merge into the record with the "lower" colrev_id
        if rec_1[Fields.STATUS] == rec_2[Fields.STATUS]:
            if rec_1[Fields.ID][-1].isdigit() and not rec_2[Fields.ID][-1].isdigit():
                main_record = rec_1
                dupe_record = rec_2
            elif (
                not rec_1[Fields.ID][-1].isdigit()
                and not rec_2[Fields.ID][-1].isdigit()
            ):
                # Last characters are letters in both records
                # Select the one that's first in the alphabet
                try:
                    pos_rec_1_suffix = string.ascii_lowercase.index(
                        rec_1[Fields.ID][-1]
                    )
                except ValueError:
                    pos_rec_1_suffix = -1
                try:
                    pos_rec_2_suffix = string.ascii_lowercase.index(
                        rec_2[Fields.ID][-1]
                    )
                except ValueError:
                    pos_rec_2_suffix = -1

                if pos_rec_1_suffix < pos_rec_2_suffix:
                    main_record = rec_1
                    dupe_record = rec_2
                else:
                    dupe_record = rec_2
                    main_record = rec_1
            else:
                main_record = rec_2
                dupe_record = rec_1

        # 2. If a record is md_prepared, use it as the dupe record
        elif rec_1[Fields.STATUS] == colrev.record.RecordState.md_prepared:
            main_record = rec_2
            dupe_record = rec_1
        elif rec_2[Fields.STATUS] == colrev.record.RecordState.md_prepared:
            main_record = rec_1
            dupe_record = rec_2

        # 3. If a record is md_processed, use the other record as the dupe record
        # -> during the fix_errors procedure, records are in md_processed
        # and beyond.
        elif rec_1[Fields.STATUS] == colrev.record.RecordState.md_processed:
            main_record = rec_1
            dupe_record = rec_2
        elif rec_2[Fields.STATUS] == colrev.record.RecordState.md_processed:
            main_record = rec_2
            dupe_record = rec_1

        # 4. Merge into curated record (otherwise)
        else:
            if colrev.record.Record(data=rec_2).masterdata_is_curated():
                main_record = rec_2
                dupe_record = rec_1
            else:
                main_record = rec_1
                dupe_record = rec_2

        return [main_record, dupe_record]

    def __same_source_merge(
        self, *, main_record: colrev.record.Record, dupe_record: colrev.record.Record
    ) -> bool:
        main_rec_sources = [x.split("/")[0] for x in main_record.data[Fields.ORIGIN]]
        dupe_rec_sources = [x.split("/")[0] for x in dupe_record.data[Fields.ORIGIN]]
        same_sources = set(main_rec_sources).intersection(set(dupe_rec_sources))

        if len(same_sources) == 0:
            return False

        # don't raise unproblematic same-source merges
        # if same_sources start with md_... AND have same IDs: no same-source merge.
        if all(x.startswith("md_") for x in same_sources):
            main_rec_same_source_origins = [
                x
                for x in main_record.data[Fields.ORIGIN]
                if x.split("/")[0] in same_sources
            ]
            dupe_rec_same_source_origins = [
                x
                for x in dupe_record.data[Fields.ORIGIN]
                if x.split("/")[0] in same_sources
            ]
            if set(main_rec_same_source_origins) == set(dupe_rec_same_source_origins):
                return False
            return True
        return True

    def __notify_on_merge(
        self, *, main_record: colrev.record.Record, dupe_record: colrev.record.Record
    ) -> None:
        merge_info = main_record.data[Fields.ID] + " - " + dupe_record.data[Fields.ID]
        if not self.__same_source_merge(
            main_record=main_record, dupe_record=dupe_record
        ):
            self.review_manager.logger.info(
                f" merge {main_record.data[Fields.ID]} - {dupe_record.data[Fields.ID]}"
            )
            return

        if self.policy == colrev.settings.SameSourceMergePolicy.apply:
            return

        if self.policy == colrev.settings.SameSourceMergePolicy.warn:
            self.review_manager.logger.warning(
                f"{Colors.ORANGE}"
                "Apply same source merge "
                f"{Colors.END} "
                f"{merge_info}\n"
                f"  {main_record.format_bib_style()}\n"
                f"  {dupe_record.format_bib_style()}\n"
                f"  {main_record.data.get(Fields.ORIGIN, ['ERROR'])} x "
                f"{dupe_record.data.get(Fields.ORIGIN, ['ERROR'])}\n"
            )
            with self.same_source_merge_file.open("a", encoding="utf8") as file:
                file.write(merge_info + "\n")
            return

        if self.policy == colrev.settings.SameSourceMergePolicy.prevent:
            self.review_manager.logger.warning(
                f"Prevented same-source merge: ({merge_info})"
            )

            with self.prevented_same_source_merge_file.open(
                "a", encoding="utf8"
            ) as file:
                file.write(merge_info + "\n")

            self.review_manager.logger.info(
                "To force merge use colrev dedupe --merge "
                f"{main_record.data[Fields.ID]},{dupe_record.data[Fields.ID]}"
            )
            raise colrev_exceptions.InvalidMerge(
                record_a=main_record, record_b=dupe_record
            )

    def __is_cross_level_merge(
        self, *, main_record: colrev.record.Record, dupe_record: colrev.record.Record
    ) -> bool:
        is_cross_level_merge_attempt = False
        if main_record.data[Fields.ENTRYTYPE] in [
            ENTRYTYPES.PROCEEDINGS
        ] or dupe_record.data[Fields.ENTRYTYPE] in [ENTRYTYPES.PROCEEDINGS]:
            is_cross_level_merge_attempt = True

        if (
            main_record.data[Fields.ENTRYTYPE] == ENTRYTYPES.BOOK
            and dupe_record.data[Fields.ENTRYTYPE] == ENTRYTYPES.INBOOK
        ):
            is_cross_level_merge_attempt = True

        if (
            main_record.data[Fields.ENTRYTYPE] == ENTRYTYPES.INBOOK
            and dupe_record.data[Fields.ENTRYTYPE] == ENTRYTYPES.BOOK
        ):
            is_cross_level_merge_attempt = True

        return is_cross_level_merge_attempt

    def __gids_conflict(
        self, *, main_record: colrev.record.Record, dupe_record: colrev.record.Record
    ) -> bool:
        gid_conflict = False
        if Fields.DOI in main_record.data and Fields.DOI in dupe_record.data:
            doi_main = main_record.data.get(Fields.DOI, "a").replace("\\", "")
            doi_dupe = dupe_record.data.get(Fields.DOI, "b").replace("\\", "")
            if doi_main != doi_dupe:
                gid_conflict = True

        return gid_conflict

    # pylint: disable=too-many-arguments
    def __print_merge_stats(
        self,
        *,
        records: dict,
        duplicate_id_mappings: dict,
        removed_duplicates: list,
        complete_dedupe: bool,
        set_to_md_processed: list,
    ) -> None:
        print()
        self.review_manager.logger.info("Summary:")
        for record_id, duplicate_ids in duplicate_id_mappings.items():
            duplicate_ids = [x.replace(record_id, "-") for x in duplicate_ids]
            if record_id not in records:
                continue
            if (
                colrev.record.RecordState.md_prepared
                == records[record_id][Fields.STATUS]
            ):
                if complete_dedupe:
                    self.review_manager.logger.info(
                        f" {Colors.GREEN}{record_id} ({'|'.join(duplicate_ids)}) ".ljust(
                            46
                        )
                        + f"md_prepared →  md_processed{Colors.END}"
                    )
                else:
                    self.review_manager.logger.info(
                        f" {Colors.GREEN}{record_id} ({'|'.join(duplicate_ids)}) ".ljust(
                            46
                        )
                        + f"md_prepared →  md_prepared{Colors.END}"
                    )
        if complete_dedupe:
            for rid in set_to_md_processed:
                self.review_manager.logger.info(
                    f" {Colors.GREEN}{rid}".ljust(46)
                    + f"md_prepared →  md_processed{Colors.END}"
                )

        self.review_manager.logger.info(
            "Merged duplicates: ".ljust(39) + f"{len(removed_duplicates)} records"
        )

    def __apply_records_merges(
        self, *, records: dict, removed_duplicates: list, complete_dedupe: bool
    ) -> list:
        for record in records.values():
            if "MOVED_DUPE_ID" in record:
                del record["MOVED_DUPE_ID"]
        for removed_duplicate in removed_duplicates:
            if removed_duplicate in records:
                del records[removed_duplicate]

        set_to_md_processed = []
        if complete_dedupe:
            # Set remaining records to md_processed (not duplicate) because all records
            # have been considered by dedupe
            for record_dict in records.values():
                if record_dict[Fields.STATUS] == colrev.record.RecordState.md_prepared:
                    record = colrev.record.Record(data=record_dict)
                    record.set_status(
                        target_state=colrev.record.RecordState.md_processed
                    )
                    set_to_md_processed.append(record.data[Fields.ID])

        self.review_manager.dataset.save_records_dict(records=records)
        return set_to_md_processed

    def __skip_merge_condition(
        self, *, main_record: colrev.record.Record, dupe_record: colrev.record.Record
    ) -> bool:
        if self.review_manager.force_mode:
            return False
        if self.__is_cross_level_merge(
            main_record=main_record, dupe_record=dupe_record
        ):
            self.review_manager.logger.info(
                "Prevented cross-level merge: "
                f"{main_record.data[Fields.ID]} - {dupe_record.data[Fields.ID]}"
            )
            return True

        if self.__gids_conflict(main_record=main_record, dupe_record=dupe_record):
            self.review_manager.logger.info(
                "Prevented merge with conflicting global IDs: "
                f"{main_record.data[Fields.ID]} - {dupe_record.data[Fields.ID]}"
            )
            return True

        if (
            self.__same_source_merge(main_record=main_record, dupe_record=dupe_record)
            and self.policy == colrev.settings.SameSourceMergePolicy.prevent
        ):
            self.review_manager.logger.info(
                "Prevented same-source merge: "
                f"{main_record.data[Fields.ID]} - {dupe_record.data[Fields.ID]}"
            )
            return True

        return False

    def __update_duplicate_id_mappings(
        self,
        *,
        duplicate_id_mappings: dict,
        main_record: colrev.record.Record,
        dupe_record: colrev.record.Record,
    ) -> None:
        if main_record.data[Fields.ID] not in duplicate_id_mappings:
            duplicate_id_mappings[main_record.data[Fields.ID]] = [
                dupe_record.data[Fields.ID]
            ]
        else:
            duplicate_id_mappings[main_record.data[Fields.ID]].append(
                dupe_record.data[Fields.ID]
            )

    def apply_merges(
        self,
        *,
        origin_sets: list,
        complete_dedupe: bool = False,
        preferred_masterdata_sources: Optional[list] = None,
    ) -> None:
        """Apply deduplication decisions

        origin_sets : [[origin_1, origin_2, origin_3], ...]

        - complete_dedupe: when not all potential duplicates were considered,
        we cannot set records to md_procssed for non-duplicate decisions
        """

        preferred_masterdata_source_prefixes = []
        if preferred_masterdata_sources:
            preferred_masterdata_source_prefixes = [
                s.get_origin_prefix() for s in preferred_masterdata_sources
            ]

        records = self.review_manager.dataset.load_records_dict()

        removed_duplicates = []
        duplicate_id_mappings: typing.Dict[str, list] = {}
        for main_record, dupe_record in self.__get_records_to_merge(
            records=records, origin_sets=origin_sets
        ):
            try:
                if self.__skip_merge_condition(
                    main_record=main_record, dupe_record=dupe_record
                ):
                    continue

                self.__notify_on_merge(
                    main_record=main_record,
                    dupe_record=dupe_record,
                )

                self.__update_duplicate_id_mappings(
                    duplicate_id_mappings=duplicate_id_mappings,
                    main_record=main_record,
                    dupe_record=dupe_record,
                )
                dupe_record.data["MOVED_DUPE_ID"] = main_record.data[Fields.ID]
                main_record.merge(
                    merging_record=dupe_record,
                    default_source="merged",
                    preferred_masterdata_source_prefixes=preferred_masterdata_source_prefixes,
                )
                removed_duplicates.append(dupe_record.data[Fields.ID])

            except colrev_exceptions.InvalidMerge:
                continue
        set_to_md_processed = self.__apply_records_merges(
            records=records,
            removed_duplicates=removed_duplicates,
            complete_dedupe=complete_dedupe,
        )

        self.__print_merge_stats(
            records=records,
            duplicate_id_mappings=duplicate_id_mappings,
            removed_duplicates=removed_duplicates,
            complete_dedupe=complete_dedupe,
            set_to_md_processed=set_to_md_processed,
        )

    def __get_records_to_merge(
        self, *, records: dict, origin_sets: list
    ) -> typing.Iterable[tuple]:
        """Resolves multiple/chained duplicates (by following the MOVED_DUPE_ID mark)
        and returns tuples with the primary merge record in the first position."""

        for origin_set in origin_sets:
            recs_to_merge = [
                r
                for r in records.values()
                if any(o in r[Fields.ORIGIN] for o in origin_set)
            ]

            # Simple way of implementing the closure
            # cases where the main_record has already been merged into another record
            for ind, rec_to_merge in enumerate(recs_to_merge):
                cur_rec = rec_to_merge
                while "MOVED_DUPE_ID" in cur_rec:
                    cur_rec = records[cur_rec["MOVED_DUPE_ID"]]
                recs_to_merge[ind] = cur_rec

            if all(recs_to_merge[0][Fields.ID] == r[Fields.ID] for r in recs_to_merge):
                continue

            for rec_1, rec_2 in combinations(recs_to_merge, 2):
                main_record_dict, dupe_record_dict = self.__select_primary_merge_record(
                    rec_1, rec_2
                )

                main_record = colrev.record.Record(data=main_record_dict)
                dupe_record = colrev.record.Record(data=dupe_record_dict)

                yield (main_record, dupe_record)

    def __unmerge_current_record_ids_records(self, *, current_record_ids: list) -> dict:
        ids_origins: typing.Dict[str, list] = {rid: [] for rid in current_record_ids}

        for records in self.review_manager.dataset.load_records_from_history():
            for rid in ids_origins:
                ids_origins[rid] = records[rid][Fields.ORIGIN]
            break

        records = self.review_manager.dataset.load_records_dict()
        for rid in ids_origins:
            del records[rid]

        unmerged, first = False, True
        for recs in self.review_manager.dataset.load_records_from_history():
            if first:
                first = False
                continue
            for rid in list(ids_origins.keys()):
                if rid not in recs:
                    break

                if ids_origins[rid] == recs[rid]:
                    break

                for record in recs.values():
                    if any(orig in ids_origins[rid] for orig in record[Fields.ORIGIN]):
                        records[record[Fields.ID]] = record
                        unmerged = True
            if unmerged:
                break
        return records

    def __unmerge_previous_id_lists_records(self, *, previous_id_lists: list) -> dict:
        records = self.review_manager.dataset.load_records_dict()
        git_repo = self.review_manager.dataset.get_repo()
        # print(self.review_manager.dataset.RECORDS_FILE_RELATIVE.is_file())
        # r_path = join_path_native("data", "records.bib")
        revlist = (
            (commit.tree / "data" / "records.bib").data_stream.read()
            for commit in git_repo.iter_commits(
                paths=str(self.review_manager.dataset.RECORDS_FILE_RELATIVE)
            )
        )

        unmerged = False
        for filecontents in revlist:
            prior_records_dict = self.review_manager.dataset.load_records_dict(
                load_str=filecontents.decode("utf-8")
            )

            for id_list_to_unmerge in previous_id_lists:
                self.review_manager.report_logger.info(
                    f'Undo merge: {",".join(id_list_to_unmerge)}'
                )
                self.review_manager.logger.info(
                    f'Undo merge: {",".join(id_list_to_unmerge)}'
                )

                if all(rec_id in prior_records_dict for rec_id in id_list_to_unmerge):
                    # delete new record,
                    # add previous records (from history) to records
                    records = {
                        k: v for k, v in records.items() if k not in id_list_to_unmerge
                    }

                    for record_dict in prior_records_dict.values():
                        if record_dict[Fields.ID] in id_list_to_unmerge:
                            # add manual_dedupe/non_dupe decision to the records
                            manual_non_duplicates = id_list_to_unmerge.copy()
                            manual_non_duplicates.remove(record_dict[Fields.ID])

                            # The followin may need to be set to the previous state of the
                            # record that was erroneously merged (could be md_prepared)
                            record = colrev.record.Record(data=record_dict)
                            record.set_status(
                                target_state=colrev.record.RecordState.md_processed
                            )
                            records[record_dict[Fields.ID]] = record_dict
                            self.review_manager.logger.info(
                                f"Restored {record_dict[Fields.ID]}"
                            )
                    unmerged = True

                if unmerged:
                    break
            if unmerged:
                break

        if not unmerged:
            self.review_manager.logger.error(
                f"Could not restore {previous_id_lists} - " "please fix manually"
            )

        return records

    def unmerge_records(
        self,
        *,
        current_record_ids: Optional[list] = None,
        previous_id_lists: Optional[list] = None,
    ) -> None:
        """Unmerge duplicate decision of the records, as identified by their ids.

        The current_record_ids identifies the records by their current IDs and
        unmerges their most recent merge in history.

        The previous_id_lists identifies the records by their IDs in the previous commit and
        only considers merges in the previous commit.

        """

        assert not (previous_id_lists and current_record_ids)

        if current_record_ids:
            records = self.__unmerge_current_record_ids_records(
                current_record_ids=current_record_ids
            )

        if previous_id_lists:
            records = self.__unmerge_previous_id_lists_records(
                previous_id_lists=previous_id_lists
            )

        self.review_manager.dataset.save_records_dict(records=records)

    def fix_errors(self, *, false_positives: list, false_negatives: list) -> None:
        """Fix lists of errors"""

        records = self.review_manager.dataset.load_records_dict()
        origin_sets = [
            o
            for r in records
            for o in r[Fields.ORIGIN]
            if r[Fields.ID] in false_negatives
        ]

        self.unmerge_records(previous_id_lists=false_positives)
        self.apply_merges(origin_sets=origin_sets, complete_dedupe=False)

        if self.review_manager.dataset.records_changed():
            self.review_manager.create_commit(
                msg="Validate and correct duplicates",
                manual_author=True,
            )

    def get_info(self) -> dict:
        """Get info on cuts (overlap of search sources) and same source merges"""

        records = self.review_manager.dataset.load_records_dict()

        origins = [record[Fields.ORIGIN] for record in records.values()]
        origins = [item.split("/")[0] for sublist in origins for item in sublist]
        origins = list(set(origins))

        same_source_merges = []

        for record in records.values():
            rec_sources = [x.split("/")[0] for x in record[Fields.ORIGIN]]

            duplicated_sources = [
                item for item, count in Counter(rec_sources).items() if count > 1
            ]
            if len(duplicated_sources) > 0:
                all_cases = []
                for duplicated_source in duplicated_sources:
                    cases = [
                        o.split("/")[1]
                        for o in record[Fields.ORIGIN]
                        if duplicated_source in o
                    ]
                    all_cases.append(f"{duplicated_source}: {cases}")
                same_source_merges.append(
                    f"{record[Fields.ID]} ({', '.join(all_cases)})"
                )

        info = {
            "same_source_merges": same_source_merges,
        }
        return info

    def merge_records(self, *, merge: str) -> None:
        """Merge two records by origin sets origin_1;origin_2;origin_3..."""

        self.apply_merges(origin_sets=merge.split(";"))

    def merge_based_on_global_ids(self, *, apply: bool = False) -> None:
        """Merge records based on global IDs (e.g., doi)"""

        # pylint: disable=too-many-branches

        self.review_manager.logger.info(
            "Dedupe operation [merge based on identical global_ids]"
        )

        records = self.review_manager.dataset.load_records_dict()

        global_keys = [
            Fields.DOI,
            Fields.COLREV_ID,
            Fields.PUBMED_ID,
            Fields.DBLP_KEY,
            Fields.URL,
        ]
        if Fields.COLREV_ID in global_keys:
            for record in records.values():
                try:
                    record[Fields.COLREV_ID] = colrev.record.Record(
                        data=record
                    ).create_colrev_id()
                except colrev_exceptions.NotEnoughDataToIdentifyException:
                    pass

        origin_sets = []
        for global_key in global_keys:
            key_dict: typing.Dict[str, list] = {}
            for record in records.values():
                if global_key not in record:
                    continue
                if record[global_key] in key_dict:
                    key_dict[record[global_key]].append(record[Fields.ID])
                else:
                    key_dict[record[global_key]] = [record[Fields.ID]]

            key_dict = {k: v for k, v in key_dict.items() if len(v) > 1}
            for record_ids in key_dict.values():
                for ref_rec_id in range(1, len(record_ids)):
                    origin_sets.append(
                        record_ids[0][Fields.ORIGIN]
                        + record_ids[ref_rec_id][Fields.ORIGIN]
                    )

        if apply:
            self.review_manager.settings.dedupe.same_source_merges = (
                colrev.settings.SameSourceMergePolicy.warn
            )
            self.apply_merges(origin_sets=origin_sets)
            self.review_manager.create_commit(
                msg="Merge records (identical global IDs)"
            )

        elif origin_sets:
            print()
            self.review_manager.logger.info("Found records with identical global-ids:")
            self.review_manager.logger.info(
                f"{Colors.ORANGE}To merge records with identical global-ids, "
                f"run colrev merge -gid{Colors.END}"
            )
            print()

        else:
            print()

    @colrev.operation.Operation.decorate()
    def main(self, *, debug: bool = False) -> None:
        """Dedupe records (main entrypoint)"""

        self.review_manager.logger.info("Dedupe")
        self.review_manager.logger.info(
            "Identifies duplicate records and merges them (keeping traces to their origins)."
        )
        self.review_manager.logger.info(
            "See https://colrev.readthedocs.io/en/latest/manual/metadata_retrieval/dedupe.html"
        )
        self.debug = debug

        if not self.review_manager.high_level_operation:
            print()

        package_manager = self.review_manager.get_package_manager()
        for (
            dedupe_package_endpoint
        ) in self.review_manager.settings.dedupe.dedupe_package_endpoints:
            # Note : load package/script at this point because the same script
            # may run with different parameters
            endpoint_dict = package_manager.load_packages(
                package_type=colrev.env.package_manager.PackageEndpointType.dedupe,
                selected_packages=[dedupe_package_endpoint],
                operation=self,
                only_ci_supported=self.review_manager.in_ci_environment(),
            )
            if dedupe_package_endpoint["endpoint"] not in endpoint_dict:
                self.review_manager.logger.info(
                    f'Skip {dedupe_package_endpoint["endpoint"]} (not available)'
                )
                if self.review_manager.in_ci_environment():
                    raise colrev_exceptions.ServiceNotAvailableException(
                        dep="colrev dedupe",
                        detailed_trace="dedupe not available in ci environment",
                    )
                raise colrev_exceptions.ServiceNotAvailableException(
                    dep="colrev dedupe", detailed_trace="dedupe not available"
                )

            endpoint = endpoint_dict[dedupe_package_endpoint["endpoint"]]

            endpoint.run_dedupe()  # type: ignore
            if not self.review_manager.high_level_operation:
                print()

        dedupe_commit_id = self.review_manager.dataset.get_repo().head.commit.hexsha
        self.review_manager.logger.info("To validate the changes, use")

        self.review_manager.logger.info(
            f"{Colors.ORANGE}colrev validate {dedupe_commit_id}{Colors.END}"
        )
        print()

        self.review_manager.logger.info(
            f"{Colors.GREEN}Completed dedupe operation{Colors.END}"
        )

        if not self.review_manager.settings.prescreen.prescreen_package_endpoints:
            self.review_manager.logger.info("Skipping prescreen/including all records")
            records = self.review_manager.dataset.load_records_dict()
            for record_dict in records.values():
                record = colrev.record.Record(data=record_dict)
                if colrev.record.RecordState.md_processed == record.data[Fields.STATUS]:
                    record.set_status(
                        target_state=colrev.record.RecordState.rev_prescreen_included
                    )

            self.review_manager.dataset.save_records_dict(records=records)
            self.review_manager.create_commit(msg="Skip prescreen/include all")

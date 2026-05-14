"""Tests for the SAM.gov adapter's pre-solicitation scanning logic."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

from scout.adapters.sam_gov import SamGovAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hit(notice_id: str, title: str, ptype: str = "o") -> dict:
    return {
        "noticeId": notice_id,
        "title": title,
        "type": ptype,
        "naicsCode": "",
        "classificationCode": "",
        "postedDate": "2026-05-14",
        "responseDeadLine": None,
        "uiLink": f"https://sam.gov/opp/{notice_id}",
        "department": "DARPA",
        "updatedDate": "2026-05-14",
        # No description URL to fetch so _enrich_description is a no-op
        "description": "Battery and fuel cell research for defense applications.",
    }


def _sam_response(hits: list[dict]) -> dict:
    return {"opportunitiesData": hits, "totalRecords": len(hits)}


def _empty_sam_response() -> dict:
    return {"opportunitiesData": [], "totalRecords": 0}


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestPrereqAgencyConfig:
    def test_darpa_in_prereq_agencies(self):
        assert "DARPA" in SamGovAdapter.PREREQ_AGENCIES

    def test_special_notice_ptype_included(self):
        assert "s" in SamGovAdapter.PREREQ_PTYPES

    def test_presolicitation_ptype_included(self):
        assert "p" in SamGovAdapter.PREREQ_PTYPES


class TestFetchIssuesPrereqQueries:
    """Verify that fetch() fires the pre-solicitation queries in addition to
    the NAICS-based queries."""

    def _run_fetch(self, mock_paginate):
        with patch.dict("os.environ", {"SAM_GOV_API_KEY": "test-key"}):
            adapter = SamGovAdapter()
            with patch.object(adapter, "_paginate", mock_paginate):
                list(adapter.fetch())

    def test_special_notice_query_issued_for_darpa(self):
        mock_paginate = MagicMock(return_value=(False, []))
        self._run_fetch(mock_paginate)
        calls = mock_paginate.call_args_list
        # Find any call with ptype="s" and keyword="DARPA"
        prereq_calls = [
            c for c in calls
            if c.kwargs.get("ptype") == "s" and c.kwargs.get("keyword") == "DARPA"
        ]
        assert prereq_calls, "No Special Notice query was issued for DARPA"

    def test_presolicitation_query_issued_for_darpa(self):
        mock_paginate = MagicMock(return_value=(False, []))
        self._run_fetch(mock_paginate)
        calls = mock_paginate.call_args_list
        prereq_calls = [
            c for c in calls
            if c.kwargs.get("ptype") == "p" and c.kwargs.get("keyword") == "DARPA"
        ]
        assert prereq_calls, "No Presolicitation query was issued for DARPA"

    def test_naics_queries_still_issued(self):
        mock_paginate = MagicMock(return_value=(False, []))
        self._run_fetch(mock_paginate)
        calls = mock_paginate.call_args_list
        naics_calls = [c for c in calls if c.kwargs.get("ncode")]
        assert len(naics_calls) == len(SamGovAdapter.CORE_NAICS)

    def test_all_prereq_agencies_queried(self):
        mock_paginate = MagicMock(return_value=(False, []))
        self._run_fetch(mock_paginate)
        calls = mock_paginate.call_args_list
        queried_agencies = {c.kwargs.get("keyword") for c in calls if c.kwargs.get("ptype")}
        for agency in SamGovAdapter.PREREQ_AGENCIES:
            assert agency in queried_agencies, f"{agency} never queried for pre-solicitations"

    def test_quota_exhaustion_stops_prereq_pass(self):
        """If a NAICS query hits 429, no pre-solicitation queries should fire."""
        mock_paginate = MagicMock(return_value=(True, []))  # always signals hard-stop
        self._run_fetch(mock_paginate)
        calls = mock_paginate.call_args_list
        prereq_calls = [c for c in calls if c.kwargs.get("ptype")]
        assert not prereq_calls, "Pre-solicitation queries should not fire after quota exhaustion"


class TestPaginateParams:
    """Verify _paginate builds the right query params for each query mode."""

    def _make_response(self, hits):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _sam_response(hits) if hits else _empty_sam_response()
        return mock_resp

    def test_naics_query_sends_ncode(self):
        adapter = SamGovAdapter()
        adapter.api_key = "test-key"
        client = MagicMock()
        client.get.return_value = self._make_response([])
        start = datetime(2026, 4, 14)
        end = datetime(2026, 5, 14)

        adapter._paginate(client, start, end, ncode="541715")

        params = client.get.call_args[1]["params"]
        assert params["ncode"] == "541715"
        assert "ptype" not in params
        assert "keyword" not in params

    def test_special_notice_query_sends_ptype_and_keyword(self):
        adapter = SamGovAdapter()
        adapter.api_key = "test-key"
        client = MagicMock()
        client.get.return_value = self._make_response([])
        start = datetime(2026, 4, 14)
        end = datetime(2026, 5, 14)

        adapter._paginate(client, start, end, ptype="s", keyword="DARPA")

        params = client.get.call_args[1]["params"]
        assert params["ptype"] == "s"
        assert params["keyword"] == "DARPA"
        assert "ncode" not in params

    def test_presolicitation_query_sends_correct_ptype(self):
        adapter = SamGovAdapter()
        adapter.api_key = "test-key"
        client = MagicMock()
        client.get.return_value = self._make_response([])
        start = datetime(2026, 4, 14)
        end = datetime(2026, 5, 14)

        adapter._paginate(client, start, end, ptype="p", keyword="DARPA")

        params = client.get.call_args[1]["params"]
        assert params["ptype"] == "p"
        assert params["keyword"] == "DARPA"


class TestNormalizeSpecialNotice:
    """Verify that a typical Proposer's Day payload normalizes correctly."""

    def test_expeditions_style_notice_normalizes(self):
        adapter = SamGovAdapter()
        adapter.api_key = "test-key"
        payload = _make_hit("DARPA-SN-26-70", "ExPEDitions Proposers Day", ptype="s")
        notice = adapter.normalize("DARPA-SN-26-70", payload, "abc123")
        assert notice is not None
        assert notice.title == "ExPEDitions Proposers Day"
        assert notice.agency == "DARPA"
        assert notice.notice_id == "DARPA-SN-26-70"

    def test_notice_without_naics_still_normalizes(self):
        adapter = SamGovAdapter()
        adapter.api_key = "test-key"
        payload = _make_hit("DARPA-SN-26-70", "ExPEDitions Proposers Day")
        payload["naicsCode"] = ""
        notice = adapter.normalize("DARPA-SN-26-70", payload, "abc123")
        assert notice is not None
        assert notice.naics == ""

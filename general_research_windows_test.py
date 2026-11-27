import hashlib
import unittest
from datetime import date, timedelta

import general_research_windows as rw


def archive(count):
    start=date(2020,8,2)
    return [{"date":(start+timedelta(days=2*i)).isoformat(),"numbers":[1,2,3,4,5,6]} for i in range(count)]


class UnseenPackageTests(unittest.TestCase):
    def setUp(self):
        self.rows=archive(133)
        self.sha=hashlib.sha256(b"archive").hexdigest()

    def test_exact_minimum_package(self):
        rows=self.rows[:45]
        p=rw.build_research_packages(rows,archive_sha256=self.sha,unavailable=set())[0]
        self.assertEqual(p["discovery_count"],40)
        self.assertEqual(p["validation_count"],2)
        self.assertEqual(p["reverse_validation_count"],2)
        self.assertNotIn(rows[-1]["date"],p["dates"])

    def test_multiple_packages_disjoint_and_ordered(self):
        ps=rw.build_research_packages(self.rows,archive_sha256=self.sha,unavailable=set())
        self.assertGreaterEqual(len(ps),2)
        for a,b in zip(ps,ps[1:]):
            self.assertLess(a["package_end_date"],b["package_start_date"])
            self.assertFalse(set(a["dates"]) & set(b["dates"]))

    def test_consumed_gap_never_compacted(self):
        gap=self.rows[40]["date"]
        ps=rw.build_research_packages(self.rows,archive_sha256=self.sha,unavailable={gap})
        self.assertTrue(all(gap not in p["dates"] for p in ps))
        for p in ps:
            indexes=[next(i for i,r in enumerate(self.rows[:-1]) if r["date"]==d) for d in p["dates"]]
            self.assertEqual(indexes,list(range(indexes[0],indexes[-1]+1)))

    def test_legacy_memory_ranges_are_conservatively_unavailable(self):
        memory={"entries":[{"discovery_window":{"start_date":self.rows[1]["date"],"end_date":self.rows[10]["date"],"draw_count":10}}]}
        self.assertEqual(rw.legacy_discovery_dates(self.rows,memory),{r["date"] for r in self.rows[1:11]})

    def test_legacy_missing_or_unreconstructable_provenance_fails_closed(self):
        with self.assertRaises(rw.ResearchWindowError):
            rw.legacy_discovery_dates(self.rows,{"entries":[{}]})
        with self.assertRaises(rw.ResearchWindowError):
            rw.legacy_discovery_dates(self.rows,{"entries":[{"discovery_window":{"start_date":"2010-01-01","end_date":"2010-02-01","draw_count":4}}]})

    def test_package_hash_archive_and_unavailable_dates_are_verified(self):
        p=rw.build_research_packages(self.rows,archive_sha256=self.sha,unavailable=set())[0]
        rw.validate_package(p,self.rows,archive_sha256=self.sha,unavailable=set(p["previously_unavailable_dates"]))
        with self.assertRaises(rw.ResearchWindowError):
            rw.validate_package(p,self.rows,archive_sha256=hashlib.sha256(b"other").hexdigest(),unavailable=set())
        with self.assertRaises(rw.ResearchWindowError):
            rw.validate_package(p,self.rows,archive_sha256=self.sha,unavailable=set(p["dates"]))

    def test_claimed_package_reservation_blocks_other_packages(self):
        ledger={"schema_version":1,"purpose":"test","entries":[],"discovery_exposures":[]}
        p=rw.build_research_packages(self.rows,archive_sha256=self.sha,unavailable=set())[0]
        attempt=hashlib.sha256(b"attempt").hexdigest()
        exposure=rw.reserve_research_package(ledger,p,attempt_id=attempt,created_at_utc="2026-11-27T10:00:00+00:00",unavailable=set(p.get("previously_unavailable_dates", [])))
        rw.append_package_status(ledger,exposure,state="CLAIMED",recorded_at_utc="2026-11-27T10:01:00+00:00",reason="claim")
        self.assertTrue(set(p["dates"]).issubset(rw.exposure_dates(ledger)))

    def test_exposed_package_keeps_discovery_but_releases_unclaimed_holdouts(self):
        ledger={"schema_version":1,"purpose":"test","entries":[],"discovery_exposures":[]}
        p=rw.build_research_packages(self.rows,archive_sha256=self.sha,unavailable=set())[0]
        exposure=rw.reserve_research_package(ledger,p,attempt_id=hashlib.sha256(b"a").hexdigest(),created_at_utc="2026-11-27T10:00:00+00:00",unavailable=set(p.get("previously_unavailable_dates", [])))
        rw.append_package_status(ledger,exposure,state="EXPOSED",recorded_at_utc="2026-11-27T10:01:00+00:00",reason="model saw discovery")
        used=rw.exposure_dates(ledger)
        self.assertEqual(used,set(p["discovery_dates"]))
        self.assertFalse(set(p["validation_dates"]+p["reverse_validation_dates"]) & used)

    def test_split_has_only_package_roles_and_hides_target(self):
        p=rw.build_research_packages(self.rows,archive_sha256=self.sha,unavailable=set())[0]
        split=rw.split_from_package(self.rows,p,archive_sha256=self.sha)
        r60=split["regimes"]["R60"]
        self.assertEqual([r["date"] for r in r60["discovery_rows"]],p["discovery_dates"])
        self.assertEqual([r["date"] for r in r60["validation_rows"]],p["validation_dates"])
        self.assertEqual([r["date"] for r in r60["reverse_validation_rows"]],p["reverse_validation_dates"])
        self.assertFalse(split["target_numbers_exposed"])
        self.assertNotIn(self.rows[-1]["date"],[r["date"] for r in split["discovery_rows"]])

    def test_no_provider_request_releases_all_package_dates_for_safe_reuse(self):
        ledger={"schema_version":1,"purpose":"test","entries":[],"discovery_exposures":[]}
        package=rw.build_research_packages(self.rows,archive_sha256=self.sha,unavailable=set())[0]
        first=rw.reserve_research_package(
            ledger,package,attempt_id=hashlib.sha256(b"first").hexdigest(),
            created_at_utc="2026-11-27T10:00:00+00:00",
            unavailable=set(package.get("previously_unavailable_dates", [])))
        rw.append_package_status(
            ledger,first,state="RELEASED",recorded_at_utc="2026-11-27T10:01:00+00:00",
            reason="No provider request was made")
        self.assertEqual(rw.exposure_dates(ledger),set())
        second_package=rw.build_research_packages(
            self.rows,archive_sha256=self.sha,unavailable=rw.exposure_dates(ledger),
            sequence_start=2)[0]
        self.assertEqual(second_package["dates"],package["dates"])
        self.assertNotEqual(second_package["package_id"],package["package_id"])
        second=rw.reserve_research_package(
            ledger,second_package,attempt_id=hashlib.sha256(b"second").hexdigest(),
            created_at_utc="2026-11-27T10:02:00+00:00",
            unavailable=set(second_package.get("previously_unavailable_dates", [])))
        self.assertEqual(rw.package_status(ledger,second),"RESERVED")

    def test_pre_scope_records_are_rejected(self):
        rows=archive(60)
        rows[0]["date"]="2020-08-01"
        with self.assertRaises(rw.ResearchWindowError):
            rw.build_research_packages(rows,archive_sha256=self.sha,unavailable=set())

if __name__=="__main__":
    unittest.main(verbosity=2)

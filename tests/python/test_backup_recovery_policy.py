from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.tools import check_backup_recovery_policy as policy_check

ROOT = Path(__file__).resolve().parents[2]
BASE_POLICY = ROOT / "deploy/operations/backup-recovery-policy.example.json"
BASE_PROFILE = ROOT / "env/redis/redis.production-validation.conf"


class BackupRecoveryPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy_path = self.root / "policy.json"
        self.profile_path = self.root / "redis.conf"
        self.profile_path.write_bytes(BASE_PROFILE.read_bytes())
        self.policy = json.loads(BASE_POLICY.read_text(encoding="utf-8"))
        self._write_policy()

    def _write_policy(self) -> None:
        self.policy_path.write_text(
            json.dumps(self.policy, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _sync_profile_digest(self) -> None:
        self.policy["redis"]["profile_sha256"] = hashlib.sha256(
            self.profile_path.read_bytes()
        ).hexdigest()
        self._write_policy()

    def _validate(self) -> dict[str, object]:
        return policy_check.validate_policy(
            self.policy_path,
            self.profile_path,
            repository_root=self.root,
        )

    def _failed_names(self) -> set[str]:
        summary = self._validate()
        return {
            str(check["name"]) for check in summary["checks"] if not check["passed"]
        }

    def _replace_profile(
        self, before: str, after: str, *, sync_digest: bool = True
    ) -> None:
        content = self.profile_path.read_text(encoding="utf-8")
        self.assertIn(before, content)
        self.profile_path.write_text(content.replace(before, after), encoding="utf-8")
        if sync_digest:
            self._sync_profile_digest()

    def test_accepts_governed_candidate_without_claiming_host_activation(self) -> None:
        summary = self._validate()

        self.assertTrue(summary["overall_pass"])
        self.assertTrue(summary["candidate_contract_valid"])
        self.assertTrue(summary["governed_candidate_ready"])
        self.assertFalse(summary["activation_ready"])
        self.assertFalse(summary["formal_todo0012_claim"])
        self.assertFalse(summary["live_policy_changed"])
        self.assertFalse(summary["secret_material_recorded"])

    def test_approved_candidate_profile_is_mounted_by_production_compose(self) -> None:
        compose = (ROOT / "deploy/operations/docker-compose.production.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("redis.production-validation.conf", compose)
        self.assertTrue(self.policy["activation"]["production_compose_mount_enabled"])
        self.assertTrue(self.policy["activation"]["host_units_install_enabled"])

    def test_rejects_aof_or_fsync_policy_drift(self) -> None:
        for before, after, failure in (
            ("appendonly yes", "appendonly no", "redis:directive:appendonly"),
            (
                "appendfsync everysec",
                "appendfsync always",
                "redis:directive:appendfsync",
            ),
            (
                "no-appendfsync-on-rewrite no",
                "no-appendfsync-on-rewrite yes",
                "redis:directive:no-appendfsync-on-rewrite",
            ),
            (
                "aof-load-truncated no",
                "aof-load-truncated yes",
                "redis:directive:aof-load-truncated",
            ),
        ):
            with self.subTest(after=after):
                original = self.profile_path.read_text(encoding="utf-8")
                self._replace_profile(before, after)
                self.assertIn(failure, self._failed_names())
                self.profile_path.write_text(original, encoding="utf-8")
                self._sync_profile_digest()

    def test_rejects_duplicate_directive_and_rdb_drift(self) -> None:
        self.profile_path.write_text(
            self.profile_path.read_text(encoding="utf-8")
            + "appendonly yes\n"
            + "save 900 1\n",
            encoding="utf-8",
        )
        self._sync_profile_digest()

        failed = self._failed_names()

        self.assertIn("redis:directive:appendonly", failed)
        self.assertIn("redis:rdb-save-rules", failed)

    def test_rejects_profile_digest_drift(self) -> None:
        self._replace_profile("maxmemory 256mb", "maxmemory 257mb", sync_digest=False)

        self.assertIn("redis:profile-sha256", self._failed_names())

    def test_rejects_silent_eviction(self) -> None:
        self._replace_profile(
            "maxmemory-policy noeviction", "maxmemory-policy allkeys-lru"
        )

        self.assertIn("redis:directive:maxmemory-policy", self._failed_names())

    def test_rejects_relaxed_rpo_and_rto(self) -> None:
        self.policy["objectives"]["redis_rpo_seconds"] = 61
        self.policy["objectives"]["rto_seconds"]["gateway"] = 301
        self.policy["objectives"]["rto_seconds"]["redis_restore"] = 601
        self._write_policy()

        failed = self._failed_names()

        self.assertIn("objectives:redis-rpo", failed)
        self.assertIn("objectives:rto", failed)

    def test_rejects_local_or_unproven_off_host_target(self) -> None:
        off_host = self.policy["backup"]["off_host"]
        off_host["destination"] = "ssh://backup@127.0.0.1/srv/boost-gateway"
        off_host["require_distinct_host_identity"] = False
        off_host["require_remote_readback_sha256"] = False
        self._write_policy()

        failed = self._failed_names()

        self.assertIn("backup:off-host-destination", failed)
        self.assertIn("backup:off-host-proof", failed)

    def test_rejects_inline_secret_and_unencrypted_transfer(self) -> None:
        encryption = self.policy["backup"]["encryption"]
        encryption["recipient"] = "age1inline-secret-like-value"
        encryption["encrypt_before_transfer"] = False
        self._write_policy()

        summary = self._validate()
        rendered = json.dumps(summary)
        failed = {
            str(check["name"]) for check in summary["checks"] if not check["passed"]
        }

        self.assertIn("backup:encryption", failed)
        self.assertIn("backup:no-inline-secret-fields", failed)
        self.assertNotIn("age1inline-secret-like-value", rendered)

    def test_rejects_incomplete_sources_and_unsafe_retention(self) -> None:
        self.policy["backup"]["source_contracts"][0]["path"] = "/tmp/redis"
        retention = self.policy["backup"]["retention"]
        retention["minimum_known_good_copies"] = 1
        retention["delete_only_after_verified_remote_copy"] = False
        self._write_policy()

        failed = self._failed_names()

        self.assertIn("backup:source:redis_snapshot", failed)
        self.assertIn("backup:retention-counts", failed)
        self.assertIn("backup:retention-deletion-guards", failed)

    def test_requires_release_state_and_link_free_archive_contract(self) -> None:
        self.policy["backup"]["source_contracts"] = [
            source
            for source in self.policy["backup"]["source_contracts"]
            if source["id"] != "release_state"
        ]
        archive = self.policy["backup"]["archive_contract"]
        archive["symbolic_link_entries_allowed"] = True
        archive["manifest_link_fields"].remove("target_source_id")
        self._write_policy()

        failed = self._failed_names()

        self.assertIn("backup:sources-unique-complete", failed)
        self.assertIn("backup:source:release_state", failed)
        self.assertIn("backup:link-free-archive", failed)
        self.assertIn("backup:validated-link-metadata", failed)

    def test_restore_must_rebuild_links_from_validated_mapping(self) -> None:
        reconstruction = self.policy["restore"]["link_reconstruction"]
        reconstruction["trust_original_link_text"] = True
        reconstruction["target_source_id_required"] = False
        self.policy["evidence"]["bind_backup_policy_sha256"] = False
        self._write_policy()

        failed = self._failed_names()

        self.assertIn("restore:validated-link-reconstruction", failed)
        self.assertIn("evidence:bindings", failed)

    def test_rejects_incomplete_performance_and_restore_proof(self) -> None:
        performance = self.policy["redis"]["performance_impact"]
        performance["minimum_repetitions_per_mode"] = 1
        performance["required_metrics"].remove("redis_aof_delayed_fsync")
        restore = self.policy["restore"]
        restore["minimum_independent_drills"] = 1
        restore["required_business_checks"].remove("leaderboard_seed_exact")
        self._write_policy()

        failed = self._failed_names()

        self.assertIn("performance:repetitions", failed)
        self.assertIn("performance:metrics", failed)
        self.assertIn("restore:independent-drills", failed)
        self.assertIn("restore:business-verification", failed)

    def test_malformed_optional_contracts_fail_closed_without_crashing(self) -> None:
        self.policy["backup"]["off_host"]["destination"] = "ssh://[invalid"
        self.policy["backup"]["retention"].pop("minimum_known_good_copies")
        self._write_policy()

        failed = self._failed_names()

        self.assertIn("backup:off-host-destination", failed)
        self.assertIn("backup:retention-counts", failed)

    def test_rejects_symlink_policy_and_profile(self) -> None:
        policy_link = self.root / "policy-link.json"
        policy_link.symlink_to(self.policy_path)
        summary = policy_check.validate_policy(policy_link, self.profile_path)
        self.assertFalse(summary["overall_pass"])
        self.assertEqual("policy:load", summary["failed_step"])

        profile_link = self.root / "redis-link.conf"
        profile_link.symlink_to(self.profile_path)
        summary = policy_check.validate_policy(self.policy_path, profile_link)
        self.assertFalse(summary["overall_pass"])
        self.assertEqual("redis:profile-load", summary["failed_step"])


if __name__ == "__main__":
    unittest.main()

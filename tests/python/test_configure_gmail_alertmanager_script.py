from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/operations/configure_gmail_alertmanager.sh"


class ConfigureGmailAlertmanagerScriptTest(unittest.TestCase):
    def test_password_is_hidden_and_stored_outside_config(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("read -r -s", text)
        self.assertIn(
            "smtp_auth_password_file: /etc/alertmanager/secrets/gmail-app-password",
            text,
        )
        self.assertNotIn("smtp_auth_password:", text)
        self.assertIn('chmod 0640 "${PASSWORD_TEMP}"', text)
        self.assertNotIn("set -x", text)

    def test_drill_requires_both_email_states_and_runs_preflight(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("FIRING email Message-ID", text)
        self.assertIn("RESOLVED email Message-ID", text)
        self.assertIn('"overall_pass": True', text)
        self.assertIn("check_observability_preflight.py", text)
        self.assertIn("--pull never", text)


if __name__ == "__main__":
    unittest.main()

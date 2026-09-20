import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from fastapi import HTTPException

from app.routers.auth import forgot_password
from app.schemas.auth import ForgotPasswordRequest
from app.services.email import EmailDeliveryError, send_email


class PasswordResetDeliveryTests(unittest.TestCase):
    def test_resend_uses_https_when_configured(self):
        with patch("app.services.email.settings") as settings, patch("app.services.email.request.urlopen") as urlopen:
            settings.APPS_SCRIPT_MAIL_URL = ""
            settings.APPS_SCRIPT_MAIL_SECRET = ""
            settings.RESEND_API_KEY = "test-key"
            settings.EMAIL_FROM = "NukeNER Review <review@example.com>"
            settings.SMTP_FROM = ""
            send_email("user@example.com", "Reset your password", "Reset link")

        email_request = urlopen.call_args.args[0]
        self.assertEqual(email_request.full_url, "https://api.resend.com/emails")
        self.assertEqual(email_request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(json.loads(email_request.data), {
            "from": "NukeNER Review <review@example.com>",
            "to": ["user@example.com"],
            "subject": "Reset your password",
            "text": "Reset link",
        })

    def test_apps_script_sends_over_https_and_checks_success(self):
        with patch("app.services.email.settings") as settings, patch("app.services.email.request.urlopen") as urlopen:
            settings.APPS_SCRIPT_MAIL_URL = "https://script.google.com/macros/s/test/exec"
            settings.APPS_SCRIPT_MAIL_SECRET = "private-test-secret"
            settings.RESEND_API_KEY = ""
            settings.EMAIL_FROM = ""
            settings.SMTP_FROM = ""
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'

            send_email("user@example.com", "Reset your password", "Reset link")

        email_request = urlopen.call_args.args[0]
        self.assertEqual(email_request.full_url, "https://script.google.com/macros/s/test/exec")
        self.assertEqual(json.loads(email_request.data), {
            "secret": "private-test-secret",
            "to": "user@example.com",
            "subject": "Reset your password",
            "body": "Reset link",
        })

    def test_apps_script_rejection_does_not_report_success(self):
        with patch("app.services.email.settings") as settings, patch("app.services.email.request.urlopen") as urlopen:
            settings.APPS_SCRIPT_MAIL_URL = "https://script.google.com/macros/s/test/exec"
            settings.APPS_SCRIPT_MAIL_SECRET = "private-test-secret"
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": false, "error": "Unauthorized"}'

            with self.assertRaises(EmailDeliveryError):
                send_email("user@example.com", "Reset your password", "Reset link")

    def setUp(self):
        self.payload = ForgotPasswordRequest(email="user@example.com")
        self.db = Mock()
        self.user = SimpleNamespace(id=uuid.uuid4(), email="user@example.com")
        self.db.query.return_value.filter.return_value.first.side_effect = [self.user, None]

    @patch("app.routers.auth.email_is_configured", return_value=False)
    def test_unconfigured_mail_returns_error_before_looking_up_account(self, _configured):
        with patch("app.routers.auth.logger.error"):
            with self.assertRaises(HTTPException) as raised:
                forgot_password(self.payload, self.db)
        self.assertEqual(raised.exception.status_code, 503)
        self.db.query.assert_not_called()

    @patch("app.routers.auth.send_email")
    @patch("app.routers.auth.email_is_configured", return_value=True)
    def test_unknown_account_keeps_generic_response_without_sending(self, _configured, send_email):
        self.db.query.return_value.filter.return_value.first.side_effect = None
        self.db.query.return_value.filter.return_value.first.return_value = None
        result = forgot_password(self.payload, self.db)
        self.assertIn("If this email has an account", result["message"])
        send_email.assert_not_called()
        self.db.commit.assert_not_called()

    @patch("app.routers.auth.send_email")
    @patch("app.routers.auth.email_is_configured", return_value=True)
    def test_success_saves_token_and_sends_link(self, _configured, send_email):
        result = forgot_password(self.payload, self.db)
        self.assertIn("reset link has been sent", result["message"])
        self.assertEqual(self.db.commit.call_count, 1)
        self.db.delete.assert_not_called()
        self.assertEqual(send_email.call_args.args[0], self.user.email)
        self.assertIn("/?reset=", send_email.call_args.args[2])

    @patch("app.routers.auth.send_email", side_effect=EmailDeliveryError("SMTP unavailable"))
    @patch("app.routers.auth.email_is_configured", return_value=True)
    def test_failed_delivery_removes_token_and_allows_retry(self, _configured, _send_email):
        with patch("app.routers.auth.logger.exception"):
            with self.assertRaises(HTTPException) as raised:
                forgot_password(self.payload, self.db)
        self.assertEqual(raised.exception.status_code, 503)
        self.db.delete.assert_called_once_with(self.db.add.call_args.args[0])
        self.assertEqual(self.db.commit.call_count, 2)


if __name__ == "__main__":
    unittest.main()

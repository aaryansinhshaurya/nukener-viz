import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException, Response

from app.models.project import MemberStatus, ProjectRole
from app.routers.projects import (accept_invitation, create_share_link, generate_invitation_link,
                                  preview_invitation, revoke_invitation)
from app.schemas.project import InvitationAccept, InviteRequest
from app.services.tokens import token_digest


class InvitationLinkTests(unittest.TestCase):
    def setUp(self):
        self.project_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.invitation_id = uuid.uuid4()
        self.db = Mock()
        self.db.get.return_value = SimpleNamespace(name="Fusion Research", deleted_at=None)
        self.owner = SimpleNamespace(user_id=self.user_id)

    def pending_invitation(self):
        return SimpleNamespace(
            id=self.invitation_id, project_id=self.project_id,
            email="colleague@example.com", role=ProjectRole.REVIEWER,
            token_hash=token_digest("old-link"), invited_by=self.user_id,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            accepted_at=None, revoked_at=None,
        )

    def test_create_share_link_does_not_require_email_delivery(self):
        self.db.query.return_value.filter.return_value.first.return_value = None
        self.db.query.return_value.filter_by.return_value.first.return_value = None
        self.db.commit.side_effect = lambda: setattr(self.db.add.call_args.args[0], "id", self.invitation_id)
        response = Response()

        with patch("app.routers.projects.settings") as settings:
            settings.FRONTEND_URL = "https://review.example.com"
            result = create_share_link(
                self.project_id,
                InviteRequest(email="Colleague@example.com", role=ProjectRole.REVIEWER),
                response,
                self.owner,
                self.db,
            )

        self.assertEqual(result.email, "colleague@example.com")
        self.assertEqual(result.role, ProjectRole.REVIEWER)
        self.assertTrue(result.url.startswith("https://review.example.com/?invite="))
        self.assertEqual(self.db.add.call_args.args[0].token_hash, token_digest(result.url.split("?invite=")[1]))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_saved_invitation_gets_fresh_link_without_new_record(self):
        row = self.pending_invitation()
        self.db.query.return_value.filter_by.return_value.with_for_update.return_value.first.return_value = row
        response = Response()
        old_hash = row.token_hash

        with patch("app.routers.projects.settings") as settings:
            settings.FRONTEND_URL = "https://review.example.com"
            result = generate_invitation_link(self.project_id, row.id, response, self.owner, self.db)

        self.assertEqual(result.id, row.id)
        self.assertEqual(result.email, row.email)
        self.assertEqual(result.role, row.role)
        self.assertNotEqual(row.token_hash, old_hash)
        self.assertEqual(row.token_hash, token_digest(result.url.split("?invite=")[1]))
        self.assertGreater(row.expires_at, datetime.now(timezone.utc) + timedelta(days=6))
        self.db.add.assert_not_called()
        self.db.commit.assert_called_once()
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_preview_shows_project_and_role_before_acceptance(self):
        row = self.pending_invitation()
        row.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        self.db.query.return_value.filter_by.return_value.first.return_value = row

        result = preview_invitation("old-link", Response(), self.db)

        self.assertEqual(result.project_name, "Fusion Research")
        self.assertEqual(result.email, row.email)
        self.assertEqual(result.role, ProjectRole.REVIEWER)

    def test_revoked_link_cannot_be_previewed_or_accepted(self):
        row = self.pending_invitation()
        row.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        self.db.query.return_value.filter_by.return_value.first.return_value = row
        self.db.query.return_value.filter_by.return_value.with_for_update.return_value.first.return_value = row

        revoke_invitation(self.project_id, row.id, self.owner, self.db)
        self.assertIsNotNone(row.revoked_at)
        self.db.commit.assert_called_once()

        with self.assertRaises(HTTPException) as preview_error:
            preview_invitation("old-link", Response(), self.db)
        with self.assertRaises(HTTPException) as accept_error:
            accept_invitation(InvitationAccept(token="old-link"), self.user(), self.db)
        self.assertEqual(preview_error.exception.status_code, 400)
        self.assertEqual(accept_error.exception.status_code, 400)
        self.db.add.assert_not_called()

    def user(self, email="colleague@example.com"):
        return SimpleNamespace(id=uuid.uuid4(), email=email, name="Colleague")

    def test_acceptance_requires_invited_email_and_works_once(self):
        row = self.pending_invitation()
        row.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        self.db.query.return_value.filter_by.return_value.with_for_update.return_value.first.return_value = row

        with self.assertRaises(HTTPException) as wrong_account:
            accept_invitation(InvitationAccept(token="old-link"), self.user("someone@example.com"), self.db)
        self.assertEqual(wrong_account.exception.status_code, 403)
        self.db.add.assert_not_called()

        self.db.refresh.side_effect = lambda member: (setattr(member, "id", uuid.uuid4()),
                                                       setattr(member, "status", MemberStatus.ACCEPTED))
        result = accept_invitation(InvitationAccept(token="old-link"), self.user(), self.db)
        member = self.db.add.call_args.args[0]
        self.assertEqual(member.project_id, self.project_id)
        self.assertEqual(member.role, ProjectRole.REVIEWER)
        self.assertEqual(result.role, ProjectRole.REVIEWER)
        self.assertIsNotNone(row.accepted_at)

        with self.assertRaises(HTTPException) as reused:
            accept_invitation(InvitationAccept(token="old-link"), self.user(), self.db)
        self.assertEqual(reused.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()

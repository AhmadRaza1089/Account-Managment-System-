"""Tests for authentication and access control."""

import pytest

from account_manager import auth, db, services
from account_manager.errors import NotFound, PermissionDenied
from account_manager.models import Role
from account_manager.security import (
    WeakPassword,
    hash_password,
    hash_token,
    new_token,
    verify_password,
)

PASSWORD = "correct-horse-battery"


# ------------------------------------------------------------------ hashing


def test_a_password_hash_does_not_contain_the_password():
    stored = hash_password(PASSWORD)
    assert PASSWORD not in stored
    assert stored.startswith("scrypt$")


def test_the_right_password_verifies():
    assert verify_password(PASSWORD, hash_password(PASSWORD))


def test_the_wrong_password_does_not():
    assert not verify_password("something else", hash_password(PASSWORD))


def test_the_same_password_hashes_differently_each_time():
    """A random salt, so identical passwords don't look identical in the
    database and can't be cracked in bulk."""
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


@pytest.mark.parametrize("bad", ["", "short", "1234567"])
def test_short_passwords_are_refused(bad):
    with pytest.raises(WeakPassword):
        hash_password(bad)


@pytest.mark.parametrize("junk", ["", "not-a-hash", "scrypt$broken", "$$$$$"])
def test_a_corrupt_stored_hash_never_verifies(junk):
    assert not verify_password(PASSWORD, junk)


def test_tokens_are_unique_and_stored_only_as_hashes():
    token = new_token()
    assert new_token() != token
    assert hash_token(token) != token
    assert len(hash_token(token)) == 64


# ------------------------------------------------------------------- users


def test_creating_and_finding_a_user(session):
    auth.create_user(session, "ahmed", PASSWORD)
    assert auth.get_user(session, "ahmed").username == "ahmed"


def test_usernames_are_case_insensitive(session):
    auth.create_user(session, "Ahmed", PASSWORD)
    assert auth.get_user(session, "AHMED").username == "ahmed"


def test_duplicate_usernames_are_refused(session):
    auth.create_user(session, "ahmed", PASSWORD)
    with pytest.raises(PermissionDenied):
        auth.create_user(session, "ahmed", PASSWORD)


def test_authenticate_accepts_the_right_password(session):
    auth.create_user(session, "ahmed", PASSWORD)
    assert auth.authenticate(session, "ahmed", PASSWORD).username == "ahmed"


def test_authenticate_rejects_the_wrong_password(session):
    auth.create_user(session, "ahmed", PASSWORD)
    with pytest.raises(auth.AuthenticationError):
        auth.authenticate(session, "ahmed", "wrong")


def test_a_missing_user_and_a_wrong_password_look_the_same(session):
    """Otherwise this could be used to find out who has an account."""
    auth.create_user(session, "ahmed", PASSWORD)
    with pytest.raises(auth.AuthenticationError) as wrong_password:
        auth.authenticate(session, "ahmed", "wrong")
    with pytest.raises(auth.AuthenticationError) as no_such_user:
        auth.authenticate(session, "nobody", "wrong")
    assert str(wrong_password.value) == str(no_such_user.value)


def test_a_disabled_account_cannot_log_in(session):
    user = auth.create_user(session, "ahmed", PASSWORD)
    auth.set_active(session, user, active=False)
    with pytest.raises(auth.AuthenticationError, match="disabled"):
        auth.authenticate(session, "ahmed", PASSWORD)


# ------------------------------------------------------------------ tokens


def test_a_token_resolves_to_its_user(session):
    user = auth.create_user(session, "ahmed", PASSWORD)
    token = auth.issue_token(session, user)
    assert auth.resolve_token(session, token).id == user.id


def test_an_unknown_token_is_refused(session):
    with pytest.raises(auth.AuthenticationError, match="not valid"):
        auth.resolve_token(session, "made-up")


def test_no_token_at_all_explains_how_to_log_in(session):
    with pytest.raises(auth.AuthenticationError, match="login"):
        auth.resolve_token(session, "")


def test_a_revoked_token_stops_working(session):
    user = auth.create_user(session, "ahmed", PASSWORD)
    token = auth.issue_token(session, user)
    auth.revoke_token(session, token)
    with pytest.raises(auth.AuthenticationError):
        auth.resolve_token(session, token)


def test_changing_a_password_ends_existing_sessions(session):
    """A password change should log out anyone using the old one."""
    user = auth.create_user(session, "ahmed", PASSWORD)
    token = auth.issue_token(session, user)
    auth.set_password(session, user, "a-brand-new-password")
    with pytest.raises(auth.AuthenticationError):
        auth.resolve_token(session, token)


def test_disabling_an_account_ends_its_sessions(session):
    user = auth.create_user(session, "ahmed", PASSWORD)
    token = auth.issue_token(session, user)
    auth.set_active(session, user, active=False)
    with pytest.raises(auth.AuthenticationError):
        auth.resolve_token(session, token)


# ---------------------------------------------------------- access control


@pytest.fixture
def people(session):
    boss = auth.create_user(session, "boss", PASSWORD, is_superuser=True)
    manager = auth.create_user(session, "manager", PASSWORD)
    clerk = auth.create_user(session, "clerk", PASSWORD)
    outsider = auth.create_user(session, "outsider", PASSWORD)
    company = services.create_company(session, "Acme", "Ahmed")

    boss_actor = auth.actor_for(session, boss, company.id)
    auth.add_member(session, company.id, manager, Role.ADMIN, actor=boss_actor)
    auth.add_member(session, company.id, clerk, Role.REGULAR_USER, actor=boss_actor)
    return {
        "boss": boss,
        "manager": manager,
        "clerk": clerk,
        "outsider": outsider,
        "company": company,
    }


def test_a_superuser_is_an_admin_everywhere(session, people):
    actor = auth.actor_for(session, people["boss"], people["company"].id)
    assert actor.is_admin


def test_a_members_role_comes_from_their_membership(session, people):
    assert auth.actor_for(session, people["manager"], people["company"].id).is_admin
    assert not auth.actor_for(session, people["clerk"], people["company"].id).is_admin


def test_a_non_member_gets_not_found_rather_than_forbidden(session, people):
    """Saying 'forbidden' would confirm the company exists."""
    with pytest.raises(NotFound):
        auth.actor_for(session, people["outsider"], people["company"].id)


def test_users_only_see_companies_they_belong_to(session, people):
    assert [c.id for c in auth.visible_companies(session, people["clerk"])] == [
        people["company"].id
    ]
    assert auth.visible_companies(session, people["outsider"]) == []


def test_a_superuser_sees_every_company(session, people):
    services.create_company(session, "Other Co", "Someone")
    assert len(auth.visible_companies(session, people["boss"])) == 2


def test_a_regular_member_cannot_grant_access(session, people):
    clerk_actor = auth.actor_for(session, people["clerk"], people["company"].id)
    with pytest.raises(PermissionDenied):
        auth.add_member(
            session,
            people["company"].id,
            people["outsider"],
            Role.ADMIN,
            actor=clerk_actor,
        )


def test_membership_can_be_revoked(session, people):
    boss_actor = auth.actor_for(session, people["boss"], people["company"].id)
    auth.remove_member(session, people["company"].id, people["clerk"], actor=boss_actor)
    with pytest.raises(NotFound):
        auth.actor_for(session, people["clerk"], people["company"].id)


def test_re_adding_a_member_changes_their_role(session, people):
    boss_actor = auth.actor_for(session, people["boss"], people["company"].id)
    auth.add_member(
        session, people["company"].id, people["clerk"], Role.ADMIN, actor=boss_actor
    )
    assert auth.actor_for(session, people["clerk"], people["company"].id).is_admin


def test_creating_a_company_makes_you_its_admin(session):
    user = auth.create_user(session, "solo", PASSWORD)
    company = services.create_company(session, "Solo Ltd", "Solo", creator=user)
    assert auth.actor_for(session, user, company.id).is_admin


# ------------------------------------------------------- credentials on disk


def test_credentials_round_trip(database):
    auth.save_credentials("ahmed", "a-token")
    assert (auth.load_credentials() or {})["token"] == "a-token"
    assert auth.current_token() == "a-token"

    auth.clear_credentials()
    assert auth.load_credentials() is None


def test_the_credentials_file_is_private(database):
    path = auth.save_credentials("ahmed", "a-token")
    assert path.stat().st_mode & 0o077 == 0, "must not be readable by other users"


def test_the_environment_wins_over_the_saved_login(database, monkeypatch):
    auth.save_credentials("ahmed", "from-file")
    monkeypatch.setenv(auth.TOKEN_ENV_VAR, "from-env")
    assert auth.current_token() == "from-env"


def test_a_corrupt_credentials_file_is_ignored(database):
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")
    assert auth.load_credentials() is None


def test_current_user_resolves_the_saved_login(superuser):
    with db.session_scope() as session:
        assert auth.current_user(session).username == superuser["username"]

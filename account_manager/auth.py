"""Authentication and access control.

Two things live here: turning a credential into a User, and turning a User
plus a company into the Actor the service layer trusts. Nothing else may
build an Actor — that is the whole point of this module.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .errors import NotFound, PermissionDenied
from .models import Actor, ApiToken, Company, CompanyMember, Role, User, utcnow
from .security import hash_password, hash_token, new_token, verify_password

logger = logging.getLogger(__name__)

TOKEN_ENV_VAR = "ACCOUNT_MANAGER_TOKEN"


class AuthenticationError(PermissionDenied):
    """The credential was missing, wrong, or belongs to a disabled account."""


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


def create_user(
    session: Session,
    username: str,
    password: str,
    *,
    is_superuser: bool = False,
) -> User:
    username = (username or "").strip().lower()
    if not username:
        raise NotFound("A username is required.")

    existing = session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if existing is not None:
        raise PermissionDenied(f"There is already a user called {username!r}.")

    user = User(
        username=username,
        password_hash=hash_password(password),
        is_superuser=is_superuser,
    )
    session.add(user)
    session.flush()
    logger.info("Created user %s (superuser=%s)", username, is_superuser)
    return user


def get_user(session: Session, username: str) -> User:
    user = session.execute(
        select(User).where(User.username == (username or "").strip().lower())
    ).scalar_one_or_none()
    if user is None:
        raise NotFound(f"No user called {username!r}.")
    return user


def list_users(session: Session) -> list[User]:
    return list(session.execute(select(User).order_by(User.id)).scalars().all())


def count_users(session: Session) -> int:
    return int(session.execute(select(func.count(User.id))).scalar_one())


def set_password(session: Session, user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    # Any session opened with the old password is no longer trusted.
    revoke_all_tokens(session, user)
    session.flush()


def set_active(session: Session, user: User, active: bool) -> None:
    user.is_active = active
    if not active:
        revoke_all_tokens(session, user)
    session.flush()


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


def authenticate(session: Session, username: str, password: str) -> User:
    """Check a username and password.

    The same message is returned whether the user does not exist or the
    password is wrong, so this cannot be used to discover who has an account.
    """
    user = session.execute(
        select(User).where(User.username == (username or "").strip().lower())
    ).scalar_one_or_none()

    # Hash even when there is no such user, so a missing account does not
    # answer noticeably faster than a wrong password.
    stored = user.password_hash if user else hash_password("not-a-real-password")
    if not verify_password(password, stored) or user is None:
        raise AuthenticationError("Incorrect username or password.")
    if not user.is_active:
        raise AuthenticationError(f"The account {user.username!r} is disabled.")
    return user


def issue_token(session: Session, user: User, *, name: str = "cli") -> str:
    """Create a token for a user. The plain value is returned once only."""
    token = new_token()
    session.add(ApiToken(user_id=user.id, token_hash=hash_token(token), name=name))
    session.flush()
    logger.info("Issued token %r for %s", name, user.username)
    return token


def resolve_token(session: Session, token: str) -> User:
    """Find the user a token belongs to."""
    if not token:
        raise AuthenticationError(
            "No credential supplied. Run 'account-manager login', or set "
            f"{TOKEN_ENV_VAR}."
        )
    record = session.execute(
        select(ApiToken).where(ApiToken.token_hash == hash_token(token))
    ).scalar_one_or_none()
    if record is None:
        raise AuthenticationError("That credential is not valid. Log in again.")

    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("The account for that credential is disabled.")

    record.last_used_at = utcnow()
    return user


def revoke_token(session: Session, token: str) -> bool:
    record = session.execute(
        select(ApiToken).where(ApiToken.token_hash == hash_token(token))
    ).scalar_one_or_none()
    if record is None:
        return False
    session.delete(record)
    session.flush()
    return True


def revoke_all_tokens(session: Session, user: User) -> None:
    for record in list(user.tokens):
        session.delete(record)
    session.flush()


# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------


def actor_for(session: Session, user: User, company_id: int) -> Actor:
    """The authority this user has in this company.

    Raises NotFound rather than PermissionDenied for a company they are not
    a member of: telling them it exists but is off limits would reveal that
    other companies are in this install.
    """
    if user.is_superuser:
        return Actor(name=user.username, role=Role.ADMIN, user_id=user.id)

    membership = session.execute(
        select(CompanyMember).where(
            CompanyMember.company_id == company_id,
            CompanyMember.user_id == user.id,
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFound(f"No company with id {company_id}.")
    return Actor(name=user.username, role=membership.role, user_id=user.id)


def visible_companies(session: Session, user: User) -> list[Company]:
    """Only the companies this user belongs to."""
    stmt = select(Company).order_by(Company.id)
    if not user.is_superuser:
        stmt = stmt.join(CompanyMember).where(CompanyMember.user_id == user.id)
    return list(session.execute(stmt).scalars().all())


def add_member(
    session: Session, company_id: int, user: User, role: Role, *, actor: Actor
) -> CompanyMember:
    """Give a user access to a company. Admins of that company only."""
    if not actor.is_admin:
        raise PermissionDenied(
            f"{actor.name} is not an admin of company {company_id}."
        )
    existing = session.execute(
        select(CompanyMember).where(
            CompanyMember.company_id == company_id,
            CompanyMember.user_id == user.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.role = role
        session.flush()
        return existing

    membership = CompanyMember(company_id=company_id, user_id=user.id, role=role)
    session.add(membership)
    session.flush()
    logger.info("Added %s to company %s as %s", user.username, company_id, role.value)
    return membership


def remove_member(session: Session, company_id: int, user: User, *, actor: Actor) -> bool:
    if not actor.is_admin:
        raise PermissionDenied(
            f"{actor.name} is not an admin of company {company_id}."
        )
    membership = session.execute(
        select(CompanyMember).where(
            CompanyMember.company_id == company_id,
            CompanyMember.user_id == user.id,
        )
    ).scalar_one_or_none()
    if membership is None:
        return False
    session.delete(membership)
    session.flush()
    return True


def list_members(session: Session, company_id: int) -> list[CompanyMember]:
    return list(
        session.execute(
            select(CompanyMember).where(CompanyMember.company_id == company_id)
        )
        .scalars()
        .all()
    )


# --------------------------------------------------------------------------
# Where the CLI keeps its token
# --------------------------------------------------------------------------


def credentials_path() -> Path:
    """Follows the XDG convention, so it sits with the user's other config."""
    base = os.environ.get("ACCOUNT_MANAGER_CONFIG_DIR") or os.environ.get(
        "XDG_CONFIG_HOME", str(Path.home() / ".config")
    )
    return Path(base) / "account-manager" / "credentials.json"


def save_credentials(username: str, token: str) -> Path:
    """Write the token readable only by this user."""
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"username": username, "token": token}), encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    return path


def load_credentials() -> dict | None:
    path = credentials_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def clear_credentials() -> None:
    credentials_path().unlink(missing_ok=True)


def current_token() -> str:
    """The token to use: the environment first, then the login file."""
    from_env = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if from_env:
        return from_env
    stored = load_credentials() or {}
    return str(stored.get("token") or "")


def current_user(session: Session) -> User:
    """The logged-in user, or an error explaining how to log in."""
    return resolve_token(session, current_token())

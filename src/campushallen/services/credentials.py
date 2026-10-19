from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from campushallen.database.models import CampushallenAccount, CampushallenCredential

from .audit import Event, audit
from .secrets import CredentialVault


def credential_for(db: Session, account_id: int) -> CampushallenCredential | None:
    return db.scalar(
        select(CampushallenCredential).where(
            CampushallenCredential.campushallen_account_id == account_id
        )
    )


def remember(
    db: Session, vault: CredentialVault, account: CampushallenAccount, password: str
) -> None:
    row = credential_for(db, account.id)
    event = Event.STORED_CREDENTIAL_REPLACED if row else Event.STORED_CREDENTIAL_ENABLED
    if not row:
        row = CampushallenCredential(campushallen_account_id=account.id)
        db.add(row)
    row.encrypted_password = vault.seal(account.id, password)
    row.encryption_version = 1
    audit(db, event, user_id=account.user_id, actor_id=account.user_id, account_id=account.id)


def forget(db: Session, account: CampushallenAccount) -> None:
    db.execute(
        delete(CampushallenCredential).where(
            CampushallenCredential.campushallen_account_id == account.id
        )
    )
    audit(
        db,
        Event.STORED_CREDENTIAL_REMOVED,
        user_id=account.user_id,
        actor_id=account.user_id,
        account_id=account.id,
    )

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.collectors.base import SourceIdentity
from app.models import Module


class ModuleRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create(self, identity: SourceIdentity) -> Module:
        query = select(Module).where(
            Module.source_type == identity.source_type, Module.source_url == identity.source_url
        )
        existing = self.session.scalar(query)
        if existing:
            return existing
        # Savepoint + unique constraint also handle simultaneous submissions of the same URL.
        try:
            with self.session.begin_nested():
                module = Module(
                    name=identity.repository_name,
                    source_type=identity.source_type,
                    source_url=identity.source_url,
                    repository_owner=identity.owner,
                    repository_name=identity.repository_name,
                    provider=identity.provider,
                )
                self.session.add(module)
                self.session.flush()
            return module
        except IntegrityError:
            existing = self.session.scalar(query)
            if existing is None:
                raise
            return existing

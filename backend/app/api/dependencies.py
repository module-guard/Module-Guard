from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Generator[Session, None, None]:
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
# Every list endpoint returns at most 50 rows per page.
Limit = Annotated[int, Query(ge=1, le=50)]
Offset = Annotated[int, Query(ge=0)]

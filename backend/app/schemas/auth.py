from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    login: str
    name: str | None
    avatar_url: str | None


class MeResponse(BaseModel):
    user: UserResponse | None
    # False when the server has no GitHub OAuth configuration; the UI hides the login button.
    login_available: bool

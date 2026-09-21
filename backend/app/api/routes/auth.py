from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentAdmin, CurrentUser, DbSession
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.schemas.auth import Credentials, TokenResponse, UserRead
from app.schemas.agent_config import AdminStatusUpdate

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: Credentials, db: DbSession) -> TokenResponse:
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username is already taken")
    user = User(username=payload.username, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=create_access_token(user.username), user=user)


@router.post("/login", response_model=TokenResponse)
def login(payload: Credentials, db: DbSession) -> TokenResponse:
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    return TokenResponse(access_token=create_access_token(user.username), user=user)


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> User:
    return user


@router.put("/users/{username}/admin", response_model=UserRead)
def set_admin_status(username: str, payload: AdminStatusUpdate, admin: CurrentAdmin, db: DbSession) -> User:
    target = db.query(User).filter(User.username == username).first()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.id == admin.id and not payload.is_admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot remove your own administrator access")
    target.is_admin = payload.is_admin
    db.commit()
    db.refresh(target)
    return target

"""用户相关 schema。"""

import unicodedata
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _sanitize_text(v: str | None) -> str | None:
    """NFKC 规范化 + 拦截 ASCII 控制字符 / BiDi 覆盖符。

    防：① admin\x00 绕过相等比较 ② 全角字符撑爆 bcrypt（1 字符 = 1 长度，但字节 4B，bcrypt O(n²)）
    ③ BiDi 控制字符（U+202E 等）混淆 UI 显示
    """
    if v is None:
        return v
    v = unicodedata.normalize("NFKC", v).strip()
    for ch in v:
        code = ord(ch)
        if (
            code < 0x20
            or code == 0x7F
            or code
            in (
                0x202A,
                0x202B,
                0x202C,
                0x202D,
                0x202E,
                0x2066,
                0x2067,
                0x2068,
                0x2069,
            )
        ):
            raise ValueError(f"包含非法控制字符 (U+{code:04X})")
    return v


class UserBase(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    nickname: str | None = Field(default=None, max_length=50)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("nickname", "email", "phone", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        """空字符串 → None：前端表单未填的可选字段序列化成 ''，EmailStr 不接受 ''。"""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("username", "nickname", "email", "phone")
    @classmethod
    def _norm(cls, v):
        return _sanitize_text(v)


class UserCreate(UserBase):
    password: str = Field(min_length=6, max_length=128)
    role_id: int | None = None
    is_active: bool = True

    @field_validator("password")
    @classmethod
    def _norm_pwd(cls, v: str) -> str:
        # 密码原样过 bcrypt，所以不规范化（保留用户原始密码）；只做控制字符拦截
        for ch in v:
            code = ord(ch)
            if code < 0x20 or code == 0x7F:
                raise ValueError("密码不能包含控制字符")
        return v


class UserUpdate(BaseModel):
    nickname: str | None = Field(default=None, max_length=50)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=20)
    avatar: str | None = None
    role_id: int | None = None
    is_active: bool | None = None

    @field_validator("nickname", "email", "phone", "avatar", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("nickname", "email", "phone")
    @classmethod
    def _norm(cls, v):
        return _sanitize_text(v)


class UserPasswordUpdate(BaseModel):
    """用户自己改密：必须提供旧密码。"""

    old_password: str
    new_password: str = Field(min_length=6, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _norm_pwd(cls, v: str) -> str:
        for ch in v:
            if ord(ch) < 0x20 or ord(ch) == 0x7F:
                raise ValueError("新密码不能包含控制字符")
        return v


class AdminPasswordUpdate(BaseModel):
    """管理员直接改密（不校验旧密码）。

    之前 admin_change_password 共用 UserPasswordUpdate，强制要求 old_password，
    实际管理员改密场景下根本没有"旧密码"语义——admin 是帮用户重置密码。
    强行传占位 old_password 既容易写错又是历史包袱。独立 schema 更清晰。
    """

    new_password: str = Field(min_length=6, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _norm_pwd(cls, v: str) -> str:
        for ch in v:
            if ord(ch) < 0x20 or ord(ch) == 0x7F:
                raise ValueError("新密码不能包含控制字符")
        return v


class UserOut(UserBase):
    id: int
    is_active: bool
    is_superuser: bool
    avatar: str | None
    role_id: int | None
    role_name: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    # 防止 bcrypt O(n²) DoS：min_length=4 足够拒掉 1-3 字符的爆破尝试。
    # 默认 admin 密码 = "admin"（5 字符），用 4 是兼容现有默认；生产必须
    # 用 --admin-pass 指定 ≥ 16 字符强密码，且 _check_prod_secrets() 会
    # 强校验弱密码（main.py._check_prod_secrets 拒绝常见弱密码）。
    password: str = Field(min_length=4, max_length=128)

    @field_validator("username")
    @classmethod
    def _norm(cls, v):
        return _sanitize_text(v)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut

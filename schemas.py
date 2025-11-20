"""
Database Schemas for College Dating App

Each Pydantic model represents a collection in your database.
Collection name is the lowercase of the class name by convention.
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, EmailStr

# Core domain models

class Residence(BaseModel):
    name: str = Field(..., description="Residence name (e.g., Dorm A)")
    campus: Optional[str] = Field(None, description="Campus or school label")


class User(BaseModel):
    email: EmailStr = Field(..., description="Student email")
    first_name: Optional[str] = Field(None)
    last_name: Optional[str] = Field(None)
    is_active: bool = Field(True)
    is_banned: bool = Field(False)
    residence_id: Optional[str] = Field(None, description="Reference to residence _id as string")
    allow_all_residences: bool = Field(False, description="If true, discovery includes all residences")


class Photo(BaseModel):
    url: str = Field(..., description="Public URL for the photo")
    order: int = Field(0, ge=0, le=7, description="Order in gallery (0-7)")


class Profile(BaseModel):
    user_id: str = Field(..., description="Reference to user _id as string")
    bio: Optional[str] = Field(None, max_length=300)
    age: Optional[int] = Field(None, ge=18, le=100)
    pronouns: Optional[str] = Field(None, max_length=30)
    year: Optional[str] = Field(None, description="Academic year (e.g., 2nd year)")
    program: Optional[str] = Field(None, description="Program/major")
    interests: List[str] = Field(default_factory=list)
    photos: List[Photo] = Field(default_factory=list)


class Verification(BaseModel):
    email: EmailStr
    token: str = Field(..., description="Magic link token")
    purpose: Literal["login", "signup"] = "login"
    used: bool = False
    expires_at: int = Field(..., description="Unix timestamp when token expires")


class Swipe(BaseModel):
    swiper_id: str
    target_id: str
    direction: Literal["left", "right"]


class Match(BaseModel):
    user_a: str
    user_b: str


class Message(BaseModel):
    match_id: str
    sender_id: str
    text: str = Field(..., max_length=1000)
    read: bool = False


class Report(BaseModel):
    reporter_id: str
    target_user_id: Optional[str] = None
    target_message_id: Optional[str] = None
    reason: str = Field(..., max_length=300)
    details: Optional[str] = None

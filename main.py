import os
import time
import secrets
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from email_validator import validate_email, EmailNotValidError
from database import db, create_document, get_documents
from schemas import User, Profile, Verification, Swipe, Match, Message, Report, Residence
from bson import ObjectId

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_DOMAINS = {"polito.it", "unito.it"}
MAGIC_TOKEN_TTL = 15 * 60  # 15 minutes
MIN_PHOTOS = 3
MAX_PHOTOS = 8
BANNED_WORDS = {"slur1", "slur2", "badword"}


def is_valid_domain(email: str) -> bool:
    try:
        v = validate_email(email, check_deliverability=False)
        domain = v.domain.lower()
        return any(domain == d or domain.endswith("@" + d) for d in ALLOWED_DOMAINS) or domain in ALLOWED_DOMAINS
    except EmailNotValidError:
        return False


class SendMagicLinkRequest(BaseModel):
    email: EmailStr
    purpose: str = "login"


class VerifyMagicLinkRequest(BaseModel):
    email: EmailStr
    token: str


class CreateProfileRequest(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    residence_id: Optional[str] = None
    allow_all_residences: bool = False
    bio: Optional[str] = None
    age: Optional[int] = None
    pronouns: Optional[str] = None
    year: Optional[str] = None
    program: Optional[str] = None
    interests: List[str] = []
    photos: List[str] = []  # incoming URLs


class SwipeRequest(BaseModel):
    target_id: str
    direction: str  # 'left' or 'right'


class MessageRequest(BaseModel):
    match_id: str
    text: str


@app.get("/")
def root():
    return {"status": "ok", "service": "dating-api"}


@app.post("/auth/magic/send")
def send_magic_link(payload: SendMagicLinkRequest):
    email = payload.email.lower()
    if not is_valid_domain(email):
        raise HTTPException(status_code=400, detail="Email domain not allowed. Use your student email (polito/unito).")

    token = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + MAGIC_TOKEN_TTL

    verification = Verification(email=email, token=token, purpose=payload.purpose, expires_at=expires_at)
    create_document("verification", verification)

    # In a real app: send email with the magic link. For MVP we return token for testing.
    return {"message": "Magic link created", "token": token, "expires_at": expires_at}


@app.post("/auth/magic/verify")
def verify_magic_link(payload: VerifyMagicLinkRequest):
    email = payload.email.lower()
    if not is_valid_domain(email):
        raise HTTPException(status_code=400, detail="Invalid email")

    recs = get_documents("verification", {"email": email, "token": payload.token, "used": False})
    if not recs:
        raise HTTPException(status_code=400, detail="Invalid or used token")

    rec = recs[-1]
    if rec.get("expires_at", 0) < int(time.time()):
        raise HTTPException(status_code=400, detail="Token expired")

    # Upsert user
    existing = db["user"].find_one({"email": email})
    user_id = None
    if existing:
        user_id = str(existing["_id"])
    else:
        user = User(email=email)
        user_id = create_document("user", user)

    # mark token used
    db["verification"].update_one({"_id": rec["_id"]}, {"$set": {"used": True}})

    return {"message": "Verified", "user_id": user_id}


@app.post("/profile")
def create_or_update_profile(user_id: str, payload: CreateProfileRequest):
    # Safety: banned words in bio
    if payload.bio:
        lowered = payload.bio.lower()
        if any(w in lowered for w in BANNED_WORDS):
            raise HTTPException(status_code=400, detail="Bio contains prohibited content")

    if payload.photos is None:
        payload.photos = []

    if len(payload.photos) < MIN_PHOTOS or len(payload.photos) > MAX_PHOTOS:
        raise HTTPException(status_code=400, detail=f"Profiles require between {MIN_PHOTOS} and {MAX_PHOTOS} photos")

    # Ensure user exists
    obj_id = ObjectId(user_id)
    user = db["user"].find_one({"_id": obj_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Update user basic fields
    db["user"].update_one(
        {"_id": obj_id},
        {"$set": {
            "first_name": payload.first_name,
            "last_name": payload.last_name,
            "residence_id": payload.residence_id,
            "allow_all_residences": payload.allow_all_residences,
        }}
    )

    # Build profile
    photos_docs = [{"url": url, "order": idx} for idx, url in enumerate(payload.photos[:MAX_PHOTOS])]
    profile_doc = {
        "user_id": user_id,
        "bio": payload.bio,
        "age": payload.age,
        "pronouns": payload.pronouns,
        "year": payload.year,
        "program": payload.program,
        "interests": payload.interests or [],
        "photos": photos_docs,
        "updated_at": int(time.time())
    }

    existing_profile = db["profile"].find_one({"user_id": user_id})
    if existing_profile:
        db["profile"].update_one({"_id": existing_profile["_id"]}, {"$set": profile_doc})
    else:
        create_document("profile", profile_doc)

    return {"message": "Profile saved"}


@app.get("/discover")
def discover(user_id: str, limit: int = Query(20, ge=1, le=50)):
    # Fetch user and profile
    user = db["user"].find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    my_profile = db["profile"].find_one({"user_id": user_id})
    if not my_profile:
        raise HTTPException(status_code=400, detail="Complete your profile first")

    # filter by residence unless allow_all_residences
    filter_query = {"user_id": {"$ne": user_id}}
    if not user.get("allow_all_residences", False):
        filter_query["residence_id"] = user.get("residence_id")

    # exclude already swiped
    swiped_ids = {s.get("target_id") for s in get_documents("swipe", {"swiper_id": user_id})}

    candidates_cursor = db["profile"].find(filter_query).limit(limit * 2)
    cards: List[dict] = []
    for prof in candidates_cursor:
        if prof.get("user_id") in swiped_ids:
            continue
        u = db["user"].find_one({"_id": ObjectId(prof["user_id"])})
        if not u or u.get("is_banned"):
            continue
        card = {
            "user_id": prof["user_id"],
            "first_name": u.get("first_name"),
            "age": prof.get("age"),
            "bio": prof.get("bio"),
            "residence_id": u.get("residence_id"),
            "photos": prof.get("photos", [])
        }
        cards.append(card)
        if len(cards) >= limit:
            break

    return {"results": cards}


@app.post("/swipe")
def swipe(user_id: str, payload: SwipeRequest):
    if payload.direction not in {"left", "right"}:
        raise HTTPException(status_code=400, detail="Invalid direction")

    # record swipe
    swipe_doc = {"swiper_id": user_id, "target_id": payload.target_id, "direction": payload.direction}
    create_document("swipe", swipe_doc)

    # if right, check for match
    is_match = False
    if payload.direction == "right":
        reciprocal = db["swipe"].find_one({"swiper_id": payload.target_id, "target_id": user_id, "direction": "right"})
        if reciprocal:
            # create match if not exists
            existing = db["match"].find_one({
                "$or": [
                    {"user_a": user_id, "user_b": payload.target_id},
                    {"user_a": payload.target_id, "user_b": user_id}
                ]
            })
            if not existing:
                create_document("match", {"user_a": user_id, "user_b": payload.target_id})
            is_match = True

    return {"message": "swiped", "match": is_match}


@app.get("/matches")
def list_matches(user_id: str):
    matches = list(db["match"].find({"$or": [{"user_a": user_id}, {"user_b": user_id}]}))
    cleaned = []
    for m in matches:
        other_id = m["user_b"] if m["user_a"] == user_id else m["user_a"]
        prof = db["profile"].find_one({"user_id": other_id})
        u = db["user"].find_one({"_id": ObjectId(other_id)})
        cleaned.append({
            "match_id": str(m["_id"]),
            "user_id": other_id,
            "first_name": u.get("first_name"),
            "photos": (prof or {}).get("photos", [])
        })
    return {"results": cleaned}


@app.get("/messages")
def list_messages(user_id: str, match_id: str):
    match = db["match"].find_one({"_id": ObjectId(match_id)})
    if not match or (user_id not in {match["user_a"], match["user_b"]}):
        raise HTTPException(status_code=404, detail="Match not found")
    msgs = list(db["message"].find({"match_id": match_id}))
    for m in msgs:
        m["_id"] = str(m["_id"])
    return {"results": msgs}


@app.post("/messages")
def send_message(user_id: str, payload: MessageRequest):
    match = db["match"].find_one({"_id": ObjectId(payload.match_id)})
    if not match or (user_id not in {match["user_a"], match["user_b"]}):
        raise HTTPException(status_code=404, detail="Match not found")

    message_doc = {"match_id": payload.match_id, "sender_id": user_id, "text": payload.text}
    create_document("message", message_doc)
    return {"message": "sent"}


@app.post("/report")
def report(user_id: str, target_user_id: Optional[str] = None, target_message_id: Optional[str] = None, reason: str = "", details: Optional[str] = None):
    if not reason:
        raise HTTPException(status_code=400, detail="Reason is required")
    create_document("report", {
        "reporter_id": user_id,
        "target_user_id": target_user_id,
        "target_message_id": target_message_id,
        "reason": reason,
        "details": details,
    })
    return {"message": "reported"}


@app.get("/test")
def test_database():
    status = {
        "backend": "running",
        "database": "unavailable",
        "collections": []
    }
    try:
        if db is not None:
            status["database"] = "connected"
            status["collections"] = db.list_collection_names()[:10]
    except Exception as e:
        status["database"] = f"error: {str(e)[:60]}"
    return status


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

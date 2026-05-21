from datetime import datetime
from typing import Optional

from ninja import Schema




class SubmitFeedbackIn(Schema):
    feedback: str
    page: str

class SubmitFeedbackOut(Schema):
    success: bool
    message: str




class ProfileSettingsOut(Schema):
    
    pass
    


class UserSettingsOut(Schema):
    profile: ProfileSettingsOut


class UserProfileOut(Schema):
    id: int
    state: str
    has_active_subscription: bool


class UserInfoOut(Schema):
    id: int
    email: str
    username: str
    first_name: str
    last_name: str
    full_name: str
    date_joined: datetime
    profile: UserProfileOut

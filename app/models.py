from dataclasses import dataclass


@dataclass
class InactiveUser:
    user_id: str
    first_name: str
    phone: str
    last_login: str
    is_new: bool = True
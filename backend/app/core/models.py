from dataclasses import dataclass

# Feature identifiers used to gate and report per-feature work.
WELCOME = "welcome"
INACTIVE = "inactive"


@dataclass
class InactiveUser:
    user_id: str
    first_name: str
    phone: str
    last_login: str
    is_new: bool = True
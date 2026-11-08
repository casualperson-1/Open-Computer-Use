from dataclasses import dataclass
from typing import Optional, Any, Dict

@dataclass
class Action:
    name: str # goto, click, double_click, right_click, hover, type, press_key, scroll, scroll_to, drag, select_option, go_back, new_tab, switch_tab, wait, extract, ask_user, done
    element_id: Optional[int] = None
    x: Optional[float] = None
    y: Optional[float] = None
    text: Optional[str] = None
    submit: bool = False
    keys: Optional[str] = None
    direction: Optional[str] = None # up, down, left, right
    amount: Optional[float] = None
    url: Optional[str] = None
    seconds: Optional[float] = None
    schema_data: Optional[Dict[str, Any]] = None
    question: Optional[str] = None
    summary: Optional[str] = None
    results: Optional[Any] = None
